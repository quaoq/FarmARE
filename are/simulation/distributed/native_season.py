"""Genuinely agent-driven, parameterized-team execution of FarmARE L3 seasons."""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from are.simulation.apps.farm_world import FarmWorldApp
from are.simulation.apps.farm_world.farm_world_app import (
    DEFAULT_RIDGE_WIDTH_M,
    FIELD_LENGTH_M,
)
from are.simulation.data_handler.exporter import JsonScenarioExporter
from are.simulation.distributed.controllers import (
    OracleCeilingController,
    OracleCeilingCoordinator,
)
from are.simulation.distributed.farm_adapter import FarmScenarioAdapter, scope_from_args
from are.simulation.distributed.guard import CausalGuard
from are.simulation.distributed.knowledge import KnowledgeStore
from are.simulation.distributed.models import (
    ActorSpec,
    AgentIntent,
    AgentTeamSpec,
    CausalHandoff,
    Claim,
    DecisionRecord,
    DistributedRunnerConfig,
    DistributedTrace,
    EpistemicStatus,
    EventKind,
    FactRequirement,
    FactVersionRecord,
    FaultManifestationRecord,
    FreeTextEnvelope,
    GuardResult,
    GuardVerdict,
    IntentKind,
    KnowledgeItem,
    LocalView,
    PolicyCommitmentRecord,
    RequirementVerdict,
    WorldBranchCommitmentRecord,
    stable_digest,
)
from are.simulation.distributed.petri import (
    DataGuardSpec,
    InformationPolicySpec,
    OccurrenceNet,
    PetriNetSpec,
    TransitionKind,
    WorldBranchSpec,
    unfold_petri_net,
)
from are.simulation.distributed.teams import (
    FIELD_INTELLIGENCE,
    FOUR_AGENT_TEAM_ID,
    PRIMARY_TEAM_ID,
    RESOURCE_MANAGEMENT,
    SCOUTING,
    build_builtin_team,
    can_send,
    load_role_refinement,
    load_team_spec,
    recipients_for,
    refine_petri_for_team,
    shortest_path,
    team_digest,
)
from are.simulation.distributed.tool_gateway import RoleToolGateway
from are.simulation.distributed.trace import CausalTraceRecorder
from are.simulation.distributed.transport import (
    FaultMode,
    FaultRule,
    FaultSchedule,
    InProcessTransport,
)
from are.simulation.environment import Environment, EnvironmentConfig
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.scenario_dcore.farm_catalog import (
    compile_paper_petri_net,
    create_native_scenario,
    native_action_name,
)
from are.simulation.types import Action, OracleEvent


@dataclass
class NativeSeasonExecution:
    scenario: Scenario
    environment: Environment
    petri_net: PetriNetSpec
    occurrence_net: OccurrenceNet
    trace: DistributedTrace
    farmare_trace_json: str
    process_spec: Any | None = None


def _has_path(team: AgentTeamSpec, sender: str, recipient: str) -> bool:
    try:
        shortest_path(team, sender, recipient)
    except ValueError:
        return False
    return True


def _farm_outcome(
    farm_world: FarmWorldApp, initial_inventory: dict[str, Any]
) -> dict[str, Any]:
    state = farm_world.get_state()
    inventory = state.get("inventory", {})
    ridges = state.get("ridges", [])
    per_ridge: list[dict[str, Any]] = []
    biological_yield_kg = 0.0
    if farm_world.physics_active:
        for ridge_id, yield_state in sorted(
            farm_world.physics.yield_recovery.states.items()
        ):
            biological = float(
                getattr(yield_state, "biological_yield_g_m2", 0.0) or 0.0
            )
            per_ridge.append(
                {
                    "ridge_id": int(ridge_id),
                    "biological_yield_g_m2": biological,
                    "recovered_yield_g_m2": float(
                        getattr(
                            yield_state,
                            "recovered_yield_g_m2_at_market_moisture",
                            0.0,
                        )
                        or 0.0
                    ),
                    "grain_moisture_fraction": getattr(
                        yield_state, "grain_moisture_frac", None
                    ),
                }
            )
            biological_yield_kg += (
                biological * FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M / 1000.0
            )
    harvested = [bool(ridge.get("harvested")) for ridge in ridges]
    resource_use = {
        key: round(max(0.0, float(initial) - float(inventory[key])), 6)
        for key, initial in initial_inventory.items()
        if isinstance(initial, (int, float))
        and isinstance(inventory.get(key), (int, float))
    }
    harvest_complete = bool(harvested and all(harvested))
    storage_complete = float(inventory.get("warehouse_grain_kg", 0.0) or 0.0) > 0
    return {
        "success": harvest_complete and storage_complete,
        "biological_yield_kg": round(biological_yield_kg, 6),
        "marketable_yield_kg": float(inventory.get("warehouse_grain_kg", 0.0) or 0.0),
        "yield_is_final": harvest_complete,
        "harvest_complete": harvest_complete,
        "storage_complete": storage_complete,
        "per_ridge_yield": per_ridge,
        "inventory": inventory,
        "resource_use": resource_use,
        "management_regime": state.get("management_regime", {}),
    }


