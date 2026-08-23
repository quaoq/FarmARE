"""Deterministic two-agent D-CORE execution runtime."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from are.simulation.distributed.evaluator import evaluate_dcore
from are.simulation.distributed.guard import CausalGuard
from are.simulation.distributed.knowledge import KnowledgeStore
from are.simulation.distributed.models import (
    AgentIntent,
    CausalHandoff,
    Claim,
    DecisionRecord,
    DistributedRunnerConfig,
    DistributedRunResult,
    EpistemicStatus,
    EventKind,
    FreeTextEnvelope,
    GuardResult,
    GuardVerdict,
    IntentKind,
    KnowledgeItem,
    LocalView,
    stable_digest,
)
from are.simulation.distributed.scheduler import (
    DeterministicScheduler,
    SchedulerPriority,
)
from are.simulation.distributed.trace import CausalTraceRecorder
from are.simulation.distributed.transport import (
    FaultMode,
    FaultRule,
    FaultSchedule,
    InProcessTransport,
)
from are.simulation.scenarios.scenario_dcore.base import DistributedScenarioBundle


def fault_schedule_from_config(config: DistributedRunnerConfig) -> FaultSchedule:
    if config.fault == "none":
        return FaultSchedule()
    if config.fault == "delay":
        return FaultSchedule(
            default=FaultRule(mode=FaultMode.DELAY, delay=config.delay or 1.0)
        )
    if config.fault == "delay_past_deadline":
        return FaultSchedule(
            default=FaultRule(mode=FaultMode.DELAY, delay=config.delay or 8.0)
        )
    if config.fault == "drop":
        return FaultSchedule(default=FaultRule(mode=FaultMode.DROP))
    if config.fault == "duplicate":
        return FaultSchedule(
            default=FaultRule(mode=FaultMode.DUPLICATE, duplicate_delay=0.5)
        )
    if config.fault == "reorder":
        return FaultSchedule(
            default=FaultRule(),
            by_send_index={
                1: FaultRule(mode=FaultMode.REORDER, delay=3.0, reorder_bias=1.0)
            },
        )
    raise ValueError(f"unsupported fault {config.fault}")


class DistributedScenarioRunner:
    def __init__(self, guard: CausalGuard | None = None):
        self.guard = guard or CausalGuard()

    def build_bundle(
        self, config: DistributedRunnerConfig
    ) -> DistributedScenarioBundle:
        if config.scenario_id == "transaction_revocation":
            from are.simulation.scenarios.scenario_dcore.transaction import (
                build_transaction_bundle,
            )

            return build_transaction_bundle(config)
        if config.scenario_id in {"farm_wetjune", "farm_wetjune_fungicide_dcore"}:
            from are.simulation.scenarios.scenario_dcore.farm import build_farm_bundle

            return build_farm_bundle(config)
        raise ValueError(f"unknown D-CORE scenario {config.scenario_id!r}")

    def run(
        self,
        config: DistributedRunnerConfig,
        bundle: DistributedScenarioBundle | None = None,
    ) -> DistributedRunResult:
        # The paper benchmark uses the ordinary native L3 scenarios.  The old
        # transaction and checkpoint bundles remain available only for semantic
        # regression tests and v1 trace replay.
        from are.simulation.scenarios.scenario_dcore.farm_catalog import FARM_SCENARIOS

        if bundle is None and config.scenario_id in FARM_SCENARIOS:
            return self._run_native_farm(config)
        bundle = bundle or self.build_bundle(config)
        actor_specs = {actor.actor_id: actor for actor in bundle.spec.actors}
        actor_ids = tuple(actor_specs)
        if config.controller_mode == "llm":
            from are.simulation.agents.are_simulation_agent_config import (
                LLMEngineConfig,
            )
            from are.simulation.agents.llm.llm_engine_builder import LLMEngineBuilder
            from are.simulation.distributed.controllers import FarmARELLMController

            builder = LLMEngineBuilder()
            for actor_id in actor_ids:
                model = config.model_by_actor.get(actor_id)
                if not model:
                    raise ValueError(f"LLM mode requires model_by_actor[{actor_id!r}]")
                engine = builder.create_engine(
                    LLMEngineConfig(
                        model_name=model,
                        provider=config.provider_by_actor.get(actor_id),
                        endpoint=config.endpoint_by_actor.get(actor_id),
                    )
                )
                bundle.controllers[actor_id] = FarmARELLMController(engine)
        elif config.controller_mode == "replay":
            from are.simulation.distributed.controllers import ReplayController
            from are.simulation.distributed.models import DistributedTrace

            replay_payload = json.loads(
                Path(config.replay_trace or "").read_text(encoding="utf-8")
            )
            replay_trace = DistributedTrace.model_validate(replay_payload)
            for actor_id in actor_ids:
                bundle.controllers[actor_id] = ReplayController(
                    decision.proposed_intent
                    for decision in replay_trace.decisions
                    if decision.actor_id == actor_id
                )
        if set(bundle.controllers) != set(actor_ids):
            raise ValueError("scenario must provide one controller per actor")
        run_id = f"{bundle.spec.task_id}:{stable_digest(config.model_dump(mode='json'))[:12]}"
        recorder = CausalTraceRecorder(run_id, bundle.spec.task_id, actor_ids)
        scheduler = DeterministicScheduler()
        transport = InProcessTransport(
            actor_ids,
            schedule=fault_schedule_from_config(config),
            seed=config.scheduler_seed,
        )
        stores = {actor_id: KnowledgeStore(actor_id) for actor_id in actor_ids}
        inboxes: dict[str, list[Any]] = {actor_id: [] for actor_id in actor_ids}
        previous_results: dict[str, Any] = {actor_id: None for actor_id in actor_ids}
        unresolved: dict[str, tuple[str, ...]] = {
            actor_id: () for actor_id in actor_ids
        }
        evidence_ids: set[str] = set()
        send_events: dict[str, str] = {}
        message_counter = 0
        deferrals: dict[tuple[str, str], int] = {}
        guard_block_count = 0

        for actor_id, items in bundle.initial_knowledge.items():
            for item in items:
                stores[actor_id].add(item)
                evidence_ids.add(item.item_id)
                evidence_ids.update(item.evidence_ids)

        def view(actor_id: str) -> LocalView:
            return LocalView(
                actor=actor_specs[actor_id],
                logical_time=scheduler.logical_time,
                world_time=scheduler.logical_time,
                knowledge=stores[actor_id].items,
                inbox=tuple(inboxes[actor_id]),
                vector_clock=recorder.clock(actor_id),
                unresolved=unresolved[actor_id],
                previous_result=previous_results[actor_id],
            )

        for actor_id, controller in bundle.controllers.items():
            controller.initialize(actor_specs[actor_id], view(actor_id))
            scheduler.schedule(
                0, SchedulerPriority.ACTOR_ACTIVATION, actor_id, "activate"
            )
        for world_event in bundle.world_events:
            scheduler.schedule(
                world_event.logical_time,
                SchedulerPriority.WORLD_EFFECT,
                "world",
                "world_effect",
                {"definition": world_event},
            )

        steps = 0
        while scheduler and steps < config.max_logical_steps:
            item = scheduler.pop()
            steps += 1
            if item.kind == "world_effect":
                definition = item.payload["definition"]
                payload = definition.handler(item.logical_time)
                recorder.record(
                    EventKind.WORLD_EFFECT,
                    "world",
                    item.logical_time,
                    action=definition.name,
                    payload=payload,
                )
                continue
            if item.kind == "delivery":
                deliveries = transport.deliver_next(item.logical_time)
                for delivery in deliveries:
                    envelope = delivery.envelope
                    duplicate = (
                        envelope.message_id in stores[envelope.recipient].inbox_frontier
                    )
                    added = stores[envelope.recipient].receive(
                        envelope, item.logical_time
                    )
                    inboxes[envelope.recipient].append(envelope)
                    fact_keys = [entry.fact_key for entry in added]
                    if isinstance(envelope, CausalHandoff) and not fact_keys:
                        fact_keys = [claim.fact_key for claim in envelope.claims]
                    claim_values = (
                        {claim.fact_key: claim.value for claim in envelope.claims}
                        if isinstance(envelope, CausalHandoff)
                        else {}
                    )
                    receive_event = recorder.record(
                        EventKind.MESSAGE_RECEIVE,
                        envelope.recipient,
                        item.logical_time,
                        received_clock=envelope.vector_clock,
                        causal_parents=(send_events[envelope.message_id],),
                        message_id=envelope.message_id,
                        status="duplicate" if duplicate else "ok",
                        payload={
                            "fact_keys": fact_keys,
                            "claim_values": claim_values,
                            "copy_index": delivery.copy_index,
                        },
                    )
                    evidence_ids.add(receive_event.event_id)
                next_delivery = transport.next_delivery_time()
                if next_delivery is not None:
                    scheduler.schedule(
                        next_delivery,
                        SchedulerPriority.MESSAGE_DELIVERY,
                        item.actor_id,
                        "delivery",
                    )
                continue
            if item.kind != "activate":
                continue
            actor_id = item.actor_id
            controller = bundle.controllers[actor_id]
            if controller.is_complete():
                continue
            snapshot = stores[actor_id].snapshot(
                item.logical_time, recorder.clock(actor_id)
            )
            recorder.add_snapshot(snapshot)
            if item.payload.get("intent"):
                intent = AgentIntent.model_validate(item.payload["intent"])
            else:
                intent = controller.decide(view(actor_id))
            decision_event = recorder.record(
                EventKind.DECISION,
                actor_id,
                item.logical_time,
                action=intent.action,
                payload={
                    "intent_kind": intent.kind.value,
                    "knowledge_digest": snapshot.digest,
                },
                decision_context_id=intent.llm_input_log_id,
            )
            guard_result: GuardResult | None = None
            result: Any = None

            if intent.kind == IntentKind.FINISH:
                recorder.record(
                    EventKind.FINISH,
                    actor_id,
                    item.logical_time,
                    causal_parents=(decision_event.event_id,),
                )
                controller.observe({"status": "finished"})
            elif intent.kind == IntentKind.WAIT:
                result = {"status": "waiting", "until": item.logical_time + intent.wait}
                controller.observe(result)
                scheduler.schedule(
                    item.logical_time + intent.wait,
                    SchedulerPriority.ACTOR_ACTIVATION,
                    actor_id,
                    "activate",
                )
            elif intent.kind == IntentKind.SEND:
                if not intent.recipient:
                    raise ValueError("send intent requires a recipient")
                message_counter += 1
                message_id = f"m{message_counter:06d}"
                if config.handoff_mode == "causal":
                    claims = []
                    for fact_key in intent.claim_fact_keys:
                        knowledge = stores[actor_id].latest(fact_key)
                        if knowledge is None:
                            continue
                        claims.append(
                            Claim(
                                fact_key=fact_key,
                                value=knowledge.value,
                                fact_version_id=knowledge.item_id,
                                scope=knowledge.scope,
                                status=knowledge.status,
                                confidence=knowledge.confidence,
                                observed_at=knowledge.observed_at,
                                valid_until=knowledge.valid_until,
                                evidence_ids=knowledge.evidence_ids
                                or (knowledge.item_id,),
                                causal_parents=knowledge.causal_parents,
                            )
                        )
                    envelope = CausalHandoff(
                        message_id=message_id,
                        sender=actor_id,
                        recipient=intent.recipient,
                        text=intent.text,
                        claims=tuple(claims),
                        unresolved=(
                            intent.unresolved_requirements or unresolved[actor_id]
                        ),
                    )
                    fact_keys = [claim.fact_key for claim in claims]
                    claim_values = {claim.fact_key: claim.value for claim in claims}
                else:
                    envelope = FreeTextEnvelope(
                        message_id=message_id,
                        sender=actor_id,
                        recipient=intent.recipient,
                        text=intent.text,
                    )
                    fact_keys = []
                    claim_values = {}
                send_event = recorder.record(
                    EventKind.MESSAGE_SEND,
                    actor_id,
                    item.logical_time,
                    causal_parents=(decision_event.event_id,),
                    message_id=message_id,
                    payload={
                        "fact_keys": fact_keys,
                        "claim_values": claim_values,
                        "text": intent.text,
                        "envelope_type": config.handoff_mode,
                        "recipient": intent.recipient,
                    },
                )
                envelope = envelope.model_copy(
                    update={"vector_clock": send_event.vector_clock}
                )
                envelope = transport.send(envelope, item.logical_time)
                send_events[message_id] = send_event.event_id
                dropped = message_id in transport.snapshot()["dropped"]
                if dropped:
                    recorder.events[-1] = send_event.model_copy(
                        update={"status": "dropped"}
                    )
                next_delivery = transport.next_delivery_time()
                if next_delivery is not None:
                    scheduler.schedule(
                        next_delivery,
                        SchedulerPriority.MESSAGE_DELIVERY,
                        intent.recipient,
                        "delivery",
                    )
                result = {"message_id": message_id, "dropped": dropped}
                controller.observe(result)
                scheduler.schedule(
                    item.logical_time + 1,
                    SchedulerPriority.ACTOR_ACTIVATION,
                    actor_id,
                    "activate",
                )
            elif intent.kind in {IntentKind.ACT, IntentKind.OBSERVE}:
                if intent.action is None:
                    raise ValueError("action intent requires an action name")
                requirements = tuple(
                    requirement
                    for requirement in bundle.spec.fact_requirements
                    if requirement.actor_id == actor_id
                    and requirement.action == intent.action
                )
                causal_inbox = [
                    envelope
                    for envelope in inboxes[actor_id]
                    if isinstance(envelope, CausalHandoff)
                ]
                newest_handoff = max(
                    causal_inbox,
                    key=lambda envelope: (
                        sum(envelope.vector_clock.values()),
                        envelope.send_time,
                        envelope.message_id,
                    ),
                    default=None,
                )
                transport_closed = transport.watermark(actor_id)
                transport_state = transport.snapshot()
                dropped_ids = set(transport_state["dropped"])
                transport_gap = any(
                    message["message_id"] in dropped_ids
                    and message["recipient"] == actor_id
                    for message in transport_state["sent"]
                )
                guard_result = self.guard.evaluate(
                    actor=actor_specs[actor_id],
                    action=intent.action,
                    requirements=requirements,
                    knowledge=stores[actor_id],
                    logical_time=item.logical_time,
                    evidence_ids=evidence_ids,
                    transport_closed=transport_closed,
                    transport_gap=transport_gap,
                    unresolved_fact_keys=frozenset(
                        newest_handoff.unresolved if newest_handoff else ()
                    ),
                )
                should_log_guard = (
                    bool(requirements) or guard_result.verdict != GuardVerdict.ALLOW
                )
                if should_log_guard:
                    recorder.record(
                        EventKind.GUARD,
                        actor_id,
                        item.logical_time,
                        causal_parents=(decision_event.event_id,),
                        action=intent.action,
                        evidence_ids=guard_result.supporting_item_ids,
                        status=guard_result.verdict.value,
                        payload={"reasons": list(guard_result.reasons)},
                    )
                enforce = (
                    config.enforcement_mode == "enforce"
                    and config.handoff_mode == "causal"
                )
                if enforce and guard_result.verdict == GuardVerdict.DEFER:
                    key = (actor_id, intent.action)
                    deferrals[key] = deferrals.get(key, 0) + 1
                    controller.observe(
                        {"status": "deferred", "reasons": guard_result.reasons}
                    )
                    next_delivery = transport.next_delivery_time()
                    retry_at = (
                        next_delivery
                        if next_delivery is not None
                        else item.logical_time + 1
                    )
                    if deferrals[key] > config.max_deferrals and next_delivery is None:
                        retry_at = item.logical_time + 1
                    scheduler.schedule(
                        retry_at,
                        SchedulerPriority.ACTOR_ACTIVATION,
                        actor_id,
                        "activate",
                        {"intent": intent.model_dump(mode="json")},
                    )
                elif enforce and guard_result.verdict == GuardVerdict.BLOCK:
                    guard_block_count += 1
                    result = {"status": "blocked", "reasons": guard_result.reasons}
                    controller.observe(result)
                    scheduler.schedule(
                        item.logical_time + 1,
                        SchedulerPriority.ACTOR_ACTIVATION,
                        actor_id,
                        "activate",
                    )
                else:
                    handler = bundle.tool_handlers.get(intent.action)
                    if handler is None:
                        raise ValueError(f"no tool handler for {intent.action}")
                    execution = handler(intent.args, item.logical_time)
                    result = execution.get("result")
                    action_event = recorder.record(
                        EventKind.ACTION,
                        actor_id,
                        item.logical_time,
                        action=intent.action,
                        args=intent.args,
                        causal_parents=(
                            decision_event.event_id,
                            *intent.causal_parents,
                        ),
                        evidence_ids=guard_result.supporting_item_ids,
                        decision_context_id=decision_event.event_id,
                        farmare_event_id=execution.get("farmare_event_id"),
                        status="error"
                        if isinstance(result, dict) and result.get("error")
                        else "ok",
                        payload={
                            "scope": intent.scope
                            or (
                                (
                                    intent.args.get("start_ridge"),
                                    intent.args.get("end_ridge"),
                                )
                                if "start_ridge" in intent.args
                                and "end_ridge" in intent.args
                                else None
                            ),
                            "violated_requirements": execution.get(
                                "violated_requirements", []
                            ),
                            "stale_facts": execution.get("stale_facts", []),
                            "harmful": execution.get("harmful", False),
                            "result": result,
                        },
                    )
                    for world_effect in execution.get("world_effects", []):
                        recorder.record(
                            EventKind.WORLD_EFFECT,
                            "world",
                            item.logical_time,
                            action=world_effect.get("effect"),
                            causal_parents=(action_event.event_id,),
                            payload=world_effect,
                        )
                    for observation in execution.get("observations", []):
                        observation_event = recorder.record(
                            EventKind.OBSERVATION,
                            actor_id,
                            item.logical_time,
                            action=intent.action,
                            causal_parents=(action_event.event_id,),
                            farmare_event_id=execution.get("farmare_event_id"),
                            payload=observation,
                        )
                        knowledge = KnowledgeItem(
                            item_id=observation_event.event_id,
                            fact_key=observation["fact_key"],
                            value=observation.get("value"),
                            scope=observation.get("scope"),
                            status=EpistemicStatus.OBSERVED,
                            source_actor=actor_id,
                            evidence_ids=(observation_event.event_id,),
                            observed_at=observation.get(
                                "observed_at", item.logical_time
                            ),
                            learned_at=item.logical_time,
                            valid_until=observation.get("valid_until"),
                            confidence=observation.get("confidence"),
                            causal_parents=(action_event.event_id,),
                            vector_clock=observation_event.vector_clock,
                        )
                        recipients = (
                            actor_ids
                            if config.visibility_mode == "shared_blackboard"
                            else (actor_id,)
                        )
                        for recipient in recipients:
                            stores[recipient].add(
                                knowledge
                                if recipient == actor_id
                                else knowledge.model_copy(
                                    update={
                                        "item_id": f"blackboard:{recipient}:{knowledge.item_id}",
                                        "learned_at": item.logical_time,
                                    }
                                )
                            )
                        evidence_ids.add(observation_event.event_id)
                    controller.observe(result)
                    scheduler.schedule(
                        item.logical_time + 1,
                        SchedulerPriority.ACTOR_ACTIVATION,
                        actor_id,
                        "activate",
                    )
            else:
                raise ValueError(f"unsupported intent kind {intent.kind}")

            recorder.add_decision(
                DecisionRecord(
                    decision_id=decision_event.event_id,
                    actor_id=actor_id,
                    logical_time=item.logical_time,
                    knowledge_snapshot=snapshot,
                    proposed_intent=intent,
                    guard=guard_result,
                    prompt_digest=stable_digest(view(actor_id).model_dump(mode="json")),
                    prompt_message_ids=tuple(
                        message.message_id for message in view(actor_id).inbox
                    ),
                    llm_input_log_id=intent.llm_input_log_id,
                )
            )

        if steps >= config.max_logical_steps and scheduler:
            raise RuntimeError("distributed run exceeded max_logical_steps")
        if bundle.continuation is not None:
            continuation = bundle.continuation()
            recorder.record(
                EventKind.WORLD_EFFECT,
                "world",
                scheduler.logical_time,
                action="fixed_physics_continuation",
                payload=continuation,
            )
        outcome = bundle.outcome()
        outcome["guard_block_count"] = guard_block_count
        harmful_actions = [
            event
            for event in recorder.events
            if event.kind == EventKind.ACTION and event.payload.get("harmful")
        ]
        outcome["harmful_write_count"] = len(harmful_actions)
        outcome["safety_success"] = not harmful_actions
        if guard_block_count and not outcome.get("success"):
            outcome["success"] = True
        outcome["transport"] = transport.snapshot()
        if config.interleaving_mode == "enumerate":
            if bundle.spec.task_id != "transaction_revocation":
                raise ValueError(
                    "bounded interleaving enumeration is only available for "
                    "transaction_revocation in the MVP"
                )
            from are.simulation.scenarios.scenario_dcore.transaction import (
                transaction_interleavings,
            )

            outcome["schedule_manifests"] = [
                {"schedule_id": f"schedule-{index:03d}", "events": schedule}
                for index, schedule in enumerate(transaction_interleavings())
            ]
        source_trace = bundle.source_trace
        if bundle.source_trace_builder is not None and config.output_dir:
            source_trace = str(Path(config.output_dir) / "farmare_trace.json")
        trace = recorder.build(
            configuration=config.model_dump(mode="json"),
            outcome=outcome,
            source_trace=source_trace,
        )
        metrics = evaluate_dcore(bundle.spec, trace)
        artifacts = self._write_artifacts(config, bundle, trace, metrics)
        return DistributedRunResult(
            trace=trace,
            metrics=metrics,
            attribution=metrics["attribution"],
            artifacts=artifacts,
        )

    def _run_native_farm(self, config: DistributedRunnerConfig) -> DistributedRunResult:
        from are.simulation.distributed.native_season import (
            NativeDistributedSeasonRunner,
        )

        started = time.perf_counter()
        execution = NativeDistributedSeasonRunner(self.guard).run(config)
        if execution.trace.schema_version == "dcore_trace_v5":
            from are.simulation.distributed.evaluator_v5 import (
                evaluate_farm_dcore_v5,
            )

            if execution.process_spec is None:
                raise ValueError("v5 trace has no v5 process specification")
            metrics = evaluate_farm_dcore_v5(execution.process_spec, execution.trace)
        elif execution.petri_net.schema_version == "farm_petri_v3":
            from are.simulation.distributed.evaluator_v4 import (
                evaluate_farm_dcore_v4,
            )

            metrics = evaluate_farm_dcore_v4(execution.petri_net, execution.trace)
        else:
            from are.simulation.distributed.evaluator_v3 import evaluate_farm_dcore

            metrics = evaluate_farm_dcore(execution.petri_net, execution.trace)
        runtime_seconds = time.perf_counter() - started
        artifacts = self._write_native_artifacts(
            config, execution, metrics, runtime_seconds=runtime_seconds
        )
        return DistributedRunResult(
            trace=execution.trace,
            metrics=metrics,
            attribution=metrics["attribution"],
            artifacts=artifacts,
        )

    @staticmethod
    def _git_commit() -> str | None:
        try:
            return subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    def _write_native_artifacts(
        self,
        config: DistributedRunnerConfig,
        execution: Any,
        metrics: dict[str, Any],
        *,
        runtime_seconds: float,
    ) -> dict[str, str]:
        if not config.output_dir:
            return {}
        from are.simulation.distributed.petri import petri_to_dot, petri_to_pnml

        output = Path(config.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        metric_version = str(metrics["metric_version"])
        trace_version = str(execution.trace.schema_version)
        files = {
            "manifest": output
            / (
                "manifest.dcore_run_v5.json"
                if trace_version == "dcore_trace_v5"
                else (
                    "manifest.dcore_run_v4.json"
                    if trace_version == "dcore_trace_v4"
                    else "manifest.dcore_run_v3.json"
                )
            ),
            "trace": output / f"trace.{trace_version}.json",
            "farmare_trace": output / "farmare_trace.json",
            "exogenous_world": output / "exogenous_world.json",
            "petri_net": output / "petri_net.json",
            "process_spec": output / "farm_process_spec_v5.json",
            "team_spec": output / "team_spec.json",
            "role_refinement": output / "role_refinement.json",
            "expert_annotations": output / "expert_annotation_manifest.json",
            "occurrence_net": output / "occurrence_net.json",
            "petri_dot": output / "petri_net.dot",
            "petri_pnml": output / "petri_net.pnml",
            "metrics": output / f"metrics.{metric_version}.json",
            "attribution": output / "attribution.json",
            "outcome": output / "farm_outcome.json",
            "row": output / "experiment_row.json",
            "complete": output / "COMPLETED.json",
        }
        trace = execution.trace
        outcome = trace.outcome
        team_profile = metrics.get("team_profile", {})
        per_agent_telemetry = team_profile.get("per_agent_telemetry", {})
        total_model_calls = sum(
            int(item.get("model_call_count", 0))
            for item in per_agent_telemetry.values()
        )
        total_prompt_tokens = sum(
            int(item.get("prompt_tokens", 0)) for item in per_agent_telemetry.values()
        )
        total_completion_tokens = sum(
            int(item.get("completion_tokens", 0))
            for item in per_agent_telemetry.values()
        )
        total_tokens = sum(
            int(item.get("total_tokens", 0)) for item in per_agent_telemetry.values()
        )
        model_completion_duration = sum(
            float(item.get("completion_duration", 0.0))
            for item in per_agent_telemetry.values()
        )
        manifest = {
            "schema_version": (
                "dcore_run_v5"
                if trace_version == "dcore_trace_v5"
                else (
                    "dcore_run_v4"
                    if trace_version == "dcore_trace_v4"
                    else "dcore_run_v3"
                )
            ),
            "run_id": trace.run_id,
            "scenario_id": config.scenario_id,
            "native_scenario_id": outcome.get("native_scenario_id"),
            "metric_version": metric_version,
            "metric_paper_eligible": bool(
                metrics.get("metric_profile", {}).get("paper_eligible")
            ),
            "paper_mode": config.paper_mode,
            "bounded_llm_smoke": config.bounded_llm_smoke,
            "petri_spec_version": execution.petri_net.schema_version,
            "process_spec_version": (
                execution.process_spec.schema_version
                if execution.process_spec is not None
                else None
            ),
            "process_spec_digest": (
                execution.process_spec.digest
                if execution.process_spec is not None
                else None
            ),
            "oracle_version": execution.petri_net.oracle_version,
            "expert_review_status": execution.petri_net.expert_review_status,
            "expert_annotation_digest": execution.petri_net.metadata.get(
                "review_manifest_digest"
            ),
            "trace_schema_version": trace.schema_version,
            "team_id": trace.team_id,
            "team_size": len(trace.actors),
            "team_spec_digest": trace.team_spec_digest,
            "role_refinement_digest": trace.role_refinement_digest,
            "git_commit": self._git_commit(),
            "python": sys.version,
            "platform": platform.platform(),
            # The native runner may resolve stable fault targets and reviewed
            # deadlines from the frozen process. Persist that resolved payload,
            # not the caller's pre-resolution configuration.
            "configuration": trace.configuration,
            "exogenous_world_digest": outcome.get("exogenous_world_digest"),
            "metric_profile": metrics.get("metric_profile"),
            "runtime_seconds": runtime_seconds,
            "source_trace": trace.source_trace,
        }
        model_configuration_id = stable_digest(
            {
                "models": config.model_by_actor,
                "providers": config.provider_by_actor,
                "families": config.agent_family_by_actor,
                "endpoints": config.endpoint_by_actor,
                "history_windows": config.history_window_by_actor,
                "temperatures": config.temperature_by_actor,
                "max_model_calls": config.max_model_calls,
                "max_output_tokens": config.max_output_tokens,
                "team_id": trace.team_id,
                "team_spec_digest": trace.team_spec_digest,
                "controller_profile_id": config.controller_profile_id,
            }
        )[:16]
        row = {
            "run_id": trace.run_id,
            "scenario": config.scenario_id,
            "native_scenario": outcome.get("native_scenario_id"),
            "team_id": trace.team_id,
            "team_size": len(trace.actors),
            "team_spec_digest": trace.team_spec_digest,
            "role_refinement_digest": trace.role_refinement_digest,
            "communication_topology": outcome.get("communication_topology"),
            "activation_policy": outcome.get("activation_policy"),
            "condition": config.condition_id,
            "controller_profile_id": config.controller_profile_id,
            "controller": config.controller_mode,
            "visibility": config.visibility_mode,
            "handoff": config.handoff_mode,
            "enforcement": config.enforcement_mode,
            "fault": config.fault,
            "fault_target_ids": trace.configuration.get("fault_target_ids", []),
            "fault_valid_until_world_time": trace.configuration.get(
                "fault_valid_until_world_time"
            ),
            "fault_delivery_world_time": trace.configuration.get(
                "fault_delivery_world_time"
            ),
            "fault_deadline_world_time": trace.configuration.get(
                "fault_deadline_world_time"
            ),
            "world_seed": config.world_seed,
            "scheduler_seed": config.scheduler_seed,
            "model_seed": config.model_seed,
            "model_seed_applied": outcome.get("model_seed_applied"),
            "fault_seed": config.fault_seed,
            "fault_seed_applied": outcome.get("fault_seed_applied"),
            "fault_schedule_type": outcome.get("fault_schedule_type"),
            "fault_manifested": outcome.get("fault_manifested"),
            "repeat_index": config.repeat_index,
            "model_configuration_id": model_configuration_id,
            "pair_id": (
                f"{config.scenario_id}:t{trace.team_id}:w{config.world_seed}:"
                f"q{config.scheduler_seed}:m{config.model_seed}:"
                f"c{model_configuration_id}:r{config.repeat_index}"
            ),
            "world_cluster_id": f"{config.scenario_id}:w{config.world_seed}",
            "metric_version": metric_version,
            "trace_schema_version": trace.schema_version,
            "paper_mode": config.paper_mode,
            "bounded_llm_smoke": config.bounded_llm_smoke,
            "process_spec_digest": (
                execution.process_spec.digest
                if execution.process_spec is not None
                else None
            ),
            "metric_paper_eligible": bool(
                metrics.get("metric_profile", {}).get("paper_eligible")
            ),
            "event_fidelity": metrics["event_fidelity"],
            "required_event_coverage": metrics["coverage"],
            "argument_fidelity": metrics["argument_fidelity"],
            "spatial_fidelity": metrics["spatial_fidelity"],
            "timing_fidelity": metrics["timing_fidelity"],
            "causal_conformance": metrics["causal_conformance"],
            "coordination_failure_rate": metrics["coordination_failure_rate"],
            "team_coordination_failure_rate": metrics.get("team_profile", {}).get(
                "coordination_failure_rate"
            ),
            "coordination_edge_density": metrics.get("team_profile", {}).get(
                "coordination_edge_density"
            ),
            "message_count": metrics.get("team_profile", {}).get("message_count"),
            "delivery_count": metrics.get("team_profile", {}).get("delivery_count"),
            "fact_handoff_depth_max": metrics.get("team_profile", {}).get(
                "fact_handoff_depth_max"
            ),
            "local_dcore_mean": metrics.get("team_profile", {}).get("local_dcore_mean"),
            "local_dcore_min": metrics.get("team_profile", {}).get("local_dcore_min"),
            "local_dcore_max": metrics.get("team_profile", {}).get("local_dcore_max"),
            "n_local_scored": metrics.get("team_profile", {}).get("n_local_scored"),
            "total_model_calls": total_model_calls,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "tokens_per_model_call": (
                total_tokens / total_model_calls if total_model_calls else None
            ),
            "model_completion_duration_seconds": model_completion_duration,
            "runtime_seconds": runtime_seconds,
            "runtime_per_model_call_seconds": (
                runtime_seconds / total_model_calls if total_model_calls else None
            ),
            "team_call_budget": outcome.get("team_call_budget"),
            "team_token_budget": outcome.get("team_token_budget"),
            "call_budget_exhausted": outcome.get("call_budget_exhausted"),
            "token_budget_exhausted": outcome.get("token_budget_exhausted"),
            "token_budget_overshoot": outcome.get("token_budget_overshoot"),
            "token_budget_policy": outcome.get("token_budget_policy"),
            "per_agent_telemetry": per_agent_telemetry,
            "model_by_actor": config.model_by_actor,
            "provider_by_actor": config.provider_by_actor,
            "agent_family_by_actor": config.agent_family_by_actor,
            "dcore_score": metrics["dcore_score"],
            "local_global_gap": metrics["local_global_gap"],
            "information_global_discordance": (
                metrics.get("information_global_discordance", {}).get(
                    "information_global_discordance"
                )
            ),
            "information_global_table": metrics.get(
                "information_global_discordance", {}
            ).get("weighted_table"),
            **{
                f"igd_{cell.lower()}": metrics.get(
                    "information_global_discordance", {}
                ).get("weighted_table", {}).get(cell)
                for cell in ("L0_G0", "L0_G1", "L1_G0", "L1_G1")
            },
            "provenance_failure_localization": metrics.get(
                "provenance_failure_localization", []
            ),
            "decision_failure_localization": metrics.get(
                "decision_failure_localization", []
            ),
            "guard_effectiveness": metrics.get("guard_effectiveness", {}),
            "guard_unsafe_proposals": metrics.get("guard_effectiveness", {}).get(
                "unsafe_proposals"
            ),
            "guard_prevented_unsafe_writes": metrics.get(
                "guard_effectiveness", {}
            ).get("prevented_unsafe_writes"),
            "guard_false_blocks": metrics.get("guard_effectiveness", {}).get(
                "false_blocks"
            ),
            "guard_unnecessary_abstentions": metrics.get(
                "guard_effectiveness", {}
            ).get("unnecessary_abstentions"),
            "guard_eventual_recoveries": metrics.get("guard_effectiveness", {}).get(
                "eventual_recoveries"
            ),
            "guard_safety_benefit_rate": metrics.get("guard_effectiveness", {}).get(
                "safety_benefit_rate"
            ),
            "guard_false_block_rate": metrics.get("guard_effectiveness", {}).get(
                "false_block_rate"
            ),
            "po_pair_agreement": metrics["po_pair_agreement"],
            "petri_token_fitness": metrics["petri_token_fitness"],
            "petri_alignment_fitness": metrics["petri_alignment_fitness"],
            "harmful_extra_cost": metrics["harmful_extra_cost"],
            "unnecessary_write_count": metrics["unnecessary_write_count"],
            "redundant_read_count": metrics["redundant_read_count"],
            "error_propagation_depth": metrics["error_propagation_depth"],
            "error_propagation_affected_transitions": metrics[
                "error_propagation_affected_transitions"
            ],
            "error_propagation_duration_days": metrics[
                "error_propagation_duration_days"
            ],
            "structural_exposure_duration_days": metrics.get(
                "structural_exposure_duration_days"
            ),
            "first_critical_divergence_module": (
                metrics.get("long_horizon_profile", {})
                .get("first_critical_divergence", {})
                .get("module_id")
                if metrics.get("long_horizon_profile", {}).get(
                    "first_critical_divergence"
                )
                else None
            ),
            "successful_replanning_rate": metrics["recovery"][
                "successful_replanning_rate"
            ],
            "synchronization_lag_median_seconds": metrics["synchronization_lag"][
                "median"
            ],
            "synchronization_lag_p95_seconds": metrics["synchronization_lag"]["p95"],
            "never_received_fact_versions": len(
                metrics["synchronization_lag"]["never_received_versions"]
            ),
            "mean_message_path_length": metrics.get("team_profile", {}).get(
                "mean_message_path_length"
            ),
            "bfcl_tool_success": metrics["bfcl_tool_success"],
            "merged_pc_ktc": metrics["merged_pc_ktc"].get("combined"),
            "core_path_correctness": metrics["core_path_correctness"],
            "average_local_pc_ktc": metrics["average_local_pc_ktc"],
            "success": outcome.get("success"),
            "safety_success": outcome.get("safety_success"),
            "biological_yield_kg": outcome.get("biological_yield_kg"),
            "marketable_yield_kg": outcome.get("marketable_yield_kg"),
            "harvest_complete": outcome.get("harvest_complete"),
            "storage_complete": outcome.get("storage_complete"),
            "blocked_write_count": outcome.get("blocked_write_count"),
            "deferred_write_count": outcome.get("deferred_write_count"),
            "guard_rejection_count": outcome.get("guard_rejection_count"),
            "guard_abstention_count": outcome.get("guard_abstention_count"),
            "recovered_write_count": outcome.get("recovered_write_count"),
            "reobservation_recovery_count": metrics["recovery"].get(
                "reobservation_recovery_count"
            ),
            "new_delivery_recovery_count": metrics["recovery"].get(
                "new_delivery_recovery_count"
            ),
            "infrastructure_failure": bool(outcome.get("infrastructure_errors")),
            "controller_failure": bool(outcome.get("controller_failure")),
            "controller_errors": outcome.get("controller_errors", []),
            "phase_profile": metrics.get("phase_profile", {}),
            "module_profile": metrics.get("module_profile", []),
            "per_ridge_yield": outcome.get("per_ridge_yield", []),
            "resource_use": outcome.get("resource_use", {}),
            "artifact_dir": str(output),
        }
        payloads = {
            "manifest": manifest,
            "trace": trace.model_dump(mode="json"),
            "exogenous_world": trace.configuration.get("exogenous_world_manifest", {}),
            "petri_net": execution.petri_net.model_dump(mode="json"),
            "process_spec": (
                execution.process_spec.model_dump(mode="json")
                if execution.process_spec is not None
                else None
            ),
            "team_spec": trace.configuration.get("team_spec", {}),
            "role_refinement": trace.configuration.get("role_refinement"),
            "expert_annotations": (
                {
                    "schema_version": "farm_dcore_review_reference_v5",
                    "process_digest": execution.process_spec.digest,
                    "review_digest": execution.process_spec.review_digest,
                    "expert_review_status": (
                        execution.process_spec.expert_review_status
                    ),
                    "annotation_status": execution.process_spec.annotation_status,
                    "review_metadata": execution.process_spec.metadata,
                }
                if execution.process_spec is not None
                else json.loads(
                    Path(execution.petri_net.metadata["review_manifest"]).read_text(
                        encoding="utf-8"
                    )
                )
            ),
            "occurrence_net": metrics["occurrence_net"],
            "metrics": metrics,
            "attribution": metrics["attribution"],
            "outcome": outcome,
            "row": row,
            "complete": {
                "status": "complete",
                "run_id": trace.run_id,
                "metric_version": metric_version,
            },
        }
        for key in (
            "manifest",
            "trace",
            "exogenous_world",
            "petri_net",
            "process_spec",
            "team_spec",
            "role_refinement",
            "expert_annotations",
            "occurrence_net",
            "metrics",
            "attribution",
            "outcome",
            "row",
            "complete",
        ):
            files[key].write_text(
                json.dumps(payloads[key], ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        files["farmare_trace"].write_text(
            execution.farmare_trace_json, encoding="utf-8"
        )
        files["petri_dot"].write_text(
            petri_to_dot(execution.petri_net), encoding="utf-8"
        )
        files["petri_pnml"].write_text(
            petri_to_pnml(execution.petri_net), encoding="utf-8"
        )
        return {key: str(path) for key, path in files.items()}

    def _write_artifacts(
        self,
        config: DistributedRunnerConfig,
        bundle: DistributedScenarioBundle,
        trace,
        metrics: dict[str, Any],
    ) -> dict[str, str]:
        if not config.output_dir:
            return {}
        output = Path(config.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        files = {
            "manifest": output / "manifest.json",
            "trace": output / "trace.dcore.json",
            "metrics": output / "metrics.json",
            "outcome": output / "outcome.json",
            "row": output / "experiment_row.json",
        }
        if bundle.source_trace_builder is not None:
            files["farmare_trace"] = output / "farmare_trace.json"
        manifest = {
            "schema_version": "dcore_run_v1",
            "run_id": trace.run_id,
            "task_id": trace.task_id,
            "configuration": config.model_dump(mode="json"),
            "source_trace": trace.source_trace,
        }
        row = {
            "run_id": trace.run_id,
            "scenario": config.scenario_id,
            "controller": config.controller_mode,
            "visibility": config.visibility_mode,
            "handoff": config.handoff_mode,
            "enforcement": config.enforcement_mode,
            "fault": config.fault,
            "seed": config.scheduler_seed,
            "schedule_id": (
                "enumerated" if config.interleaving_mode == "enumerate" else "default"
            ),
            "event_fidelity": metrics["event_fidelity"],
            "causal_conformance": metrics["causal_conformance"],
            "po_pair_agreement": metrics["po_pair_agreement"],
            "dcore_score": metrics["dcore_score"],
            "local_global_gap": metrics["local_global_gap"],
            "merged_pc_ktc": metrics["merged_pc_ktc"]["combined"],
            "average_local_pc_ktc": metrics["average_local_pc_ktc"],
            "synchronization_lag": metrics["synchronization_lag"],
            "success": trace.outcome.get("success"),
            "safety_success": trace.outcome.get("safety_success"),
            "biological_yield_kg": trace.outcome.get("biological_yield_kg"),
            "marketable_yield_kg": trace.outcome.get("marketable_yield_kg"),
        }
        payloads = {
            "manifest": manifest,
            "trace": trace.model_dump(mode="json"),
            "metrics": metrics,
            "outcome": trace.outcome,
            "row": row,
        }
        if bundle.source_trace_builder is not None:
            payloads["farmare_trace"] = json.loads(bundle.source_trace_builder())
        for key, path in files.items():
            path.write_text(
                json.dumps(payloads[key], ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        return {key: str(path) for key, path in files.items()}