class NativeDistributedSeasonRunner:
    """Run persistent agents against native FarmARE tools without oracle control."""

    def __init__(
        self,
        guard: CausalGuard | None = None,
        controllers: dict[str, Any] | None = None,
    ):
        self.guard = guard or CausalGuard()
        self.controllers = controllers

    @staticmethod
    def _load_petri_net(
        config: DistributedRunnerConfig,
    ) -> tuple[PetriNetSpec, Any | None]:
        if config.petri_spec_path:
            path = Path(config.petri_spec_path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            process = None
            if raw.get("schema_version") == "farm_process_spec_v5":
                from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5

                process = FarmProcessSpecV5.model_validate(raw)
                net = process.occurrence_net
            else:
                net = PetriNetSpec.model_validate(raw)
            if (
                net.scenario_id != config.scenario_id
                and net.metadata.get("public_scenario_id") != config.scenario_id
            ):
                raise ValueError("frozen Petri specification scenario mismatch")
            return net, process
        return (
            compile_paper_petri_net(config.scenario_id, world_seed=config.world_seed),
            None,
        )

    @staticmethod
    def _validate_team_contract(
        config: DistributedRunnerConfig,
        team: AgentTeamSpec,
        petri_net: PetriNetSpec,
        refinement: Any | None,
    ) -> None:
        actor_ids = tuple(actor.actor_id for actor in team.actors)
        known = set(actor_ids)
        if tuple(petri_net.actors) != actor_ids:
            raise ValueError("team actor order must match the Petri specification")
        action_owners = {
            action: actor.actor_id
            for actor in team.actors
            for action in actor.permitted_actions
        }
        time_owner = action_owners.get("SystemApp__advance_time")
        if time_owner is not None and time_owner not in team.time_authority:
            raise ValueError("SystemApp__advance_time owner lacks time authority")
        for mapping_name in (
            "model_by_actor",
            "provider_by_actor",
            "endpoint_by_actor",
            "agent_family_by_actor",
            "history_window_by_actor",
            "temperature_by_actor",
        ):
            unknown = set(getattr(config, mapping_name)) - known
            if unknown:
                raise ValueError(
                    f"{mapping_name} contains actors outside the selected team: "
                    f"{sorted(unknown)}"
                )
        if team.petri_spec_digest:
            actual = petri_net.metadata.get("base_petri_spec_digest") or stable_digest(
                petri_net.model_dump(mode="json")
            )
            if team.petri_spec_digest != actual:
                raise ValueError("team Petri specification digest mismatch")
        for actor in actor_ids:
            if actor in team.time_authority:
                continue
            if not any(
                can_send(team, actor, authority) or _has_path(team, actor, authority)
                for authority in team.time_authority
            ):
                raise ValueError(
                    f"actor {actor!r} has no communication path to a time authority"
                )
        if config.paper_mode:
            if team.expert_review_status != "confirmed":
                raise ValueError("paper mode rejects an unconfirmed team specification")
            if team.team_id != PRIMARY_TEAM_ID:
                if refinement is None or refinement.expert_review_status != "confirmed":
                    raise ValueError(
                        "paper mode requires a confirmed role-refinement specification"
                    )
                actual_refinement = stable_digest(refinement.model_dump(mode="json"))
                if team.role_refinement_digest != actual_refinement:
                    raise ValueError("team role-refinement digest mismatch")

    @staticmethod
    def _validate_scientific_gate(
        config: DistributedRunnerConfig,
        petri_net: PetriNetSpec,
        team: AgentTeamSpec,
        process_spec: Any | None = None,
    ) -> None:
        if not config.paper_mode:
            return
        if config.scientific_contract != "v5":
            raise ValueError("paper mode requires scientific_contract=v5")
        if (
            process_spec is None
            or process_spec.schema_version != "farm_process_spec_v5"
        ):
            raise ValueError("paper mode requires a frozen farm_process_spec_v5")
        if (
            process_spec.annotation_status != "frozen"
            or process_spec.expert_review_status != "confirmed"
            or not process_spec.review_digest
            or process_spec.metadata.get("engineering_defaults")
        ):
            raise ValueError(
                "paper mode rejects an unreviewed v5 process specification"
            )
        # v5 review state is bound to the process digest.  Legacy v4 fields on
        # the embedded occurrence net are not a second scientific authority.
        from are.simulation.distributed.scientific_v5 import (
            ScientificGateManifestV5,
        )

        gate = ScientificGateManifestV5.model_validate_json(
            Path(config.scientific_gate_manifest or "").read_text(encoding="utf-8")
        )
        expected_spec_digest = process_spec.digest
        if gate.confirmed_process_digest != expected_spec_digest:
            raise ValueError("scientific gate manifest specification digest mismatch")
        if gate.confirmed_team_digest != team_digest(team):
            raise ValueError("scientific gate manifest team digest mismatch")
        expected_gate_status = (
            {"offline_complete", "complete"}
            if config.bounded_llm_smoke
            else {"complete"}
        )
        if gate.status not in expected_gate_status:
            raise ValueError("scientific gate is not authorized for this run stage")
        if gate.scenario_id != config.scenario_id:
            raise ValueError("scientific gate manifest scenario mismatch")
        repository_root = Path(__file__).parents[3]
        try:
            current_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            dirty = bool(
                subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=repository_root,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            )
            tags = set(
                subprocess.run(
                    ["git", "tag", "--points-at", "HEAD"],
                    cwd=repository_root,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.splitlines()
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise ValueError("paper mode cannot verify the release commit") from error
        if dirty or gate.code_commit != current_commit:
            raise ValueError("paper mode requires the clean, gate-pinned commit")
        if not config.bounded_llm_smoke and gate.release_tag not in tags:
            raise ValueError("paper mode requires the gate-pinned release tag")
        lock_path = repository_root / "uv.lock"
        lock_digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
        if gate.environment_lock_digest != lock_digest:
            raise ValueError("scientific gate environment lock digest mismatch")
        protocol_digest = hashlib.sha256(
            Path(__file__).with_name("EXPERIMENT_PROTOCOL.md").read_bytes()
        ).hexdigest()
        if gate.analysis_protocol_digest != protocol_digest:
            raise ValueError("scientific gate analysis protocol digest mismatch")
        required_gates = (
            "offline_semantic_gates",
            "prompt_leakage_check",
            "saved_trace_replay",
            "blocked_write_nonmutation",
            "allowed_write_exactly_once",
        )
        missing = [name for name in required_gates if getattr(gate, name) is not True]
        if missing:
            raise ValueError(
                "real-LLM run blocked by incomplete scientific gates: "
                + ", ".join(missing)
            )

    def run(self, config: DistributedRunnerConfig) -> NativeSeasonExecution:
        scenario = create_native_scenario(
            config.scenario_id, world_seed=config.world_seed
        )
        scenario_tools = scenario.get_tools()
        team = load_team_spec(config, scenario_tools)
        actor_ids = tuple(actor.actor_id for actor in team.actors)
        base_petri_net, loaded_process = self._load_petri_net(config)
        refinement = load_role_refinement(config, team, config.scenario_id)
        if tuple(base_petri_net.actors) == actor_ids:
            petri_net = base_petri_net
        else:
            if refinement is None:
                raise ValueError(
                    "custom team actors require a pre-refined matching Petri specification"
                )
            petri_net = refine_petri_for_team(base_petri_net, team, refinement)
        process_spec = loaded_process
        if config.scientific_contract == "v5":
            from are.simulation.distributed.scientific_v5 import (
                engineering_process_from_v4,
            )

            if process_spec is None:
                process_spec = engineering_process_from_v4(petri_net)
            elif process_spec.occurrence_net != petri_net:
                raise ValueError(
                    "v5 process occurrence net does not match the resolved team net"
                )
        self._validate_team_contract(config, team, petri_net, refinement)
        self._validate_scientific_gate(config, petri_net, team, process_spec)
        if process_spec is not None and process_spec.annotation_status == "frozen":
            requested_mode = {
                "none": "reliable",
                "delay": "delay_within_validity",
            }.get(config.fault, config.fault)
            treatment = next(
                (
                    item
                    for item in process_spec.fault_treatments
                    if item.mode == requested_mode
                ),
                None,
            )
            if treatment is None:
                raise ValueError(
                    f"frozen v5 process has no treatment for {requested_mode!r}"
                )
            if config.fault_target_ids and tuple(config.fault_target_ids) != tuple(
                treatment.target_ids
            ):
                raise ValueError("run fault targets disagree with the frozen treatment")
            fault_deadline = None
            if treatment.deadline_id:
                policy = next(
                    (
                        item
                        for item in process_spec.information_policies
                        if item.policy_id == treatment.deadline_id
                    ),
                    None,
                )
                if policy is None or policy.deadline_world_time is None:
                    raise ValueError(
                        "frozen past-deadline treatment has no resolvable policy deadline"
                    )
                fault_deadline = policy.deadline_world_time
            config = config.model_copy(
                update={
                    "fault_target_ids": tuple(treatment.target_ids),
                    "fault_valid_until_world_time": (treatment.valid_until_world_time),
                    "fault_delivery_world_time": treatment.delivery_world_time,
                    "fault_deadline_world_time": fault_deadline,
                }
            )
        reviewed_paths = petri_net.metadata.get("reviewed_communication_paths", {})
        evidence_actor_ids = {
            path[0]
            for name, path in reviewed_paths.items()
            if name == "agronomic_evidence" and path
        } or ({FIELD_INTELLIGENCE, SCOUTING} & set(actor_ids))
        env = Environment(
            config=EnvironmentConfig(
                start_time=scenario.start_time,
                duration=scenario.duration,
                time_increment_in_seconds=scenario.time_increment_in_seconds,
                oracle_mode=False,
                verbose=False,
            )
        )
        env.register_apps(scenario.apps or [])
        farm_world = scenario.get_typed_app(FarmWorldApp)
        initial_inventory = dict(farm_world.get_state().get("inventory", {}))
        adapter = FarmScenarioAdapter(scenario)
        gateway = RoleToolGateway(env, scenario_tools, team)
        actor_specs = {
            actor.actor_id: actor.model_copy(
                update={
                    "permitted_actions": gateway.permitted_actions(actor.actor_id),
                    "tool_schemas": gateway.permitted_tool_schemas(actor.actor_id),
                }
            )
            for actor in team.actors
        }
        controllers = self._build_controllers(
            config, actor_specs, scenario, petri_net, env, team
        )
        stores = {actor: KnowledgeStore(actor) for actor in actor_ids}
        inboxes: dict[str, list[CausalHandoff | FreeTextEnvelope]] = {
            actor: [] for actor in actor_ids
        }
        run_id = (
            f"{config.scenario_id}:{stable_digest(config.model_dump(mode='json'))[:16]}"
        )
        recorder = CausalTraceRecorder(
            run_id,
            petri_net.net_id,
            actor_ids,
            schema_version=(
                "dcore_trace_v5"
                if config.scientific_contract == "v5"
                else (
                    "dcore_trace_v4"
                    if petri_net.schema_version == "farm_petri_v3"
                    else "dcore_trace_v3"
                )
            ),
            metric_version=(
                "dcore_eval_v5"
                if config.scientific_contract == "v5"
                else (
                    "dcore_eval_v4"
                    if petri_net.schema_version == "farm_petri_v3"
                    else "dcore_eval_v3"
                )
            ),
            team_id=team.team_id,
            team_spec_digest=team_digest(team),
            role_refinement_digest=(
                stable_digest(refinement.model_dump(mode="json"))
                if refinement
                else team.role_refinement_digest
            ),
        )
        transport = InProcessTransport(
            actor_ids,
            schedule=self._fault_schedule(config),
            seed=config.fault_seed,
        )
        for actor, controller in controllers.items():
            controller.initialize(
                actor_specs[actor],
                LocalView(
                    actor=actor_specs[actor],
                    logical_time=0.0,
                    world_time=env.time_manager.time(),
                    knowledge=(),
                    inbox=(),
                    vector_clock=recorder.clock(actor),
                ),
            )

        exogenous_manifest = getattr(
            farm_world.physics, "dcore_exogenous_manifest", None
        ) or {
            "scenario": config.scenario_id,
            "world_seed": config.world_seed,
            "weather_initial": next(
                (
                    app.get_state()
                    for app in scenario.apps or []
                    if app.__class__.__name__ == "WeatherApp"
                ),
                None,
            ),
        }
        exogenous_world_digest = stable_digest(exogenous_manifest)
        logical_time = 0.0
        message_versions: defaultdict[str, int] = defaultdict(int)
        sent_message_count = 0
        provenance_ids: set[str] = set()
        finished: set[str] = set()
        previous_results: dict[str, Any] = {actor: None for actor in actor_ids}
        guard_rejection_count = 0
        blocked_count = deferred_count = recovered_count = harmful_count = 0
        blocked_actions: defaultdict[tuple[str, str, str], int] = defaultdict(int)
        blocked_since: dict[tuple[str, str, str], float] = {}
        blocked_event_ids: defaultdict[tuple[str, str, str], list[str]] = defaultdict(
            list
        )
        recovery_latencies: list[float] = []
        # Errors produced after an agent activation begins are part of the
        # controller treatment and therefore remain in the intention-to-treat
        # denominator.  Setup/provider infrastructure failures escape the run
        # and are classified separately by the matrix harness.
        controller_errors: list[str] = []
        activation_manifest: list[str] = []
        rng = random.Random(config.scheduler_seed)
        committed_branches: dict[str, str] = {}
        branch_evidence: dict[str, tuple[str, ...]] = {}
        committed_world_context: dict[str, Any] = {}

        def deliver_due() -> None:
            nonlocal logical_time
            deliveries = transport.deliver_next(env.time_manager.time())
            for delivery in deliveries:
                envelope = delivery.envelope
                logical_time += 0.001
                already_seen = (
                    envelope.message_id in stores[envelope.recipient].inbox_frontier
                )
                status = (
                    "duplicate" if delivery.copy_index > 0 or already_seen else "ok"
                )
                send_event = next(
                    event
                    for event in reversed(recorder.events)
                    if event.kind == EventKind.MESSAGE_SEND
                    and event.message_id == envelope.message_id
                )
                fact_keys = [
                    claim.fact_key for claim in getattr(envelope, "claims", ())
                ]
                receive_event = recorder.record(
                    EventKind.MESSAGE_RECEIVE,
                    envelope.recipient,
                    logical_time,
                    world_time=env.time_manager.time(),
                    received_clock=envelope.vector_clock,
                    causal_parents=(send_event.event_id,),
                    action=(
                        "farm.resource_receive"
                        if send_event.action == "farm.resource_handoff"
                        else (
                            "farm.coordination_receive"
                            if send_event.action == "farm.coordination_handoff"
                            else "farm.causal_receive"
                        )
                    ),
                    message_id=envelope.message_id,
                    status=status,
                    payload={
                        "fact_keys": fact_keys,
                        "copy_index": delivery.copy_index,
                        "transport_delay_seconds": (
                            env.time_manager.time() - envelope.send_time
                        ),
                    },
                    fact_version=envelope.message_id,
                    season_phase=send_event.season_phase,
                )
                added = stores[envelope.recipient].receive(
                    envelope, env.time_manager.time()
                )
                for item in added:
                    recorder.add_fact_version(
                        FactVersionRecord(
                            version_id=item.item_id,
                            fact_key=item.fact_key,
                            value=item.value,
                            status=item.status,
                            scope=item.scope,
                            source_event_id=receive_event.event_id,
                            origin_version_id=next(
                                (
                                    claim.fact_version_id
                                    for claim in getattr(envelope, "claims", ())
                                    if claim.fact_key == item.fact_key
                                ),
                                None,
                            ),
                            farmare_event_id=None,
                            world_time=item.observed_at,
                            learned_time=item.learned_at,
                            valid_until=item.valid_until,
                            evidence_ids=item.evidence_ids,
                            visible_to=(envelope.recipient,),
                            season_phase=send_event.season_phase,
                            authoritative=False,
                        )
                    )
                if not already_seen:
                    inboxes[envelope.recipient].append(envelope)
                provenance_ids.update(
                    evidence_id for item in added for evidence_id in item.evidence_ids
                )

        step = 0
        while step < config.max_logical_steps:
            step += 1
            deliver_due()
            actor_order = sorted(
                actor_ids,
                key=lambda actor: (
                    team.activation_priorities.get(actor, 0),
                    actor_ids.index(actor),
                ),
            )
            if team.activation_policy == "seeded_permutation":
                rng.shuffle(actor_order)
            elif team.activation_policy == "event_driven":
                eligible = [
                    actor
                    for actor in actor_order
                    if actor not in finished
                    and (
                        inboxes[actor]
                        or bool(previous_results[actor])
                        or not activation_manifest
                    )
                ]
                actor_order = eligible or [
                    actor for actor in actor_order if actor not in finished
                ]
            for actor_id in actor_order:
                if actor_id in finished:
                    continue
                activation_manifest.append(actor_id)
                logical_time += 1.0
                controller = controllers[actor_id]
                snapshot = stores[actor_id].snapshot(
                    logical_time, recorder.clock(actor_id)
                )
                recorder.add_snapshot(snapshot)
                phase_hint = adapter.phase("", env.time_manager.time())
                self._commit_world_branches(
                    petri_net=petri_net,
                    recorder=recorder,
                    adapter=adapter,
                    phase=phase_hint,
                    logical_time=logical_time,
                    world_time=env.time_manager.time(),
                    committed=committed_branches,
                    evidence_by_branch=branch_evidence,
                    world_context=committed_world_context,
                )
                pending_policy = self._information_policy_commitment(
                    petri_net=petri_net,
                    process_spec=process_spec,
                    actor_id=actor_id,
                    phase=phase_hint,
                    knowledge=stores[actor_id],
                    world_time=env.time_manager.time(),
                    # A current delivery watermark is not channel closure: new
                    # messages may still be sent later in the season.
                    channel_closed=False,
                )
                local_view = LocalView(
                    actor=actor_specs[actor_id],
                    logical_time=logical_time,
                    world_time=env.time_manager.time(),
                    knowledge=stores[actor_id].items,
                    inbox=tuple(inboxes[actor_id]),
                    vector_clock=recorder.clock(actor_id),
                    previous_result=previous_results[actor_id],
                )
                try:
                    intent = controller.decide(local_view)
                except Exception as exc:
                    controller_errors.append(f"{actor_id} controller: {exc}")
                    finished.add(actor_id)
                    continue
                phase = getattr(controller, "last_phase", None) or adapter.phase(
                    intent.action or "", env.time_manager.time()
                )
                policy_commitment_id = None
                if pending_policy is not None:
                    commitment_event = recorder.record(
                        EventKind.POLICY_COMMITMENT,
                        actor_id,
                        logical_time,
                        world_time=env.time_manager.time(),
                        action="farm.information_policy_commitment",
                        payload={
                            key: value
                            for key, value in pending_policy.items()
                            if key != "supporting_item_ids"
                        },
                        season_phase=phase_hint,
                    )
                    policy_commitment_id = commitment_event.event_id
                decision = recorder.record(
                    EventKind.DECISION,
                    actor_id,
                    logical_time,
                    world_time=env.time_manager.time(),
                    action=intent.action,
                    payload={
                        "knowledge_digest": snapshot.digest,
                        "intent_kind": intent.kind.value,
                        "inbox_frontier": snapshot.inbox_frontier,
                    },
                    decision_context_id=intent.llm_input_log_id,
                    season_phase=phase,
                )
                if pending_policy is not None and policy_commitment_id is not None:
                    recorder.add_policy_commitment(
                        PolicyCommitmentRecord(
                            commitment_id=policy_commitment_id,
                            policy_id=pending_policy["policy_id"],
                            actor_id=actor_id,
                            decision_id=decision.event_id,
                            logical_time=logical_time,
                            world_time=env.time_manager.time(),
                            knowledge_snapshot_digest=snapshot.digest,
                            requirement_verdicts=pending_policy["requirement_verdicts"],
                            deadline_state=pending_policy["deadline_state"],
                            channel_state=pending_policy["channel_state"],
                            permitted_responses=pending_policy["permitted_responses"],
                            required_responses=pending_policy["required_responses"],
                            specification_digest=pending_policy["specification_digest"],
                            supporting_item_ids=pending_policy["supporting_item_ids"],
                            season_phase=phase_hint,
                            matched_rule_id=pending_policy.get("matched_rule_id"),
                            recomputation_basis_digest=pending_policy.get(
                                "recomputation_basis_digest"
                            ),
                        )
                    )
                guard_result: GuardResult | None = None
                result_payload: dict[str, Any] = {
                    "intent_kind": intent.kind.value,
                    "selected_action": intent.action,
                    "season_phase": phase,
                }

                if intent.kind == IntentKind.SEND:
                    envelopes = self._build_envelopes(
                        config=config,
                        team=team,
                        actor_id=actor_id,
                        intent=intent,
                        stores=stores,
                        message_versions=message_versions,
                        world_time=env.time_manager.time(),
                    )
                    sent_ids = []
                    for envelope in envelopes:
                        sent_message_count += 1
                        if sent_message_count > config.max_messages:
                            raise RuntimeError("team message budget exceeded")
                        claims = tuple(getattr(envelope, "claims", ()))
                        root_message_id = envelope.message_id.split(":to:", 1)[0]
                        send = recorder.record(
                            EventKind.MESSAGE_SEND,
                            actor_id,
                            logical_time + 0.01 + sent_message_count * 0.000001,
                            world_time=env.time_manager.time(),
                            action=(
                                intent.action
                                or (
                                    "farm.resource_handoff"
                                    if actor_id == RESOURCE_MANAGEMENT
                                    else "farm.causal_handoff"
                                )
                            ),
                            causal_parents=(decision.event_id,),
                            evidence_ids=tuple(
                                dict.fromkeys(
                                    evidence
                                    for claim in claims
                                    for evidence in claim.evidence_ids
                                )
                            ),
                            message_id=envelope.message_id,
                            payload={
                                "recipient": envelope.recipient,
                                "root_message_id": root_message_id,
                                "broadcast_size": len(envelopes),
                                "fact_keys": [claim.fact_key for claim in claims],
                                "fact_versions": [
                                    claim.fact_version_id for claim in claims
                                ],
                                "claim_values": {
                                    claim.fact_key: claim.value for claim in claims
                                },
                                "claim_scopes": {
                                    claim.fact_key: claim.scope for claim in claims
                                },
                                "envelope_type": envelope.envelope_type,
                                "unresolved": list(getattr(envelope, "unresolved", ())),
                            },
                            season_phase=phase,
                            fact_version=envelope.message_id,
                        )
                        envelope = envelope.model_copy(
                            update={"vector_clock": send.vector_clock}
                        )
                        sent = transport.send(envelope, env.time_manager.time())
                        sent_ids.append(sent.message_id)
                        if sent.message_id in transport.snapshot()["dropped"]:
                            recorder.events[-1] = send.model_copy(
                                update={"status": "dropped"}
                            )
                    result_payload.update(
                        {
                            "message_id": sent_ids[0] if len(sent_ids) == 1 else None,
                            "message_ids": sent_ids,
                            "executed": True,
                        }
                    )
                elif intent.kind == IntentKind.FINISH:
                    finished.add(actor_id)
                    recorder.record(
                        EventKind.FINISH,
                        actor_id,
                        logical_time + 0.01,
                        world_time=env.time_manager.time(),
                        action="farm.actor_complete",
                        causal_parents=(decision.event_id,),
                        season_phase=phase,
                    )
                    result_payload["executed"] = True
                elif intent.kind == IntentKind.ABSTAIN:
                    recorder.record(
                        EventKind.ACTION,
                        actor_id,
                        logical_time + 0.01,
                        world_time=env.time_manager.time(),
                        action="farm.abstain",
                        causal_parents=(decision.event_id,),
                        decision_context_id=decision.event_id,
                        status="abstained",
                        payload={"reason": intent.text, "write": False},
                        season_phase=phase,
                    )
                    result_payload.update(
                        {"executed": False, "abstained": True, "reason": intent.text}
                    )
                elif intent.kind == IntentKind.WAIT and not intent.action:
                    result_payload.update({"executed": False, "deferred": True})
                else:
                    try:
                        if not intent.action:
                            raise ValueError("tool intent has no action")
                        intent_key = (
                            actor_id,
                            intent.action,
                            stable_digest(intent.args),
                        )
                        metadata = gateway.metadata(intent.action)
                        if metadata["high_impact"]:
                            requirements = self._guard_requirements(
                                petri_net=petri_net,
                                actor_id=actor_id,
                                action=intent.action,
                                args=intent.args,
                                scope=intent.scope or scope_from_args(intent.args),
                                phase=phase,
                                world_time=env.time_manager.time(),
                            )
                            guard_result = self.guard.evaluate(
                                actor=actor_specs[actor_id],
                                action=intent.action,
                                requirements=requirements,
                                knowledge=stores[actor_id],
                                logical_time=env.time_manager.time(),
                                evidence_ids=provenance_ids,
                                transport_closed=transport.watermark(actor_id),
                                transport_gap=(
                                    any(
                                        stores[actor_id].latest(requirement.fact_key)
                                        is None
                                        for requirement in requirements
                                    )
                                    and bool(transport.snapshot()["dropped"])
                                ),
                            )
                            if (
                                config.enforcement_mode == "enforce"
                                and guard_result.verdict == GuardVerdict.DEFER
                                and blocked_actions[intent_key] >= config.max_deferrals
                            ):
                                guard_result = guard_result.model_copy(
                                    update={
                                        "verdict": GuardVerdict.BLOCK,
                                        "reasons": (
                                            *guard_result.reasons,
                                            "maximum deferral count reached",
                                        ),
                                    }
                                )
                            recorder.record(
                                EventKind.GUARD,
                                actor_id,
                                logical_time + 0.01,
                                world_time=env.time_manager.time(),
                                action=intent.action,
                                causal_parents=(decision.event_id,),
                                status=guard_result.verdict.value,
                                payload={
                                    "requirement_verdicts": {
                                        key: value.value
                                        for key, value in guard_result.requirement_verdicts.items()
                                    },
                                    "reasons": guard_result.reasons,
                                    "supporting_item_ids": (
                                        guard_result.supporting_item_ids
                                    ),
                                },
                                season_phase=phase,
                            )
                        should_execute = (
                            guard_result is None
                            or config.enforcement_mode in {"off", "audit"}
                            or guard_result.verdict == GuardVerdict.ALLOW
                        )
                        if not should_execute:
                            status = (
                                "deferred"
                                if guard_result
                                and guard_result.verdict == GuardVerdict.DEFER
                                else "blocked"
                            )
                            guard_rejection_count += 1
                            deferred_count += int(status == "deferred")
                            blocked_count += int(status == "blocked")
                            blocked_actions[intent_key] += 1
                            blocked_since.setdefault(
                                intent_key, env.time_manager.time()
                            )
                            guarded_action = recorder.record(
                                EventKind.ACTION,
                                actor_id,
                                logical_time + 0.02,
                                world_time=env.time_manager.time(),
                                action=intent.action,
                                args=intent.args,
                                causal_parents=(decision.event_id,),
                                decision_context_id=decision.event_id,
                                status=status,
                                payload={
                                    "scope": intent.scope
                                    or scope_from_args(intent.args),
                                    "blocked_before_farmare": True,
                                    "guard_reasons": (
                                        guard_result.reasons if guard_result else ()
                                    ),
                                    "guard_requirement_fact_keys": tuple(
                                        requirement.fact_key
                                        for requirement in requirements
                                    ),
                                },
                                season_phase=phase,
                            )
                            blocked_event_ids[intent_key].append(
                                guarded_action.event_id
                            )
                            result_payload.update(
                                {
                                    "executed": False,
                                    "guard_verdict": (
                                        guard_result.verdict.value
                                        if guard_result
                                        else None
                                    ),
                                    "guard_reasons": (
                                        guard_result.reasons if guard_result else ()
                                    ),
                                }
                            )
                        else:
                            for fact in adapter.authoritative_snapshot(
                                source_event_id=decision.event_id,
                                farmare_event_id=None,
                                action=intent.action,
                                args=intent.args,
                                world_time=env.time_manager.time(),
                                phase=phase,
                            ):
                                recorder.add_fact_version(fact)
                            execution = gateway.execute(
                                actor_id=actor_id,
                                intent_id=decision.event_id,
                                action=intent.action,
                                arguments=intent.args,
                            )
                            completed = execution.completed_event
                            farmare_id = (
                                completed.event_id if completed is not None else None
                            )
                            violated = []
                            if (
                                guard_result is not None
                                and guard_result.verdict != GuardVerdict.ALLOW
                            ):
                                violated = list(guard_result.requirement_verdicts)
                                harmful_count += 1
                            parents = [decision.event_id]
                            for supporting in (
                                guard_result.supporting_item_ids if guard_result else ()
                            ):
                                item = next(
                                    (
                                        entry
                                        for entry in stores[actor_id].items
                                        if entry.item_id == supporting
                                    ),
                                    None,
                                )
                                if item:
                                    parents.extend(item.causal_parents)
                            recovery_latency = None
                            recovery_observations: list[str] = []
                            recovery_receives: list[str] = []
                            if blocked_actions.get(intent_key, 0):
                                recovery_latency = max(
                                    0.0,
                                    env.time_manager.time() - blocked_since[intent_key],
                                )
                                first_rejection_id = blocked_event_ids[intent_key][0]
                                first_rejection_index = next(
                                    index
                                    for index, event in enumerate(recorder.events)
                                    if event.event_id == first_rejection_id
                                )
                                rejected_ids = set(blocked_event_ids[intent_key])
                                required_fact_keys = {
                                    fact_key
                                    for event in recorder.events
                                    if event.event_id in rejected_ids
                                    for fact_key in event.payload.get(
                                        "guard_requirement_fact_keys", ()
                                    )
                                }
                                recovery_observations = [
                                    event.event_id
                                    for event in recorder.events[
                                        first_rejection_index + 1 :
                                    ]
                                    if event.kind == EventKind.OBSERVATION
                                    and event.payload.get("fact_key")
                                    in required_fact_keys
                                ]
                                recovery_receives = [
                                    event.event_id
                                    for event in recorder.events[
                                        first_rejection_index + 1 :
                                    ]
                                    if event.kind == EventKind.MESSAGE_RECEIVE
                                    and event.actor_id == actor_id
                                    and event.status == "ok"
                                    and required_fact_keys.intersection(
                                        event.payload.get("fact_keys", ())
                                    )
                                ]
                            action_event = recorder.record(
                                EventKind.ACTION,
                                actor_id,
                                logical_time + 0.02,
                                world_time=env.time_manager.time(),
                                action=intent.action,
                                args=execution.arguments,
                                causal_parents=tuple(dict.fromkeys(parents)),
                                evidence_ids=(
                                    guard_result.supporting_item_ids
                                    if guard_result
                                    else ()
                                ),
                                decision_context_id=decision.event_id,
                                farmare_event_id=farmare_id,
                                status="error" if execution.error else "ok",
                                payload={
                                    "scope": intent.scope
                                    or scope_from_args(execution.arguments),
                                    "violated_requirements": violated,
                                    "harmful": bool(violated),
                                    "write": metadata["write"],
                                    "high_impact": metadata["high_impact"],
                                    "tool_error": execution.error,
                                    "recovery_latency_seconds": recovery_latency,
                                    "recovery_of_action_event_ids": tuple(
                                        blocked_event_ids[intent_key]
                                    ),
                                    "recovery_observation_event_ids": tuple(
                                        recovery_observations
                                    ),
                                    "recovery_receive_event_ids": tuple(
                                        recovery_receives
                                    ),
                                },
                                season_phase=phase,
                            )
                            if metadata["write"]:
                                recorder.record(
                                    EventKind.WORLD_EFFECT,
                                    "world",
                                    logical_time + 0.025,
                                    world_time=env.time_manager.time(),
                                    action=f"farmare.effect:{intent.action}",
                                    causal_parents=(action_event.event_id,),
                                    farmare_event_id=farmare_id,
                                    status=("error" if execution.error else "ok"),
                                    payload={
                                        "source_action_event_id": (
                                            action_event.event_id
                                        )
                                    },
                                    season_phase=phase,
                                )
                            if metadata["observation"] and not execution.error:
                                self._record_observation_facts(
                                    recorder=recorder,
                                    store=stores[actor_id],
                                    all_stores=stores,
                                    shared=(
                                        config.visibility_mode == "shared_blackboard"
                                        or team.topology.kind == "shared_blackboard"
                                    ),
                                    evidence_actor=actor_id in evidence_actor_ids,
                                    adapter=adapter,
                                    actor_id=actor_id,
                                    action_event_id=action_event.event_id,
                                    farmare_event_id=farmare_id,
                                    action=intent.action,
                                    args=execution.arguments,
                                    result=execution.result,
                                    logical_time=logical_time + 0.03,
                                    world_time=env.time_manager.time(),
                                    phase=phase,
                                    provenance_ids=provenance_ids,
                                )
                            if execution.error:
                                controller_errors.append(execution.error)
                            executed = not execution.error
                            if executed and blocked_actions.get(intent_key, 0):
                                recovered_count += 1
                                recovery_latencies.append(
                                    float(recovery_latency or 0.0)
                                )
                                blocked_actions[intent_key] = 0
                                blocked_since.pop(intent_key, None)
                                blocked_event_ids.pop(intent_key, None)
                            result_payload.update(
                                {
                                    "executed": executed,
                                    "result": execution.result,
                                    "error": execution.error,
                                    "farmare_event_id": farmare_id,
                                    "guard_verdict": (
                                        guard_result.verdict.value
                                        if guard_result
                                        else None
                                    ),
                                }
                            )
                    except Exception as exc:
                        controller_errors.append(f"{actor_id} action: {exc}")
                        result_payload.update({"executed": False, "error": str(exc)})

                metadata = getattr(controller, "last_metadata", {}) or {}
                recorder.add_decision(
                    DecisionRecord(
                        decision_id=decision.event_id,
                        actor_id=actor_id,
                        logical_time=logical_time,
                        knowledge_snapshot=snapshot,
                        proposed_intent=intent,
                        guard=guard_result,
                        prompt_digest=(
                            getattr(controller, "last_prompt_digest", None)
                            or snapshot.digest
                        ),
                        prompt_item_ids=(
                            getattr(controller, "last_prompt_item_ids", ())
                            or snapshot.item_ids
                        ),
                        prompt_message_ids=getattr(
                            controller, "last_prompt_message_ids", ()
                        ),
                        llm_input_log_id=intent.llm_input_log_id,
                        season_phase=phase,
                        response_id=metadata.get("response_id"),
                        model_name=metadata.get("model_name"),
                        model_provider=metadata.get("model_provider"),
                        system_fingerprint=metadata.get("system_fingerprint"),
                        prompt_tokens=metadata.get("prompt_tokens"),
                        completion_tokens=metadata.get("completion_tokens"),
                        total_tokens=metadata.get("total_tokens"),
                        cached_tokens=metadata.get("cached_tokens"),
                        reasoning_tokens=metadata.get("reasoning_tokens"),
                        completion_duration=metadata.get("completion_duration"),
                        retry_count=int(metadata.get("retry_count", 0)),
                        policy_commitment_id=policy_commitment_id,
                    )
                )
                previous_results[actor_id] = result_payload
                controller.observe(result_payload)
                deliver_due()

            if all(controller.is_complete() for controller in controllers.values()):
                break
            if finished == set(actor_ids):
                break
        else:
            controller_errors.append(
                f"max_logical_steps={config.max_logical_steps} reached"
            )

        for actor in actor_ids:
            recorder.record(
                EventKind.WATERMARK,
                actor,
                logical_time + 0.5,
                world_time=env.time_manager.time(),
                action="farm.transport_closed",
                payload={"pending": len(transport.pending_for(actor))},
                season_phase="storage",
            )
        recorder.record(
            EventKind.FINISH,
            "world",
            logical_time + 1.0,
            world_time=env.time_manager.time(),
            action="farm.season_complete",
            season_phase="storage",
        )
        validation = scenario.validate(env)
        outcome = _farm_outcome(farm_world, initial_inventory)
        fault_manifestation = transport.fault_manifestation(config.fault)
        if config.scientific_contract == "v5":
            mode = {
                "delay": "delay_within_validity",
            }.get(config.fault, config.fault)
            recorder.add_fault_manifestation(
                FaultManifestationRecord(
                    fault_id=f"fault:{config.fault}:0",
                    mode=mode,
                    target_ids=tuple(config.fault_target_ids),
                    manifested=bool(fault_manifestation["manifested"]),
                    evidence_event_ids=tuple(
                        event.event_id
                        for event in recorder.events
                        if event.message_id
                        in set(
                            fault_manifestation.get("dropped_message_ids", ())
                            + fault_manifestation.get("duplicate_message_ids", ())
                            + fault_manifestation.get("delayed_message_ids", ())
                        )
                    ),
                    details=fault_manifestation,
                )
            )
        if (
            config.paper_mode
            and config.fault != "none"
            and not fault_manifestation["manifested"]
        ):
            raise RuntimeError(
                f"paper fault treatment {config.fault!r} did not manifest"
            )
        total_model_calls = sum(
            item.llm_input_log_id is not None or item.model_name is not None
            for item in recorder.decisions
        )
        total_model_tokens = sum(item.total_tokens or 0 for item in recorder.decisions)
        total_model_duration = sum(
            item.completion_duration or 0.0 for item in recorder.decisions
        )
        outcome.update(
            {
                "native_scenario_id": scenario.scenario_id,
                "world_seed": config.world_seed,
                "scheduler_seed": config.scheduler_seed,
                "fault_seed": config.fault_seed,
                "fault_seed_applied": False,
                "fault_schedule_type": "named_deterministic",
                "model_seed": config.model_seed,
                "model_seed_applied": False,
                "team_id": team.team_id,
                "team_size": len(actor_ids),
                "team_spec_digest": team_digest(team),
                "role_refinement_digest": recorder.role_refinement_digest,
                "communication_topology": team.topology.kind,
                "activation_policy": team.activation_policy,
                "message_count": sent_message_count,
                "team_call_budget": team.team_call_budget or config.max_model_calls,
                "per_agent_call_budget": team.per_agent_call_budget,
                "team_token_budget": team.team_token_budget,
                "per_agent_token_budget": team.per_agent_token_budget,
                "total_model_calls": total_model_calls,
                "total_model_tokens": total_model_tokens,
                "total_model_completion_duration_seconds": total_model_duration,
                "call_budget_exhausted": total_model_calls
                >= (team.team_call_budget or config.max_model_calls),
                "token_budget_exhausted": bool(
                    team.team_token_budget is not None
                    and total_model_tokens >= team.team_token_budget
                ),
                "token_budget_overshoot": (
                    max(0, total_model_tokens - team.team_token_budget)
                    if team.team_token_budget is not None
                    else None
                ),
                "token_budget_policy": "stop_before_next_model_call",
                "exogenous_world_digest": exogenous_world_digest,
                "exogenous_world_days": len(exogenous_manifest.get("weather_days", [])),
                "effective_weather_seed": exogenous_manifest.get(
                    "effective_weather_seed"
                ),
                "schedule_id": stable_digest(activation_manifest)[:16],
                "activation_manifest": activation_manifest,
                "transport": transport.snapshot(),
                "fault_manifestation": fault_manifestation,
                "fault_manifested": fault_manifestation["manifested"],
                "blocked_write_count": blocked_count,
                "guard_rejection_count": guard_rejection_count,
                "blocked_intent_count": len(blocked_actions),
                "deferred_write_count": deferred_count,
                "recovered_write_count": recovered_count,
                "recovery_latencies_seconds": recovery_latencies,
                "harmful_write_count": harmful_count,
                "safety_violation_count": harmful_count,
                "guard_abstention_count": sum(
                    count > 0 for count in blocked_actions.values()
                ),
                "controller_errors": controller_errors,
                "controller_failure": bool(controller_errors),
                "infrastructure_errors": [],
                "safety_success": harmful_count == 0,
                "farmare_task_validation": {
                    "success": validation.success,
                    "exception": (
                        str(validation.exception) if validation.exception else None
                    ),
                    "rationale": validation.rationale,
                },
            }
        )
        outcome["success"] = bool(
            outcome["harvest_complete"]
            and outcome["storage_complete"]
            and validation.success is True
            and not controller_errors
        )
        trace = recorder.build(
            configuration={
                **config.model_dump(mode="json"),
                "runtime_semantics": "agent_driven_native_tools_v3",
                "team_spec": team.model_dump(mode="json"),
                "team_spec_digest": team_digest(team),
                "team_size": len(actor_ids),
                "topology_edges": list(team.topology.edges),
                "activation_policy_resolved": team.activation_policy,
                "role_refinement": (
                    refinement.model_dump(mode="json") if refinement else None
                ),
                "controller_task_briefing_policy": (
                    "nonprocedural_public_task_contract_v1"
                ),
                "controller_task_briefing_digest": stable_digest(
                    self._task_briefing(config.scenario_id)
                ),
                "exogenous_world_digest": exogenous_world_digest,
                "exogenous_world_manifest": exogenous_manifest,
                "committed_branches": committed_branches,
                "branch_commitment_evidence": branch_evidence,
                "committed_world_context": committed_world_context,
                "controller_adapter": (
                    "native_base_agent_step_v1"
                    if config.controller_mode in {"llm", "mock_llm"}
                    else "dcore_agent_controller_v1"
                ),
                "oracle_visible_to_controller": (
                    config.controller_mode in {"scripted", "mock_llm"}
                ),
            },
            outcome=outcome,
            source_trace=(
                f"{config.output_dir}/farmare_trace.json" if config.output_dir else None
            ),
        )
        farmare_trace_json = JsonScenarioExporter().export_to_json(
            env,
            scenario,
            scenario.scenario_id,
            model_id="dcore",
            agent_id="distributed",
        )
        occurrence = unfold_petri_net(
            petri_net,
            world_context=committed_world_context,
            world_fingerprint=exogenous_world_digest,
            committed_branches=committed_branches,
        )
        return NativeSeasonExecution(
            scenario=scenario,
            environment=env,
            petri_net=petri_net,
            occurrence_net=occurrence,
            trace=trace,
            farmare_trace_json=farmare_trace_json,
            process_spec=process_spec,
        )

    @staticmethod
    def _commit_world_branches(
        *,
        petri_net: PetriNetSpec,
        recorder: CausalTraceRecorder,
        adapter: FarmScenarioAdapter,
        phase: str,
        logical_time: float,
        world_time: float,
        committed: dict[str, str],
        evidence_by_branch: dict[str, tuple[str, ...]],
        world_context: dict[str, Any],
    ) -> None:
        """Commit branches from current authoritative truth before action choice."""

        pending = [
            branch
            for branch in petri_net.exogenous_branches
            if isinstance(branch, WorldBranchSpec)
            and branch.branch_id not in committed
            and branch.commit_phase == phase
        ]
        if not pending:
            return
        snapshot_event = recorder.record(
            EventKind.OBSERVATION,
            "world",
            logical_time + 0.0001,
            world_time=world_time,
            action="farm.authoritative_branch_snapshot",
            season_phase=phase,
        )
        records = adapter.authoritative_snapshot(
            source_event_id=snapshot_event.event_id,
            farmare_event_id=None,
            action="farm.authoritative_branch_snapshot",
            args={},
            world_time=world_time,
            phase=phase,
        )
        latest: dict[str, FactVersionRecord] = {}
        for fact in records:
            recorder.add_fact_version(fact)
            latest[fact.fact_key] = recorder.fact_versions[-1]
            world_context[fact.fact_key] = fact.value

        operations = {
            "eq": lambda actual, expected: actual == expected,
            "ne": lambda actual, expected: actual != expected,
            "ge": lambda actual, expected: actual >= expected,
            "gt": lambda actual, expected: actual > expected,
            "le": lambda actual, expected: actual <= expected,
            "lt": lambda actual, expected: actual < expected,
            "in": lambda actual, expected: actual in expected,
        }
        for branch in pending:
            if not set(branch.commitment_fact_keys) <= set(latest):
                continue

            def holds(alternative: Any) -> bool:
                try:
                    return bool(alternative.guards) and all(
                        guard.fact_key in latest
                        and operations[guard.operator.value](
                            latest[guard.fact_key].value, guard.expected
                        )
                        for guard in alternative.guards
                    )
                except (TypeError, ValueError):
                    return False

            matches = [item for item in branch.alternatives if holds(item)]
            if len(matches) > 1:
                raise ValueError(f"ambiguous world branch {branch.branch_id!r}")
            selected = (
                matches[0]
                if matches
                else next((item for item in branch.alternatives if item.default), None)
            )
            if selected is None:
                raise ValueError(
                    f"authoritative facts do not resolve world branch {branch.branch_id!r}"
                )
            evidence = tuple(
                latest[key].version_id for key in branch.commitment_fact_keys
            )
            commitment_event = recorder.record(
                EventKind.BRANCH_COMMITMENT,
                "world",
                logical_time + 0.0002,
                world_time=world_time,
                action="farm.world_branch_commitment",
                causal_parents=tuple(
                    latest[key].source_event_id for key in branch.commitment_fact_keys
                ),
                evidence_ids=tuple(
                    latest[key].source_event_id for key in branch.commitment_fact_keys
                ),
                payload={
                    "branch_id": branch.branch_id,
                    "alternative_id": selected.alternative_id,
                },
                season_phase=phase,
            )
            context_digest = stable_digest(
                {key: latest[key].value for key in sorted(branch.commitment_fact_keys)}
            )
            recorder.add_world_branch_commitment(
                WorldBranchCommitmentRecord(
                    commitment_id=commitment_event.event_id,
                    branch_id=branch.branch_id,
                    alternative_id=selected.alternative_id,
                    logical_time=logical_time,
                    world_time=world_time,
                    evidence_fact_version_ids=evidence,
                    world_context_digest=context_digest,
                    specification_digest=stable_digest(branch.model_dump(mode="json")),
                )
            )
            committed[branch.branch_id] = selected.alternative_id
            evidence_by_branch[branch.branch_id] = evidence

    @staticmethod
    def _record_observation_facts(
        *,
        recorder: CausalTraceRecorder,
        store: KnowledgeStore,
        all_stores: dict[str, KnowledgeStore],
        shared: bool,
        evidence_actor: bool,
        adapter: FarmScenarioAdapter,
        actor_id: str,
        action_event_id: str,
        farmare_event_id: str | None,
        action: str,
        args: dict[str, Any],
        result: Any,
        logical_time: float,
        world_time: float,
        phase: str,
        provenance_ids: set[str],
    ) -> None:
        facts = adapter.extract_observed(
            action=action, args=args, result=result, phase=phase
        )
        if not evidence_actor:
            # Operations reads (inventory, tractor status) are useful local
            # facts but cannot stand in for intelligence-owned agronomic
            # evidence. Cross-role writes therefore require a delivered claim
            # in local-visibility conditions.
            facts = tuple(
                fact for fact in facts if not fact.key.startswith("phase_evidence:")
            )
        for index, fact in enumerate(facts):
            observation = recorder.record(
                EventKind.OBSERVATION,
                actor_id,
                logical_time + index * 0.0001,
                world_time=world_time,
                action=action,
                causal_parents=(action_event_id,),
                farmare_event_id=farmare_event_id,
                payload={
                    "fact_key": fact.key,
                    "value": fact.value,
                    "scope": fact.scope,
                    "valid_until": (
                        world_time + fact.valid_for if fact.valid_for else None
                    ),
                },
                season_phase=phase,
            )
            version_id = f"fact:{stable_digest((observation.event_id, fact.key))[:20]}"
            recorder.events[-1] = observation.model_copy(
                update={"fact_version": version_id}
            )
            item = KnowledgeItem(
                item_id=version_id,
                fact_key=fact.key,
                value=fact.value,
                scope=fact.scope,
                status=fact.status,
                source_actor=actor_id,
                evidence_ids=(observation.event_id,),
                observed_at=world_time,
                learned_at=world_time,
                valid_until=(world_time + fact.valid_for if fact.valid_for else None),
                causal_parents=(observation.event_id,),
                vector_clock=observation.vector_clock,
            )
            store.add(item)
            visible_to = [actor_id]
            if shared:
                for other_actor, other_store in all_stores.items():
                    if other_actor == actor_id:
                        continue
                    other_store.add(
                        item.model_copy(
                            update={
                                "item_id": f"blackboard:{other_actor}:{version_id}",
                                "status": EpistemicStatus.CLAIMED,
                                "learned_at": world_time,
                            }
                        )
                    )
                    visible_to.append(other_actor)
            authoritative_origin = max(
                (
                    item
                    for item in recorder.fact_versions
                    if item.authoritative
                    and item.fact_key == fact.key
                    and item.world_time <= world_time
                ),
                key=lambda item: (item.world_time, item.version_id),
                default=None,
            )
            recorder.add_fact_version(
                FactVersionRecord(
                    version_id=version_id,
                    fact_key=fact.key,
                    value=fact.value,
                    status=fact.status,
                    scope=fact.scope,
                    source_event_id=observation.event_id,
                    origin_version_id=(
                        authoritative_origin.version_id
                        if authoritative_origin is not None
                        else None
                    ),
                    farmare_event_id=farmare_event_id,
                    world_time=world_time,
                    learned_time=world_time,
                    valid_until=(
                        world_time + fact.valid_for if fact.valid_for else None
                    ),
                    evidence_ids=(observation.event_id,),
                    visible_to=tuple(visible_to),
                    season_phase=phase,
                    authoritative=False,
                )
            )
            provenance_ids.add(observation.event_id)

    @staticmethod
    def _build_envelopes(
        *,
        config: DistributedRunnerConfig,
        team: AgentTeamSpec,
        actor_id: str,
        intent: AgentIntent,
        stores: dict[str, KnowledgeStore],
        message_versions: defaultdict[str, int],
        world_time: float,
    ) -> tuple[CausalHandoff | FreeTextEnvelope, ...]:
        requested = intent.recipients
        if intent.recipient == "*":
            requested = recipients_for(team, actor_id)
        elif intent.recipient:
            requested = (*requested, intent.recipient)
        if not requested:
            requested = recipients_for(team, actor_id)
        requested = tuple(dict.fromkeys(requested))
        if not requested:
            raise ValueError(f"actor {actor_id!r} has no permitted message recipient")
        for recipient in requested:
            if not can_send(team, actor_id, recipient):
                raise PermissionError(
                    f"topology forbids message {actor_id!r}->{recipient!r}"
                )
        phase = "unknown"
        if intent.claim_fact_keys:
            phase = intent.claim_fact_keys[0].removeprefix("phase_evidence:")
        message_versions[phase] += 1
        root_message_id = f"handoff:{phase}:v{message_versions[phase]}"
        claims = []
        unresolved = list(intent.unresolved_requirements)
        for fact_key in intent.claim_fact_keys:
            item = stores[actor_id].latest(fact_key)
            if item is None:
                unresolved.append(fact_key)
                continue
            claims.append(
                Claim(
                    fact_key=item.fact_key,
                    value=item.value,
                    fact_version_id=item.item_id,
                    scope=item.scope,
                    status=item.status,
                    confidence=item.confidence,
                    observed_at=item.observed_at,
                    valid_until=item.valid_until,
                    evidence_ids=item.evidence_ids,
                    causal_parents=item.causal_parents,
                )
            )
        envelopes: list[CausalHandoff | FreeTextEnvelope] = []
        for recipient in requested:
            message_id = (
                root_message_id
                if len(requested) == 1
                else f"{root_message_id}:to:{recipient}"
            )
            if config.handoff_mode == "free_text":
                envelopes.append(
                    FreeTextEnvelope(
                        message_id=message_id,
                        sender=actor_id,
                        recipient=recipient,
                        text=intent.text,
                        send_time=world_time,
                    )
                )
            else:
                envelopes.append(
                    CausalHandoff(
                        message_id=message_id,
                        sender=actor_id,
                        recipient=recipient,
                        text=intent.text,
                        claims=tuple(claims),
                        unresolved=tuple(dict.fromkeys(unresolved)),
                        send_time=world_time,
                    )
                )
        return tuple(envelopes)

    @staticmethod
    def _fault_schedule(config: DistributedRunnerConfig) -> FaultSchedule:
        if config.fault == "none":
            return FaultSchedule()
        delay = config.delay or 12 * 3600
        scheduled_delay = 0.0 if config.fault_delivery_world_time is not None else delay
        rules = {
            "delay": FaultRule(
                FaultMode.DELAY,
                delay=scheduled_delay,
                valid_until_world_time=config.fault_valid_until_world_time,
                delivery_world_time=config.fault_delivery_world_time,
            ),
            "delay_within_validity": FaultRule(
                FaultMode.DELAY,
                delay=scheduled_delay,
                valid_until_world_time=config.fault_valid_until_world_time,
                delivery_world_time=config.fault_delivery_world_time,
            ),
            "delay_past_validity": FaultRule(
                FaultMode.DELAY,
                delay=(
                    scheduled_delay
                    if config.fault_delivery_world_time is not None
                    else max(delay, 4 * 86400)
                ),
                valid_until_world_time=config.fault_valid_until_world_time,
                delivery_world_time=config.fault_delivery_world_time,
            ),
            "delay_past_deadline": FaultRule(
                FaultMode.DELAY,
                delay=(
                    scheduled_delay
                    if config.fault_delivery_world_time is not None
                    else max(delay, 4 * 86400)
                ),
                deadline_world_time=config.fault_deadline_world_time,
                delivery_world_time=config.fault_delivery_world_time,
            ),
            "drop": FaultRule(FaultMode.DROP),
            "duplicate": FaultRule(FaultMode.DUPLICATE, duplicate_delay=0.0),
            "reorder": FaultRule(
                FaultMode.REORDER,
                delay=max(delay, 4 * 86400),
                reorder_bias=1.0,
            ),
        }
        rule = rules.get(config.fault)
        if config.fault == "mixed":
            return FaultSchedule(
                by_message_prefix={
                    # Frozen semantic targets span two disease decisions and
                    # harvest. Prefixes name semantic phases and remain stable
                    # when different controllers send different message counts.
                    "handoff:midseason:": FaultRule(FaultMode.DELAY, delay=4 * 86400),
                    "handoff:r5:": FaultRule(FaultMode.DROP),
                    "handoff:harvest:": FaultRule(FaultMode.DUPLICATE),
                }
            )
        assert rule is not None
        if config.fault == "reorder":
            targets = config.fault_target_ids or (
                "handoff:midseason:v1",
                "handoff:midseason:v2",
            )
            if len(targets) != 2 or any(
                target.startswith(("fact:", "world:", "prefix:")) for target in targets
            ):
                raise ValueError(
                    "reorder requires exactly two stable message IDs: older, newer"
                )
            return FaultSchedule(
                by_message_id={
                    targets[0]: FaultRule(
                        FaultMode.REORDER,
                        delay=max(delay, 4 * 86400),
                        reorder_bias=1.0,
                    ),
                    targets[1]: FaultRule(),
                }
            )
        if config.fault_target_ids:
            return FaultSchedule(
                by_message_id={
                    target: rule
                    for target in config.fault_target_ids
                    if not target.startswith(("fact:", "world:"))
                    and not target.startswith("prefix:")
                },
                by_message_prefix={
                    target.removeprefix("prefix:"): rule
                    for target in config.fault_target_ids
                    if target.startswith("prefix:")
                },
                by_fact_version={
                    target: rule
                    for target in config.fault_target_ids
                    if target.startswith(("fact:", "world:"))
                },
            )
        # The default single-fault ablation targets all evidence bundles in a
        # coordination-critical semantic phase, not a controller-dependent
        # send index.
        return FaultSchedule(by_message_prefix={"handoff:midseason:": rule})

    def _build_controllers(
        self,
        config: DistributedRunnerConfig,
        actor_specs: dict[str, ActorSpec],
        scenario: Scenario,
        petri_net: PetriNetSpec,
        env: Environment,
        team: AgentTeamSpec,
    ) -> dict[str, Any]:
        actor_ids = tuple(actor_specs)
        if self.controllers is not None:
            if set(self.controllers) != set(actor_ids):
                raise ValueError("native season requires one controller per farm actor")
            return self.controllers
        if config.controller_mode in {"scripted", "mock_llm"}:
            coordinator = OracleCeilingCoordinator(
                self._oracle_ceiling_steps(scenario, petri_net, team)
            )
            if config.controller_mode == "mock_llm":
                from are.simulation.agents.agent_builder import AgentBuilder
                from are.simulation.agents.agent_config_builder import (
                    AgentConfigBuilder,
                )
                from are.simulation.distributed.controllers import (
                    CoordinatedMockReactEngine,
                    FarmAREBaseAgentController,
                )

                class _MockEngineBuilder:
                    def __init__(self, engine):
                        self.engine = engine

                    def create_engine(self, engine_config, mock_responses=None):
                        return self.engine

                task_briefing = self._task_briefing(config.scenario_id)
                built = {}
                for actor, spec in actor_specs.items():
                    engine = CoordinatedMockReactEngine(actor, coordinator)
                    family = config.agent_family_by_actor.get(actor, "default")
                    agent_config = AgentConfigBuilder().build(family)
                    base_config = agent_config.get_base_agent_config()
                    base_config.use_custom_logger = False
                    base_config.history_window = config.history_window_by_actor.get(
                        actor, 8
                    )
                    base_config.system_prompt = self._distributed_prompt(
                        str(base_config.system_prompt), spec, task_briefing, team
                    )
                    farmare_agent = AgentBuilder(_MockEngineBuilder(engine)).build(
                        agent_config, env=env
                    )
                    built[actor] = FarmAREBaseAgentController(
                        farmare_agent,
                        max_decisions=config.max_logical_steps,
                        max_model_calls=config.max_logical_steps,
                        max_total_tokens=team.per_agent_token_budget.get(actor),
                    )
                return built
            return {
                actor: OracleCeilingController(
                    actor,
                    coordinator,
                    llm_style=False,
                )
                for actor in actor_ids
            }
        if config.controller_mode == "replay":
            from are.simulation.distributed.controllers import ReplayController

            payload = json.loads(
                Path(config.replay_trace or "").read_text(encoding="utf-8")
            )
            replay = DistributedTrace.model_validate(payload)
            return {
                actor: ReplayController(
                    decision.proposed_intent
                    for decision in replay.decisions
                    if decision.actor_id == actor
                    and decision.proposed_intent.kind != IntentKind.WAIT
                )
                for actor in actor_ids
            }
        if config.controller_mode == "llm":
            from are.simulation.agents.agent_builder import AgentBuilder
            from are.simulation.agents.agent_config_builder import (
                AgentConfigBuilder,
            )
            from are.simulation.agents.are_simulation_agent_config import (
                LLMEngineConfig,
            )
            from are.simulation.agents.llm.llm_engine_builder import (
                LLMEngineBuilder,
            )
            from are.simulation.distributed.controllers import (
                FarmAREBaseAgentController,
            )

            engines = LLMEngineBuilder()
            families = AgentConfigBuilder()
            built = {}
            task_briefing = self._task_briefing(config.scenario_id)
            team_call_budget = team.team_call_budget or config.max_model_calls
            team_token_budget = team.team_token_budget
            for actor, spec in actor_specs.items():
                model = config.model_by_actor.get(actor)
                if not model:
                    raise ValueError(f"LLM mode requires model_by_actor[{actor!r}]")
                provider = config.provider_by_actor.get(actor, "openai")
                engine_provider = "openai-json" if provider == "openai" else provider
                family = config.agent_family_by_actor.get(actor, "default")
                agent_config = families.build(agent_name=family)
                base_config = agent_config.get_base_agent_config()
                base_config.use_custom_logger = False
                base_config.llm_engine_config = LLMEngineConfig(
                    model_name=model,
                    provider=engine_provider,
                    endpoint=config.endpoint_by_actor.get(actor),
                    temperature=config.temperature_by_actor.get(actor, 0.0),
                )
                base_config.history_window = config.history_window_by_actor.get(
                    actor, 8
                )
                base_config.system_prompt = self._distributed_prompt(
                    str(getattr(base_config, "system_prompt", "")),
                    spec,
                    task_briefing,
                    team,
                )
                farmare_agent = AgentBuilder(llm_engine_builder=engines).build(
                    agent_config, env=env
                )
                engine = farmare_agent.llm_engine
                if hasattr(engine, "model_config"):
                    engine.model_config.max_tokens = config.max_output_tokens
                built[actor] = FarmAREBaseAgentController(
                    farmare_agent,
                    max_decisions=team.per_agent_call_budget.get(
                        actor,
                        max(1, team_call_budget // len(actor_ids)),
                    ),
                    max_model_calls=team.per_agent_call_budget.get(
                        actor,
                        max(1, team_call_budget // len(actor_ids)),
                    ),
                    max_total_tokens=team.per_agent_token_budget.get(
                        actor,
                        (
                            max(1, team_token_budget // len(actor_ids))
                            if team_token_budget is not None
                            else None
                        ),
                    ),
                )
            return built
        raise ValueError(f"unsupported controller mode {config.controller_mode!r}")

    @staticmethod
    def _task_briefing(scenario_id: str) -> str:
        """Return the public task contract, never the procedural oracle briefing.

        Native L3 briefing events contain expert actions, exact treatment
        amounts, and an ordered solution. They remain available to the oracle
        ceiling and evaluator, but exposing them to model controllers would
        invalidate an agent-driven planning experiment.
        """

        briefings = {
            "farm_wetjune_recheck": (
                "Manage a 64-ridge HEINONG84 soybean field from pre-planting "
                "through safe storage. The season may include wet-June disease "
                "pressure and disease recurrence after treatment. Diagnose from "
                "owned observations, communicate scoped and fresh evidence, and "
                "choose planting, monitoring, intervention, harvest, drying, and "
                "storage actions from the current farm state."
            ),
            "farm_disease_drought": (
                "Manage a 64-ridge HEINONG84 soybean field through safe storage. "
                "Disease pressure may be followed by reproductive-stage drought; "
                "distinguish recurrence from water stress using owned evidence, "
                "coordinate scoped operations, and adapt the seasonal plan to the "
                "observed farm state and available resources."
            ),
            "farm_three_cultivar": (
                "Manage a 64-ridge soybean field with HEIHE50 on ridges 0-20, "
                "HEINONG84 on 21-42, and HEINONG58 on 43-63 through safe storage. "
                "The zones may differ in disease, water stress, maturity, and "
                "harvest readiness. Maintain zone-specific evidence and coordinate "
                "planting, interventions, harvest, drying, and storage without "
                "assuming unobserved conditions."
            ),
        }
        try:
            return briefings[scenario_id]
        except KeyError as error:
            raise ValueError(
                f"no non-procedural task briefing for {scenario_id!r}"
            ) from error

    @staticmethod
    def _distributed_prompt(
        base_prompt: str,
        spec: ActorSpec,
        task_briefing: str,
        team: AgentTeamSpec,
    ) -> str:
        recipients = recipients_for(team, spec.actor_id)
        time_rule = (
            "You may advance farm time."
            if spec.actor_id in team.time_authority
            else "You may not advance farm time."
        )
        return (
            base_prompt
            + "\n\n<distributed_role>\n"
            + spec.role
            + ". You have persistent local history and only the listed tools. "
            "Never assume unobserved facts. "
            + time_rule
            + " Your permitted message recipients are: "
            + ", ".join(recipients)
            + ". "
            "In each ReAct step, call exactly one listed tool. Farm tools "
            "propose their exact arguments but do not execute inside the agent; "
            "the D-CORE runtime validates and returns the result. Use dcore_send "
            "only for fact keys in local knowledge, dcore_wait when evidence is "
            "missing, and dcore_finish only after your seasonal duties are "
            "complete.\n</distributed_role>"
            + "\n\n<farm_task>\n"
            + task_briefing
            + "\n</farm_task>"
        )

    @staticmethod
    def _guard_requirements(
        *,
        petri_net: PetriNetSpec,
        actor_id: str,
        action: str,
        args: dict[str, Any],
        scope: tuple[int, int] | str | None,
        phase: str,
        world_time: float,
    ) -> tuple[FactRequirement, ...]:
        """Project the frozen Petri safety policy onto a proposed action.

        This lookup is internal to the guard. It neither chooses an action nor
        exposes a reference transition to the controller.
        """

        candidates = [
            transition
            for transition in petri_net.transitions
            if transition.high_impact
            and transition.actor_id == actor_id
            and transition.action == action
        ]
        phase_candidates = [
            transition for transition in candidates if transition.phase == phase
        ]
        if phase_candidates:
            candidates = phase_candidates

        def compatibility(transition: Any) -> tuple[int, int]:
            exact = sum(
                args.get(constraint.name) == constraint.expected
                for constraint in transition.arguments
            )
            return exact, -abs(len(transition.arguments) - len(args))

        selected = max(candidates, key=compatibility, default=None)
        if selected is None:
            evidence_phase = "harvest" if phase == "storage" else phase
            return (
                FactRequirement(
                    requirement_id=f"guard:{phase}:{action}:evidence",
                    actor_id=actor_id,
                    action=action,
                    fact_key=f"phase_evidence:{evidence_phase}",
                    scope=scope,
                    max_age=3 * 86400,
                    deadline=world_time + 2 * 86400,
                    require_evidence=True,
                ),
            )
        return tuple(
            FactRequirement(
                requirement_id=guard.guard_id,
                actor_id=actor_id,
                action=action,
                fact_key=guard.fact_key,
                expected_value=guard.expected,
                operator=guard.operator.value,
                scope=scope,
                max_age=guard.max_age,
                deadline=(selected.window_end or world_time + 2 * 86400),
                require_evidence=guard.required_evidence,
            )
            for guard in selected.guards
            if guard.source == "knowledge"
        )

    @staticmethod
    def _information_policy_commitment(
        *,
        petri_net: PetriNetSpec,
        process_spec: Any | None = None,
        actor_id: str,
        phase: str,
        knowledge: KnowledgeStore,
        world_time: float,
        channel_closed: bool,
        deadline_closed: bool | None = None,
    ) -> dict[str, Any] | None:
        """Commit from K_i(t) before the controller proposes an intent."""
        v5 = process_spec is not None
        policies = (
            tuple(process_spec.information_policies)
            if v5
            else tuple(
                InformationPolicySpec.model_validate(item)
                for item in petri_net.metadata.get("information_policies", [])
            )
        )
        policy = next(
            (
                item
                for item in policies
                if item.actor_id == actor_id and phase in item.phases
            ),
            None,
        )
        if policy is None:
            return None
        verdicts: dict[str, RequirementVerdict] = {}
        supporting: list[str] = []
        requirements = policy.requirements or tuple(
            DataGuardSpec(
                guard_id=f"policy:{policy.policy_id}:{fact_key}",
                fact_key=fact_key,
                source="knowledge",
                expected=True,
                required_evidence=True,
            )
            for fact_key in policy.requirement_fact_keys
        )
        operations = {
            "eq": lambda actual, expected: actual == expected,
            "ne": lambda actual, expected: actual != expected,
            "ge": lambda actual, expected: actual >= expected,
            "gt": lambda actual, expected: actual > expected,
            "le": lambda actual, expected: actual <= expected,
            "lt": lambda actual, expected: actual < expected,
            "in": lambda actual, expected: actual in expected,
        }
        for requirement in requirements:
            fact_key = requirement.fact_key
            item = knowledge.latest(fact_key)
            if item is None:
                verdicts[fact_key] = RequirementVerdict.UNKNOWN
                continue
            supporting.append(item.item_id)
            stale = item.valid_until is not None and world_time > item.valid_until
            unsupported = requirement.required_evidence and not item.evidence_ids
            try:
                contradicted = not operations[requirement.operator.value](
                    item.value, requirement.expected
                )
            except (TypeError, ValueError):
                contradicted = True
            verdicts[fact_key] = (
                RequirementVerdict.UNKNOWN
                if v5 and (stale or unsupported)
                else (
                    RequirementVerdict.FALSE
                    if stale or unsupported or contradicted
                    else RequirementVerdict.TRUE
                )
            )
        aggregate = (
            "false"
            if RequirementVerdict.FALSE in verdicts.values()
            else (
                "unknown" if RequirementVerdict.UNKNOWN in verdicts.values() else "true"
            )
        )
        # The agronomic deadline and transport watermark are distinct. Draft
        # engineering policies omit a deadline and therefore remain open;
        # paper mode requires the reviewed policy to supply one.
        if deadline_closed is None:
            deadline_closed = (
                policy.deadline_world_time is not None
                and world_time >= policy.deadline_world_time
            )
        deadline_state = "closed" if deadline_closed else "open"
        channel_state = "closed" if channel_closed else "open"
        if v5:
            from are.simulation.distributed.scientific_v5 import select_policy_rule

            rule = select_policy_rule(
                policy,
                {key: verdict.value for key, verdict in verdicts.items()},
                deadline_state,
                channel_state,
            )
        else:
            rule = next(
                item
                for item in policy.rules
                if item.requirement_state == aggregate
                and item.deadline_state == deadline_state
                and item.channel_state in {channel_state, "any"}
            )
        basis = {
            "policy_id": policy.policy_id,
            "requirement_verdicts": {
                key: value.value for key, value in sorted(verdicts.items())
            },
            "deadline_state": deadline_state,
            "channel_state": channel_state,
            "supporting_item_ids": sorted(supporting),
        }
        return {
            "policy_id": policy.policy_id,
            "requirement_verdicts": verdicts,
            "deadline_state": deadline_state,
            "channel_state": channel_state,
            "permitted_responses": tuple(
                response.value for response in rule.permitted_responses
            ),
            "required_responses": tuple(
                response.value for response in rule.required_responses
            ),
            "supporting_item_ids": tuple(supporting),
            "matched_rule_id": getattr(rule, "rule_id", None),
            "recomputation_basis_digest": stable_digest(basis),
            "specification_digest": str(
                process_spec.digest
                if v5
                else petri_net.metadata.get("hierarchical_template_digest", "draft")
            ),
        }

    @staticmethod
    def _oracle_ceiling_steps(
        scenario: Scenario,
        petri_net: PetriNetSpec,
        team: AgentTeamSpec | None = None,
    ) -> tuple[tuple[str, str, AgentIntent], ...]:
        """Compile the declared ceiling controller; the runtime never reads it."""

        if team is None:
            # Compatibility for engineering fixtures that inspect the declared
            # ceiling directly. Runtime execution always supplies its resolved
            # team explicitly.
            team = build_builtin_team(PRIMARY_TEAM_ID, scenario.get_tools())

        by_id = {
            transition.transition_id: transition for transition in petri_net.transitions
        }
        policies = tuple(
            InformationPolicySpec.model_validate(item)
            for item in petri_net.metadata.get("information_policies", [])
        )
        steps: list[tuple[str, str, AgentIntent]] = []
        previous_actor: str | None = None
        for oracle in scenario.events:
            if not isinstance(oracle, OracleEvent):
                continue
            event = oracle.make_event(None)
            action = getattr(event, "action", None)
            if (
                not isinstance(action, Action)
                or action.class_name == "AgentUserInterface"
            ):
                continue
            transition = by_id.get(oracle.event_id)
            if transition is None:
                continue
            if (
                team.team_id == FOUR_AGENT_TEAM_ID
                and previous_actor is not None
                and previous_actor != transition.actor_id
            ):
                coordination_path = shortest_path(
                    team, previous_actor, transition.actor_id
                )
                for sender, recipient in zip(
                    coordination_path[:-1], coordination_path[1:], strict=True
                ):
                    steps.append(
                        (
                            sender,
                            transition.phase,
                            AgentIntent(
                                kind=IntentKind.SEND,
                                action="farm.coordination_handoff",
                                recipient=recipient,
                                text="Resource/operation sequence synchronization.",
                            ),
                        )
                    )
            if transition.high_impact:
                policy_fact_keys = tuple(
                    fact_key
                    for policy in policies
                    if transition.actor_id == policy.actor_id
                    and transition.phase in policy.phases
                    and transition.action in policy.action_patterns
                    for fact_key in policy.requirement_fact_keys
                )
                fact_keys = tuple(
                    dict.fromkeys(
                        (
                            *(
                                guard.fact_key
                                for guard in transition.guards
                                if guard.source == "knowledge"
                            ),
                            *policy_fact_keys,
                        )
                    )
                )
                declared_paths = petri_net.metadata.get(
                    "reviewed_communication_paths", {}
                )
                declared = tuple(declared_paths.get("agronomic_evidence", ()))
                evidence_actor = (
                    declared[0]
                    if declared
                    else (
                        FIELD_INTELLIGENCE
                        if team.team_id == PRIMARY_TEAM_ID
                        else SCOUTING
                    )
                )
                path = (
                    declared
                    if declared and declared[-1] == transition.actor_id
                    else shortest_path(team, evidence_actor, transition.actor_id)
                )
                for sender, recipient in zip(path[:-1], path[1:], strict=True):
                    steps.append(
                        (
                            sender,
                            transition.phase,
                            AgentIntent(
                                kind=IntentKind.SEND,
                                recipient=recipient,
                                text=(
                                    f"Causal evidence for {transition.phase} operation."
                                ),
                                claim_fact_keys=fact_keys,
                            ),
                        )
                    )
                resource_path = tuple(declared_paths.get("resource_readiness", ()))
                if resource_path and transition.actor_id == resource_path[-1]:
                    steps.append(
                        (
                            resource_path[0],
                            transition.phase,
                            AgentIntent(
                                kind=IntentKind.SEND,
                                recipient=resource_path[-1],
                                text=(
                                    f"Resource readiness for {transition.phase} operation."
                                ),
                                claim_fact_keys=(
                                    "inventory:fungicide_available",
                                    "equipment:sprayer_ready",
                                    "equipment:harvester_ready",
                                ),
                            ),
                        )
                    )
            steps.append(
                (
                    transition.actor_id,
                    transition.phase,
                    AgentIntent(
                        kind=(
                            IntentKind.OBSERVE
                            if transition.kind == TransitionKind.OBSERVE
                            else IntentKind.ACT
                        ),
                        action=native_action_name(action),
                        args={
                            key: value
                            for key, value in action.args.items()
                            if key != "self"
                        },
                        scope=scope_from_args(action.args),
                    ),
                )
            )
            previous_actor = transition.actor_id
        return tuple(steps)
