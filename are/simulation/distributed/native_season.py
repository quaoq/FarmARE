"""Genuinely agent-driven, parameterized-team execution of FarmARE L3 seasons."""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from are.simulation.agents.agent_log import (
    LLMInputLog,
    LLMOutputThoughtActionLog,
    LLMRetryUsageLog,
)
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
from are.simulation.distributed.journal import DurableRunJournal
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
from are.simulation.distributed.ontology import COORDINATION_CONTRACT, capability_cards
from are.simulation.distributed.petri import (
    DataGuardSpec,
    InformationPolicySpec,
    OccurrenceNet,
    PetriNetSpec,
    TransitionKind,
    WorldBranchSpec,
    unfold_petri_net,
)
from are.simulation.distributed.prefix_replay import semantic_state_digest
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
from are.simulation.time_manager import TimeManager
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


class _DeterministicSimulationClock(TimeManager):
    """Replace wall time with deterministic native-operation ticks.

    FarmARE schedules some zero-delay native effects relative to the current
    clock. Advancing one millisecond before each native tool invocation makes
    consecutive effects due without letting logging or controller bookkeeping
    advance farm time.
    """

    def __init__(self) -> None:
        super().__init__()

    def real_time_passed(self) -> float:
        return 0.0

    def advance_native_operation(self) -> None:
        self.add_offset(0.001)

    def pause(self) -> None:
        if not self.is_paused:
            self.pause_passed_time = self.offset
            self.pause_offset = 0.0
            self.pause_real_start_time = None
            self.is_paused = True

    def resume(self) -> None:
        if self.is_paused:
            self.offset += self.pause_offset
            self.pause_offset = 0.0
            self.is_paused = False
            self.pause_passed_time = None
            self.pause_real_start_time = None


def _has_path(team: AgentTeamSpec, sender: str, recipient: str) -> bool:
    try:
        shortest_path(team, sender, recipient)
    except ValueError:
        return False
    return True


def _farm_outcome(
    farm_world: FarmWorldApp,
    initial_inventory: dict[str, Any],
    *,
    combine_grain_kg: float = 0.0,
    scenario_horizon: float | None = None,
    outcome_status: str = "available",
    missing_reason: str | None = None,
) -> dict[str, Any]:
    """Return the versioned physical outcome without collapsing logistics.

    Historical keys remain for artifact compatibility.  V2 fields distinguish
    recovered crop, combine/trailer/warehouse location, storage completion and
    postharvest compliance.  Callers interrupted before a trustworthy
    measurement must pass ``outcome_status="missing"`` rather than fabricating
    zero yield.
    """
    state = farm_world.get_state()
    inventory = state.get("inventory", {})
    ridges = state.get("ridges", [])
    per_ridge: list[dict[str, Any]] = []
    biological_yield_kg = 0.0
    recovered_harvest_kg = 0.0
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
            if 0 <= int(ridge_id) < len(ridges) and bool(
                ridges[int(ridge_id)].get("harvested")
            ):
                recovered_harvest_kg += (
                    float(
                        getattr(
                            yield_state,
                            "recovered_yield_g_m2_at_market_moisture",
                            0.0,
                        )
                        or 0.0
                    )
                    * FIELD_LENGTH_M
                    * DEFAULT_RIDGE_WIDTH_M
                    / 1000.0
                )
    harvested = [bool(ridge.get("harvested")) for ridge in ridges]
    resource_use = {
        key: round(max(0.0, float(initial) - float(inventory[key])), 6)
        for key, initial in initial_inventory.items()
        if isinstance(initial, (int, float))
        and isinstance(inventory.get(key), (int, float))
    }
    harvest_complete = bool(harvested and all(harvested))
    combine_grain_kg = max(0.0, float(combine_grain_kg or 0.0))
    trailer_grain_kg = max(
        0.0, float(inventory.get("harvest_grain_kg", 0.0) or 0.0)
    )
    warehouse_grain_kg = max(
        0.0, float(inventory.get("warehouse_grain_kg", 0.0) or 0.0)
    )
    located_grain_kg = combine_grain_kg + trailer_grain_kg + warehouse_grain_kg
    # Native tools round batches to two decimals.  The relative component
    # accommodates summation over 64 ridge-level recovered quantities.
    accounting_tolerance_kg = max(1.0, recovered_harvest_kg * 0.001)
    grain_accounted_for = (
        abs(located_grain_kg - recovered_harvest_kg) <= accounting_tolerance_kg
        if recovered_harvest_kg > 0
        else located_grain_kg <= accounting_tolerance_kg
    )
    some_grain_stored = warehouse_grain_kg > 0
    storage_complete = bool(
        harvest_complete
        and some_grain_stored
        and combine_grain_kg <= 0.01
        and trailer_grain_kg <= 0.01
        and grain_accounted_for
    )
    warehouse_moisture_pct = state.get("warehouse_grain_moisture_pct")
    storage_limit_pct = float(farm_world._postharvest_market.max_storage_moisture_pct)
    moisture_safe = bool(
        warehouse_moisture_pct is not None
        and float(warehouse_moisture_pct) <= storage_limit_pct + 1e-9
    )
    postharvest_compliant = bool(storage_complete and moisture_safe)
    measurement_time = float(farm_world.time_manager.time())
    return {
        "schema_version": "farm_outcome_v2",
        "success": harvest_complete and storage_complete and postharvest_compliant,
        "biological_yield_kg": round(biological_yield_kg, 6),
        "marketable_yield_kg": warehouse_grain_kg,
        "yield_is_final": harvest_complete,
        "recovered_harvest_kg": round(recovered_harvest_kg, 6),
        "combine_grain_kg": round(combine_grain_kg, 6),
        "trailer_grain_kg": round(trailer_grain_kg, 6),
        "warehouse_grain_kg": round(warehouse_grain_kg, 6),
        "some_grain_stored": some_grain_stored,
        "harvest_complete": harvest_complete,
        "all_harvested_grain_accounted_for": grain_accounted_for,
        "grain_accounting_difference_kg": round(
            located_grain_kg - recovered_harvest_kg, 6
        ),
        "grain_accounting_tolerance_kg": round(accounting_tolerance_kg, 6),
        "storage_complete": storage_complete,
        "postharvest_compliant": postharvest_compliant,
        "warehouse_grain_moisture_pct": warehouse_moisture_pct,
        "max_storage_moisture_pct": storage_limit_pct,
        "outcome_status": outcome_status,
        "missing_reason": missing_reason,
        "measurement_time": measurement_time,
        "scenario_horizon": scenario_horizon,
        "measurement_at_horizon": bool(
            scenario_horizon is not None
            and abs(measurement_time - scenario_horizon) <= 0.01
        ),
        "measurement_overrun_seconds": (
            max(0.0, measurement_time - scenario_horizon)
            if scenario_horizon is not None
            else None
        ),
        "biological_quantities_provisional": bool(
            not harvest_complete
            and (scenario_horizon is None or measurement_time < scenario_horizon)
        ),
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
            settings = (process or net).metadata.get("native_scenario", {})
            if (
                settings.get("scenario_revision") != config.scenario_revision
                or settings.get("calibration_candidate", False)
                != config.calibration_candidate
            ):
                raise ValueError(
                    "frozen specification native scenario variant mismatch"
                )
            return net, process
        return (
            compile_paper_petri_net(
                config.scenario_id,
                world_seed=config.world_seed,
                scenario_revision=config.scenario_revision,
                calibration_candidate=config.calibration_candidate,
            ),
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
        from are.simulation.distributed.llm_budget import active_team_budget

        provider_budget = active_team_budget()
        if provider_budget is not None and config.controller_mode == "llm":
            provider_budget.per_actor_calls = {
                actor: team.per_agent_call_budget.get(
                    actor, max(1, provider_budget.max_calls // len(actor_ids))
                )
                for actor in actor_ids
            }
            provider_budget.per_actor_tokens = dict(team.per_agent_token_budget)
            if team.team_token_budget and not provider_budget.per_actor_tokens:
                provider_budget.per_actor_tokens = {
                    actor: team.team_token_budget // len(actor_ids)
                    for actor in actor_ids
                }
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
            if team.expert_review_status not in {"confirmed", "author_defined"}:
                raise ValueError("paper mode rejects an unconfirmed team specification")
            if team.team_id != PRIMARY_TEAM_ID:
                if refinement is None or refinement.expert_review_status not in {
                    "confirmed",
                    "author_defined",
                }:
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
            or process_spec.expert_review_status not in {"confirmed", "author_defined"}
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
        from are.simulation.distributed.models import RoleRefinementSpec
        from are.simulation.distributed.review_policy import validate_review_bundle

        refinement = (
            RoleRefinementSpec.model_validate_json(
                Path(config.role_refinement_path).read_text()
            )
            if config.role_refinement_path
            else None
        )
        validate_review_bundle(
            process_spec,
            team,
            refinement,
            gate.analysis_protocol_digest,
            gate.review_attestation,
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
        if config.scenario_id == "farm_disease_drought" and gate.status == "complete":
            # Legacy evidence covers the actual world. Prospective confirmation
            # binds a separate study cohort to the same frozen scenario/source.
            sensitivity_world = create_native_scenario(
                config.scenario_id,
                world_seed=config.world_seed,
                scenario_revision=config.scenario_revision,
                calibration_candidate=config.calibration_candidate,
            )
            gate.verify_sensitivity(
                Path(config.scientific_gate_manifest or ""),
                world_seed=config.world_seed,
                native_scenario={
                    "scenario_revision": config.scenario_revision,
                    "calibration_candidate": config.calibration_candidate,
                },
                exogenous_digest=stable_digest(
                    sensitivity_world.get_typed_app(
                        FarmWorldApp
                    ).physics.dcore_exogenous_manifest
                ),
            )
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
        from are.simulation.distributed.llm_budget import team_llm_budget

        call_budget = (
            config.replay_suffix_call_budget
            if config.replay_live_suffix and config.replay_suffix_call_budget is not None
            else config.team_call_budget or config.max_model_calls
        )
        token_budget = (
            config.replay_suffix_token_budget
            if config.replay_live_suffix
            and config.replay_suffix_token_budget is not None
            else config.team_token_budget
        )
        with team_llm_budget(
            call_budget, token_budget
        ):
            return self._run(config)

    def _run(self, config: DistributedRunnerConfig) -> NativeSeasonExecution:
        scenario = create_native_scenario(
            config.scenario_id,
            world_seed=config.world_seed,
            scenario_revision=config.scenario_revision,
            calibration_candidate=config.calibration_candidate,
        )
        scenario_horizon = float(scenario.start_time + scenario.duration)
        if config.replay_app_seeds:
            apps_by_name = {app.name: app for app in (scenario.apps or ())}
            if set(config.replay_app_seeds) != set(apps_by_name):
                raise ValueError("replay app-seed inventory does not match the scenario")
            for name, seed in config.replay_app_seeds.items():
                app = apps_by_name[name]
                app.seed = int(seed)
                app.rng = random.Random(app.seed)
        app_random_seeds = {
            app.name: int(app.seed) for app in (scenario.apps or ())
        }
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
            if (
                config.paper_mode
                and requested_mode != "reliable"
                and not all(
                    target.startswith("selector:") for target in treatment.target_ids
                )
            ):
                raise ValueError(
                    "new paper runs require frozen phase/route/send-order selectors; historical traces remain evaluable"
                )
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
                    "delay": treatment.delivery_delay_seconds or config.delay,
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
            ),
            time_manager=_DeterministicSimulationClock(),
        )
        env.register_apps(scenario.apps or [])
        farm_world = scenario.get_typed_app(FarmWorldApp)
        tractor_app = next(
            (
                app
                for app in (scenario.apps or ())
                if app.__class__.__name__ == "TractorApp"
            ),
            None,
        )
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
            config, actor_specs, scenario, petri_net, env, team, process_spec
        )
        controller_log_positions = {actor: 0 for actor in actor_ids}
        stores = {actor: KnowledgeStore(actor) for actor in actor_ids}
        inboxes: dict[str, list[CausalHandoff | FreeTextEnvelope]] = {
            actor: [] for actor in actor_ids
        }
        run_id = (
            f"{config.scenario_id}:{stable_digest(config.model_dump(mode='json'))[:16]}"
        )
        journal = (
            DurableRunJournal(Path(config.output_dir) / "progress.dcore.jsonl")
            if config.output_dir
            else None
        )

        def journal_append(kind: str, payload: dict[str, Any]) -> None:
            if journal is not None:
                journal.append(kind, {"run_id": run_id, **payload})

        def journal_model_attempts(
            actor_id: str,
            *,
            logical_time: float,
            world_time: float,
            activation_succeeded: bool,
        ) -> None:
            """Persist every provider invocation exposed by either controller.

            FarmARE's native agent emits one input followed by one or more output
            usage records when format correction is needed.  Retry calls reuse the
            most recent input, so the journal repeats that request for each response
            and marks every nonfinal response as a rejected proposal.
            """

            controller = controllers[actor_id]
            logs = getattr(controller, "logs", None)
            if logs is None:
                logs = getattr(getattr(controller, "base_agent", None), "logs", ())
            start = controller_log_positions[actor_id]
            new_logs = list(logs[start:])
            controller_log_positions[actor_id] = len(logs)
            inputs = [item for item in new_logs if isinstance(item, LLMInputLog)]
            outputs = [
                item
                for item in new_logs
                if isinstance(item, (LLMOutputThoughtActionLog, LLMRetryUsageLog))
            ]
            if not outputs:
                for attempt, input_log in enumerate(inputs, start=1):
                    journal_append(
                        "model_request",
                        {
                            "actor_id": actor_id,
                            "logical_time": logical_time,
                            "world_time": world_time,
                            "attempt": attempt,
                            "input_log_id": input_log.id,
                            "prompt": input_log.content,
                            "prompt_digest": stable_digest(input_log.content),
                            "response_missing": True,
                        },
                    )
                return
            for index, output_log in enumerate(outputs):
                input_log = inputs[min(index, len(inputs) - 1)] if inputs else None
                prompt = (
                    input_log.content
                    if input_log is not None
                    else getattr(controller, "last_prompt_payload", None)
                )
                attempt = index + 1
                journal_append(
                    "model_request",
                    {
                        "actor_id": actor_id,
                        "logical_time": logical_time,
                        "world_time": world_time,
                        "attempt": attempt,
                        "input_log_id": getattr(input_log, "id", None),
                        "prompt": prompt,
                        "prompt_digest": stable_digest(prompt),
                    },
                )
                rejected = isinstance(output_log, LLMRetryUsageLog) or not (
                    activation_succeeded and index == len(outputs) - 1
                )
                metadata = {
                    key: getattr(output_log, key, None)
                    for key in (
                        "model_name",
                        "model_provider",
                        "response_id",
                        "system_fingerprint",
                        "prompt_tokens",
                        "completion_tokens",
                        "total_tokens",
                        "cached_tokens",
                        "reasoning_tokens",
                        "completion_duration",
                        "retry_reason",
                    )
                }
                journal_append(
                    "model_response",
                    {
                        "actor_id": actor_id,
                        "logical_time": logical_time,
                        "world_time": world_time,
                        "attempt": attempt,
                        "output_log_id": output_log.id,
                        "response": output_log.content,
                        "metadata": metadata,
                        "proposal_status": "rejected" if rejected else "accepted",
                    },
                )
                if rejected:
                    journal_append(
                        "rejected_proposal",
                        {
                            "actor_id": actor_id,
                            "logical_time": logical_time,
                            "world_time": world_time,
                            "attempt": attempt,
                            "output_log_id": output_log.id,
                            "response": output_log.content,
                            "reason": getattr(
                                output_log, "retry_reason", "proposal_validation"
                            ),
                        },
                    )

        journal_append(
            "run_started",
            {
                "scenario_id": config.scenario_id,
                "configuration_digest": stable_digest(config.model_dump(mode="json")),
                "app_random_seeds": app_random_seeds,
                "world_time": env.time_manager.time(),
            },
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

        verifier_engines: dict[str, Any] = {}
        if config.live_verification_policy in {"always_verify", "periodic_verify"}:
            if config.paper_mode and config.controller_mode not in {
                "llm",
                "response_replay",
            }:
                raise ValueError("paper live-verifier policies require model controllers")
            from are.simulation.agents.are_simulation_agent_config import (
                LLMEngineConfig,
            )
            from are.simulation.agents.llm.llm_engine_builder import LLMEngineBuilder

            for actor in actor_ids:
                model = config.model_by_actor.get(actor)
                provider = config.provider_by_actor.get(actor)
                mock_responses = config.verifier_mock_responses_by_actor.get(actor)
                if config.controller_mode == "mock_llm":
                    model = model or "offline-mock"
                    provider = provider or "mock"
                    mock_responses = mock_responses or tuple(
                        '{"verdict":"allow","reason":"offline verifier fixture"}'
                        for _ in range(config.max_logical_steps)
                    )
                if not model or not provider:
                    raise ValueError(
                        f"live verifier requires model/provider for actor {actor!r}"
                    )
                verifier_engines[actor] = LLMEngineBuilder().create_engine(
                    LLMEngineConfig(
                        model_name=model,
                        provider=("openai-json" if provider == "openai" else provider),
                        endpoint=config.endpoint_by_actor.get(actor),
                        temperature=config.temperature_by_actor.get(actor, 0.0),
                    ),
                    mock_responses=list(mock_responses) if mock_responses else None,
                )

        def call_live_verifier(
            *,
            actor_id: str,
            decision_id: str,
            action: str,
            arguments: dict[str, Any],
            world_time: float,
        ) -> dict[str, Any]:
            """Meter one legal-prefix verifier, with two format retries."""

            controller = controllers[actor_id]
            prompt_ids = set(getattr(controller, "last_prompt_item_ids", ()))
            legal_view = {
                "schema_version": "live_verifier_prefix_v1",
                "public_contract": self._task_briefing(
                    config.scenario_id, process_spec
                ),
                "actor_id": actor_id,
                "proposal": {"action": action, "arguments": arguments},
                "world_time": world_time,
                "delivered_evidence": [
                    {
                        **item.model_dump(mode="json"),
                        "included_in_actor_prompt": item.item_id in prompt_ids,
                    }
                    for item in stores[actor_id].items
                ],
                "actual_prompt_item_ids": sorted(prompt_ids),
                "available_receipts": list(
                    getattr(controller, "accepted_write_receipts", ())
                ),
            }
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Review one high-impact farm proposal using only the supplied "
                        "legal prefix. Return JSON with verdict allow, defer, or block; "
                        "reason; and required_fact_ids. Do not infer hidden or future facts."
                    ),
                },
                {"role": "user", "content": json.dumps(legal_view, sort_keys=True)},
            ]
            from are.simulation.distributed.journal import provider_journal
            from are.simulation.distributed.pilot_budget import actor_request_scope

            for attempt in range(1, 4):
                journal_append(
                    "live_verifier_request",
                    {
                        "intent_id": decision_id,
                        "actor_id": actor_id,
                        "purpose": "verifier",
                        "attempt": attempt,
                        "legal_view": legal_view,
                        "prompt_digest": stable_digest(messages),
                    },
                )
                with actor_request_scope(
                    f"verifier:{actor_id}"
                ), provider_journal(journal_append if journal is not None else None):
                    response, metadata = verifier_engines[actor_id].chat_completion(
                        messages
                    )
                try:
                    start, end = response.find("{"), response.rfind("}")
                    parsed = json.loads(response[start : end + 1])
                    verdict = str(parsed["verdict"]).lower()
                    if verdict not in {"allow", "defer", "block"}:
                        raise ValueError("invalid verifier verdict")
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                    journal_append(
                        "live_verifier_response",
                        {
                            "intent_id": decision_id,
                            "actor_id": actor_id,
                            "purpose": "verifier",
                            "attempt": attempt,
                            "status": "rejected",
                            "response": response,
                            "metadata": metadata,
                            "error": str(error),
                        },
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": "Return exactly the required JSON object.",
                        }
                    )
                    continue
                result = {
                    "verdict": verdict,
                    "reason": str(parsed.get("reason", "")),
                    "required_fact_ids": tuple(parsed.get("required_fact_ids", ())),
                    "attempt": attempt,
                    "metadata": metadata,
                    "legal_view_digest": stable_digest(legal_view),
                }
                journal_append(
                    "live_verifier_response",
                    {
                        "intent_id": decision_id,
                        "actor_id": actor_id,
                        "purpose": "verifier",
                        "status": "accepted",
                        **result,
                    },
                )
                return result
            return {
                "verdict": "defer",
                "reason": "verifier_format_retries_exhausted",
                "required_fact_ids": (),
                "attempt": 3,
                "legal_view_digest": stable_digest(legal_view),
            }

        replay_checkpoint = None
        replay_repair_candidate = None
        replay_source_decisions: tuple[DecisionRecord, ...] = ()
        replay_source_facts: dict[str, FactVersionRecord] = {}
        replay_checkpoint_verification: dict[str, Any] | None = None
        replay_repair_application: dict[str, Any] | None = None
        if config.replay_checkpoint is not None:
            from are.simulation.distributed.evaluation_adapters.contracts import (
                ContinuationManifest,
                RepairCandidate,
            )

            replay_checkpoint = ContinuationManifest.model_validate(
                config.replay_checkpoint
            )
            source_payload = json.loads(
                Path(config.replay_trace or "").read_text(encoding="utf-8")
            )
            replay_source_trace = DistributedTrace.model_validate(source_payload)
            replay_source_decisions = replay_source_trace.decisions
            replay_source_facts = {
                item.version_id: item for item in replay_source_trace.fact_versions
            }
            if config.replay_repair_candidate is not None:
                replay_repair_candidate = RepairCandidate.model_validate(
                    config.replay_repair_candidate
                )
                if replay_repair_candidate.feasibility != "feasible":
                    raise ValueError("only a feasible locked repair may be executed")
                if (
                    replay_checkpoint.repair_candidate_id is not None
                    and replay_checkpoint.repair_candidate_id
                    != replay_repair_candidate.candidate_id
                ):
                    raise ValueError("checkpoint and repair candidate IDs disagree")

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
        wake_at: dict[str, float] = {}
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
        infrastructure_errors: list[str] = []
        native_execution_errors: list[str] = []
        termination_by_actor: dict[str, str] = {}
        activation_manifest: list[str] = []
        high_impact_proposal_count = 0
        live_verification_count = 0
        live_repair_deferral_count = 0
        live_interventions: list[dict[str, Any]] = []
        live_repair_attempted_keys: set[tuple[str, str, str]] = set()
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
                journal_append(
                    "message_delivery",
                    {
                        "event_id": receive_event.event_id,
                        "message_id": envelope.message_id,
                        "sender": envelope.sender,
                        "recipient": envelope.recipient,
                        "status": status,
                        "world_time": env.time_manager.time(),
                    },
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
                                    for index, claim in enumerate(
                                        getattr(envelope, "claims", ())
                                    )
                                    if item.item_id
                                    == f"{envelope.message_id}:claim:{index}"
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
                    # A genuinely new handoff may wake a waiting actor. This
                    # never removes the terminal `finished` state.
                    wake_at.pop(envelope.recipient, None)
                provenance_ids.update(
                    evidence_id for item in added for evidence_id in item.evidence_ids
                )

        pending_context_restorations: dict[str, set[str]] = defaultdict(set)

        def resolve_repair_evidence(
            primitive: Any, *, holder_actor_id: str
        ) -> KnowledgeItem:
            source_record = replay_source_facts.get(primitive.fact_version_id or "")
            if source_record is None and primitive.fact_version_id:
                source_record = next(
                    (
                        item
                        for item in recorder.fact_versions
                        if item.version_id == primitive.fact_version_id
                    ),
                    None,
                )
            candidates = [
                item
                for item in stores[holder_actor_id].items
                if item.fact_key == primitive.fact_key
                and (
                    item.scope == primitive.scope
                    if primitive.scope is not None
                    else True
                )
            ]
            if primitive.fact_version_id and source_record is None:
                raise ValueError("required repair evidence version is unavailable")
            if source_record is not None:
                semantic_matches = [
                    item
                    for item in candidates
                    if item.value == source_record.value
                    and item.scope == source_record.scope
                    and round(item.observed_at) == round(source_record.world_time)
                ]
                if not semantic_matches:
                    raise ValueError(
                        "required repair evidence version is not held by the actor"
                    )
                candidates = semantic_matches
            if not candidates:
                raise ValueError("repair evidence is absent from the verified prefix")
            item = max(
                candidates,
                key=lambda value: (value.observed_at, value.learned_at, value.item_id),
            )
            if item.valid_until is not None and env.time_manager.time() > item.valid_until:
                raise ValueError("repair cannot route or restore expired evidence")
            return item

        def apply_locked_repair(
            phase: str, candidate: Any | None = None
        ) -> dict[str, Any]:
            nonlocal logical_time, sent_message_count
            repair_candidate = candidate or replay_repair_candidate
            if repair_candidate is None:
                raise ValueError("live suffix requested without a locked repair")

            def record_repair_decision(
                repair_actor: str,
                repair_action: str,
                repair_args: dict[str, Any],
                primitive_name: str,
            ) -> Any:
                repair_snapshot = stores[repair_actor].snapshot(
                    logical_time, recorder.clock(repair_actor)
                )
                recorder.add_snapshot(repair_snapshot)
                event = recorder.record(
                    EventKind.DECISION,
                    repair_actor,
                    logical_time,
                    world_time=env.time_manager.time(),
                    action=repair_action,
                    status="repair_intervention",
                    payload={
                        "repair_candidate_id": repair_candidate.candidate_id,
                        "repair_primitive": primitive_name,
                    },
                    season_phase=phase,
                )
                recorder.add_decision(
                    DecisionRecord(
                        decision_id=event.event_id,
                        actor_id=repair_actor,
                        logical_time=logical_time,
                        knowledge_snapshot=repair_snapshot,
                        proposed_intent=AgentIntent(
                            kind=IntentKind.ACT,
                            action=repair_action,
                            args=repair_args,
                        ),
                        prompt_digest=repair_snapshot.digest,
                        prompt_item_ids=repair_snapshot.item_ids,
                        season_phase=phase,
                    )
                )
                return event

            applications: list[dict[str, Any]] = []
            for primitive_index, primitive in enumerate(
                repair_candidate.primitives
            ):
                logical_time += 0.01
                application: dict[str, Any] = {
                    "primitive_index": primitive_index,
                    "primitive": primitive.model_dump(mode="json"),
                    "status": "applied",
                }
                if primitive.actor_id not in stores:
                    raise ValueError("repair primitive names an unknown actor")
                if primitive.primitive in {
                    "acquire_observation",
                    "refresh_observation",
                }:
                    if not primitive.native_action:
                        raise ValueError("observation repair lacks a native action")
                    metadata = gateway.metadata(primitive.native_action)
                    permitted_sensing_write = primitive.native_action in {
                        "Mavic3M__fly_survey",
                        "Matrice4T__fly_survey",
                    } or (
                        primitive.native_action.startswith("Robot")
                        and "__inspect_" in primitive.native_action
                    )
                    if not metadata["observation"] or (
                        metadata["write"] and not permitted_sensing_write
                    ):
                        raise ValueError(
                            "observation repair must use a permitted native sensing tool"
                        )
                    repair_decision = record_repair_decision(
                        primitive.actor_id,
                        primitive.native_action,
                        dict(primitive.native_arguments),
                        primitive.primitive,
                    )
                    execution = gateway.execute(
                        actor_id=primitive.actor_id,
                        intent_id=repair_decision.event_id,
                        action=primitive.native_action,
                        arguments=dict(primitive.native_arguments),
                    )
                    action_event = recorder.record(
                        EventKind.ACTION,
                        primitive.actor_id,
                        logical_time + 0.001,
                        world_time=env.time_manager.time(),
                        action=primitive.native_action,
                        args=execution.arguments,
                        status="error" if execution.error else "ok",
                        payload={
                            "repair_candidate_id": repair_candidate.candidate_id,
                            "result": execution.result,
                            "error": execution.error,
                        },
                        causal_parents=(repair_decision.event_id,),
                        decision_context_id=repair_decision.event_id,
                        farmare_event_id=(
                            execution.completed_event.event_id
                            if execution.completed_event
                            else None
                        ),
                        season_phase=phase,
                    )
                    journal_append(
                        "repair_native_receipt",
                        {
                            "candidate_id": repair_candidate.candidate_id,
                            "primitive_index": primitive_index,
                            "receipt": execution.receipt(repair_decision.event_id),
                            "world_time": env.time_manager.time(),
                        },
                    )
                    if execution.error:
                        application["status"] = "native_execution_failure"
                        application["error"] = execution.error
                    else:
                        self._record_observation_facts(
                            recorder=recorder,
                            store=stores[primitive.actor_id],
                            all_stores=stores,
                            shared=config.visibility_mode == "shared_blackboard",
                            evidence_actor=primitive.actor_id in evidence_actor_ids,
                            adapter=adapter,
                            actor_id=primitive.actor_id,
                            action_event_id=action_event.event_id,
                            farmare_event_id=action_event.farmare_event_id,
                            action=primitive.native_action,
                            args=execution.arguments,
                            result=execution.result,
                            logical_time=logical_time + 0.002,
                            world_time=env.time_manager.time(),
                            phase=phase,
                            provenance_ids=provenance_ids,
                        )
                elif primitive.primitive in {
                    "redeliver_evidence",
                    "route_evidence",
                }:
                    if not primitive.recipient_actor_id:
                        raise ValueError("routing repair lacks a recipient")
                    if not can_send(
                        team, primitive.actor_id, primitive.recipient_actor_id
                    ):
                        raise ValueError("routing repair violates the team topology")
                    evidence = resolve_repair_evidence(
                        primitive, holder_actor_id=primitive.actor_id
                    )
                    sent_message_count += 1
                    message_id = (
                        f"repair:{repair_candidate.candidate_id}:"
                        f"{primitive_index}"
                    )
                    envelope = CausalHandoff(
                        message_id=message_id,
                        sender=primitive.actor_id,
                        recipient=primitive.recipient_actor_id,
                        text=f"Locked D-CORE repair for {primitive.fact_key}",
                        claims=(
                            Claim(
                                fact_key=evidence.fact_key,
                                value=evidence.value,
                                fact_version_id=evidence.item_id,
                                scope=evidence.scope,
                                status=evidence.status,
                                confidence=evidence.confidence,
                                observed_at=evidence.observed_at,
                                valid_until=evidence.valid_until,
                                evidence_ids=evidence.evidence_ids,
                                causal_parents=evidence.causal_parents,
                            ),
                        ),
                        send_time=env.time_manager.time(),
                    )
                    send = recorder.record(
                        EventKind.MESSAGE_SEND,
                        primitive.actor_id,
                        logical_time,
                        world_time=env.time_manager.time(),
                        action="dcore.repair_route",
                        status="repair_intervention",
                        evidence_ids=evidence.evidence_ids,
                        message_id=message_id,
                        payload={
                            "recipient": primitive.recipient_actor_id,
                            "fact_keys": [evidence.fact_key],
                            "fact_versions": [evidence.item_id],
                            "repair_candidate_id": repair_candidate.candidate_id,
                        },
                        season_phase=phase,
                    )
                    envelope = envelope.model_copy(
                        update={"vector_clock": send.vector_clock}
                    )
                    transport.send(envelope, env.time_manager.time(), phase=phase)
                    journal_append(
                        "repair_message_send",
                        {
                            "candidate_id": repair_candidate.candidate_id,
                            "primitive_index": primitive_index,
                            "envelope": envelope.model_dump(mode="json"),
                            "world_time": env.time_manager.time(),
                        },
                    )
                    deliver_due()
                elif primitive.primitive == "restore_context":
                    evidence = resolve_repair_evidence(
                        primitive, holder_actor_id=primitive.actor_id
                    )
                    restored = evidence.model_copy(
                        update={
                            "item_id": (
                                f"repair:{repair_candidate.candidate_id}:"
                                f"context:{primitive_index}"
                            ),
                            "learned_at": env.time_manager.time(),
                            "status": EpistemicStatus.CLAIMED,
                        }
                    )
                    stores[primitive.actor_id].add(restored)
                    pending_context_restorations[primitive.actor_id].add(
                        restored.item_id
                    )
                    restore_event = recorder.record(
                        EventKind.OBSERVATION,
                        primitive.actor_id,
                        logical_time,
                        world_time=env.time_manager.time(),
                        action="dcore.restore_context",
                        status="repair_intervention",
                        payload={
                            "fact_key": restored.fact_key,
                            "scope": restored.scope,
                            "repair_candidate_id": repair_candidate.candidate_id,
                        },
                        fact_version=restored.item_id,
                        season_phase=phase,
                    )
                    recorder.add_fact_version(
                        FactVersionRecord(
                            version_id=restored.item_id,
                            fact_key=restored.fact_key,
                            value=restored.value,
                            status=restored.status,
                            scope=restored.scope,
                            source_event_id=restore_event.event_id,
                            origin_version_id=evidence.item_id,
                            world_time=restored.observed_at,
                            learned_time=restored.learned_at,
                            valid_until=restored.valid_until,
                            evidence_ids=restored.evidence_ids,
                            visible_to=(primitive.actor_id,),
                            season_phase=phase,
                            authoritative=False,
                        )
                    )
                elif primitive.primitive == "request_reconsideration":
                    previous_results[primitive.actor_id] = {
                        "status": "dcore_reconsideration_requested",
                        "repair_candidate_id": repair_candidate.candidate_id,
                        "fact_key": primitive.fact_key,
                        "scope": primitive.scope,
                        "instruction": "Reconsider the pending decision using currently valid prefix evidence.",
                    }
                    record_repair_decision(
                        primitive.actor_id,
                        "dcore.request_reconsideration",
                        {},
                        primitive.primitive,
                    )
                else:
                    raise ValueError(f"unsupported repair primitive {primitive.primitive!r}")
                applications.append(application)
                journal_append(
                    "repair_primitive_applied",
                    {
                        "candidate_id": repair_candidate.candidate_id,
                        **application,
                        "world_time": env.time_manager.time(),
                    },
                )
            return {
                "schema_version": "repair_application_v1",
                "candidate_id": repair_candidate.candidate_id,
                "applications": applications,
            }

        step = 0
        while step < config.max_logical_steps:
            step += 1
            deliver_due()
            if env.time_manager.time() >= scenario_horizon:
                for horizon_actor in actor_ids:
                    if horizon_actor not in finished:
                        termination_by_actor[horizon_actor] = "scenario_horizon_reached"
                        recorder.record(
                            EventKind.FINISH,
                            horizon_actor,
                            logical_time + 0.0001,
                            world_time=scenario_horizon,
                            action="farm.scenario_horizon_reached",
                            status="incomplete",
                            payload={"scenario_horizon": scenario_horizon},
                        )
                        finished.add(horizon_actor)
                break
            active = [actor for actor in actor_ids if actor not in finished]
            if active and all(
                wake_at.get(actor, logical_time) > logical_time for actor in active
            ):
                # Advance only the scheduler clock to an explicitly requested
                # wake-up. Farm time and native state remain unchanged.
                logical_time = min(wake_at[actor] for actor in active)
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
                if actor_id in finished or logical_time < wake_at.get(
                    actor_id, logical_time
                ):
                    continue
                wake_at.pop(actor_id, None)
                activation_manifest.append(actor_id)
                logical_time += 1.0
                controller = controllers[actor_id]
                snapshot = stores[actor_id].snapshot(
                    logical_time, recorder.clock(actor_id)
                )
                recorder.add_snapshot(snapshot)
                journal_append(
                    "context_snapshot",
                    {
                        "actor_id": actor_id,
                        "logical_time": logical_time,
                        "world_time": env.time_manager.time(),
                        "snapshot": snapshot.model_dump(mode="json"),
                    },
                )
                phase_hint = adapter.phase("", env.time_manager.time())
                if process_spec is not None and process_spec.phase_windows:
                    phase_hint = next(
                        (
                            window.phase
                            for window in process_spec.phase_windows
                            if window.start_world_time
                            <= env.time_manager.time()
                            < window.end_world_time
                        ),
                        "outside_specification",
                    )
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
                controller_state = {
                    item: {
                        "complete": bool(getattr(controllers[item], "complete", False)),
                        "decisions": int(getattr(controllers[item], "decisions", 0)),
                        "max_decisions": getattr(controllers[item], "max_decisions", None),
                        "max_model_calls": getattr(
                            controllers[item], "max_model_calls", None
                        ),
                        "max_total_tokens": getattr(
                            controllers[item], "max_total_tokens", None
                        ),
                        "intent_kind_counts": dict(
                            getattr(controllers[item], "intent_kind_counts", {})
                        ),
                        "last_prompt_item_ids": tuple(
                            getattr(controllers[item], "last_prompt_item_ids", ())
                        ),
                        "last_prompt_message_ids": tuple(
                            getattr(controllers[item], "last_prompt_message_ids", ())
                        ),
                    }
                    for item in actor_ids
                }
                actor_memory = {
                    item: {
                        "accepted_field_work": sorted(
                            (
                                [action, ridge],
                                value,
                            )
                            for (action, ridge), value in getattr(
                                controllers[item], "accepted_field_work", {}
                            ).items()
                        ),
                        "accepted_postharvest_work": getattr(
                            controllers[item], "accepted_postharvest_work", {}
                        ),
                        "recent_failures": tuple(
                            getattr(controllers[item], "recent_failures", ())
                        ),
                        "persistent_failures": sorted(
                            (list(key), value)
                            for key, value in getattr(
                                controllers[item], "persistent_failures", {}
                            ).items()
                        ),
                    }
                    for item in actor_ids
                }
                prompt_histories = {
                    item: tuple(
                        {
                            "type": type(log).__name__,
                            # Generated log ids make ``str(log)`` unstable across
                            # an otherwise identical replay.  The continuing
                            # controller consumes content/iteration/retry state.
                            "content": getattr(log, "content", None),
                            "iteration": getattr(log, "iteration", None),
                            "retry_reason": getattr(log, "retry_reason", None),
                        }
                        for log in getattr(
                            getattr(controllers[item], "base_agent", None),
                            "logs",
                            (),
                        )
                    )
                    for item in actor_ids
                }
                request_counters = {
                    item: sum(
                        isinstance(log, (LLMOutputThoughtActionLog, LLMRetryUsageLog))
                        for log in getattr(
                            getattr(controllers[item], "base_agent", None),
                            "logs",
                            (),
                        )
                    )
                    for item in actor_ids
                }
                pending_envelopes = tuple(
                    transport.snapshot().get("pending", ())
                )
                scientific_configuration = {
                    key: value
                    for key, value in config.model_dump(mode="json").items()
                    if key
                    not in {
                        "controller_mode",
                        "output_dir",
                        "replay_trace",
                        "replay_app_seeds",
                        "replay_checkpoint",
                        "replay_repair_candidate",
                        "replay_live_suffix",
                        "replay_suffix_call_budget",
                        "replay_suffix_token_budget",
                        "replay_live_responses_by_actor",
                        "resume",
                        "paper_mode",
                        "engineering_llm_pilot",
                        "bounded_llm_smoke",
                        "scientific_gate_manifest",
                        "model_by_actor",
                        "provider_by_actor",
                        "endpoint_by_actor",
                        "temperature_by_actor",
                        "max_output_tokens",
                    }
                }
                predecision_checkpoint = {
                    "physical_state_digest": stable_digest(env.get_apps_state()),
                    "semantic_physical_state_digest": semantic_state_digest(
                        env.get_apps_state()
                    ),
                    "actor_context_digests": {
                        item: stable_digest(
                            {
                                "knowledge": stores[item].items,
                                "inbox": inboxes[item],
                                "previous_result": previous_results[item],
                            }
                        )
                        for item in actor_ids
                    },
                    "semantic_actor_context_digests": {
                        item: semantic_state_digest(
                            {
                                "knowledge": stores[item].items,
                                "inbox": inboxes[item],
                                "previous_result": previous_results[item],
                            }
                        )
                        for item in actor_ids
                    },
                    "knowledge_digests": {
                        item: stable_digest(stores[item].items) for item in actor_ids
                    },
                    "semantic_knowledge_digests": {
                        item: semantic_state_digest(stores[item].items)
                        for item in actor_ids
                    },
                    "pending_delivery_digest": stable_digest(pending_envelopes),
                    "semantic_pending_delivery_digest": semantic_state_digest(
                        pending_envelopes
                    ),
                    "pending_delivery_envelopes": pending_envelopes,
                    "clock_digest": stable_digest(
                        {item: recorder.clock(item) for item in actor_ids}
                    ),
                    "configuration_digest": stable_digest(scientific_configuration),
                    "controller_state_digests": {
                        item: semantic_state_digest(value)
                        for item, value in controller_state.items()
                    },
                    "controller_state": controller_state,
                    "prompt_history_digests": {
                        item: semantic_state_digest(value)
                        for item, value in prompt_histories.items()
                    },
                    "prompt_history": prompt_histories,
                    "actor_memory_digests": {
                        item: semantic_state_digest(value)
                        for item, value in actor_memory.items()
                    },
                    "actor_memory": actor_memory,
                    "request_counters": request_counters,
                    "scheduler_state_digest": semantic_state_digest(
                        {
                            "logical_time": logical_time,
                            "step": step,
                            "finished": sorted(finished),
                            "wake_at": wake_at,
                            "activation_manifest": activation_manifest,
                        }
                    ),
                    "scheduler_state": {
                        "logical_time": logical_time,
                        "step": step,
                        "finished": sorted(finished),
                        "wake_at": wake_at,
                        "activation_manifest": activation_manifest,
                    },
                    "random_state_digest": stable_digest(rng.getstate()),
                }
                if replay_checkpoint is not None and replay_checkpoint_verification is None:
                    decision_index = len(recorder.decisions)
                    if decision_index >= len(replay_source_decisions):
                        raise ValueError("replay exhausted decisions before checkpoint")
                    source_decision = replay_source_decisions[decision_index]
                    if source_decision.actor_id != actor_id:
                        raise ValueError(
                            "replay activation order diverged before checkpoint"
                        )
                    if (
                        source_decision.decision_id
                        == replay_checkpoint.checkpoint_decision_id
                    ):
                        from are.simulation.distributed.prefix_replay import (
                            semantic_trace_digest,
                        )

                        observed = {
                            "semantic_prefix_digest": semantic_trace_digest(
                                {
                                    "events": [
                                        item.model_dump(mode="json")
                                        for item in recorder.events
                                    ]
                                }
                            ),
                            "physical_state_digest": predecision_checkpoint[
                                "semantic_physical_state_digest"
                            ],
                            "actor_context_digests": predecision_checkpoint[
                                "semantic_actor_context_digests"
                            ],
                            "knowledge_digests": predecision_checkpoint[
                                "semantic_knowledge_digests"
                            ],
                            "pending_delivery_digest": predecision_checkpoint[
                                "semantic_pending_delivery_digest"
                            ],
                            "clock_digest": predecision_checkpoint["clock_digest"],
                            "configuration_digest": predecision_checkpoint[
                                "configuration_digest"
                            ],
                            "controller_state_digests": predecision_checkpoint[
                                "controller_state_digests"
                            ],
                            "prompt_history_digests": predecision_checkpoint[
                                "prompt_history_digests"
                            ],
                            "actor_memory_digests": predecision_checkpoint[
                                "actor_memory_digests"
                            ],
                            "request_counters": predecision_checkpoint[
                                "request_counters"
                            ],
                            "scheduler_state_digest": predecision_checkpoint[
                                "scheduler_state_digest"
                            ],
                            "random_state_digest": predecision_checkpoint[
                                "random_state_digest"
                            ],
                        }
                        if replay_checkpoint.schema_version == "continuation_manifest_v1":
                            for legacy_absent in (
                                "configuration_digest",
                                "controller_state_digests",
                                "prompt_history_digests",
                                "actor_memory_digests",
                                "request_counters",
                                "scheduler_state_digest",
                                "random_state_digest",
                            ):
                                observed.pop(legacy_absent, None)
                        expected = {
                            key: getattr(replay_checkpoint, key) for key in observed
                        }
                        mismatches = tuple(
                            key for key in observed if observed[key] != expected[key]
                        )
                        replay_checkpoint_verification = {
                            "schema_version": "checkpoint_verification_v1",
                            "checkpoint_decision_id": (
                                replay_checkpoint.checkpoint_decision_id
                            ),
                            "verified": not mismatches,
                            "mismatches": mismatches,
                            "observed": observed,
                            "expected": expected,
                            "state_evidence": {
                                "observed_controller_state": controller_state,
                                "expected_controller_state": replay_checkpoint.controller_state,
                                "observed_prompt_history": prompt_histories,
                                "expected_prompt_history": replay_checkpoint.prompt_history,
                                "observed_actor_memory": actor_memory,
                                "expected_actor_memory": replay_checkpoint.actor_memory,
                            },
                        }
                        journal_append(
                            "checkpoint_verification",
                            replay_checkpoint_verification,
                        )
                        if mismatches:
                            raise ValueError(
                                "replay checkpoint mismatch: " + ", ".join(mismatches)
                            )
                        if config.replay_live_suffix:
                            discarded = {}
                            for replay_actor, replay_controller in controllers.items():
                                replay_engine = getattr(
                                    getattr(replay_controller, "base_agent", None),
                                    "llm_engine",
                                    None,
                                )
                                begin_live = getattr(
                                    replay_engine, "begin_live_suffix", None
                                )
                                if begin_live is None:
                                    raise ValueError(
                                        "repaired suffix controller cannot discard future responses"
                                    )
                                discarded[replay_actor] = begin_live()
                                if config.replay_suffix_call_budget is not None:
                                    calls_used = getattr(
                                        replay_controller, "_model_call_count", lambda: 0
                                    )()
                                    replay_controller.max_model_calls = (
                                        int(calls_used)
                                        + config.replay_suffix_call_budget
                                    )
                                if config.replay_suffix_token_budget is not None:
                                    tokens_used = getattr(
                                        replay_controller, "_token_count", lambda: 0
                                    )()
                                    replay_controller.max_total_tokens = (
                                        int(tokens_used)
                                        + config.replay_suffix_token_budget
                                    )
                            replay_repair_application = (
                                apply_locked_repair(phase_hint)
                                if replay_repair_candidate is not None
                                else {
                                    "schema_version": "repair_application_v2",
                                    "candidate_id": None,
                                    "condition": "fresh_no_intervention",
                                    "applications": [],
                                }
                            )
                            replay_repair_application["discarded_future_responses"] = (
                                discarded
                            )
                            replay_repair_application["live_suffix_started"] = True
                            snapshot = stores[actor_id].snapshot(
                                logical_time, recorder.clock(actor_id)
                            )
                            recorder.add_snapshot(snapshot)
                            pending_policy = self._information_policy_commitment(
                                petri_net=petri_net,
                                process_spec=process_spec,
                                actor_id=actor_id,
                                phase=phase_hint,
                                knowledge=stores[actor_id],
                                world_time=env.time_manager.time(),
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
                    from are.simulation.distributed.journal import provider_journal
                    from are.simulation.distributed.pilot_budget import (
                        actor_request_scope,
                    )

                    request_actor = actor_id
                    if (
                        isinstance(previous_results.get(actor_id), dict)
                        and previous_results[actor_id].get("status")
                        == "dcore_reconsideration_requested"
                    ):
                        request_actor = f"reconsideration:{actor_id}"
                    elif (
                        config.replay_live_suffix
                        and replay_checkpoint_verification is not None
                    ):
                        request_actor = f"continuation:{actor_id}"
                    with actor_request_scope(request_actor), provider_journal(
                        journal_append if journal is not None else None
                    ):
                        intent = controller.decide(local_view)
                except Exception as exc:
                    from are.simulation.distributed.pilot_budget import (
                        RequestBudgetExceeded,
                    )
                    from are.simulation.exceptions import (
                        AgentError,
                        InvalidToolCallError,
                    )

                    journal_model_attempts(
                        actor_id,
                        logical_time=logical_time,
                        world_time=env.time_manager.time(),
                        activation_succeeded=False,
                    )

                    if isinstance(exc, RequestBudgetExceeded):
                        reason = "budget_termination"
                    elif (
                        isinstance(exc, (AgentError, InvalidToolCallError, ValueError))
                        or type(exc).__name__ == "InvalidProposalResponse"
                    ):
                        reason = "invalid_proposal"
                        controller_errors.append(f"{actor_id} controller: {exc}")
                    else:
                        reason = "provider_or_infrastructure_error"
                        infrastructure_errors.append(
                            f"{actor_id}: {type(exc).__name__}: {exc}"
                        )
                    termination_by_actor[actor_id] = reason
                    journal_append(
                        "activation_error",
                        {
                            "actor_id": actor_id,
                            "logical_time": logical_time,
                            "world_time": env.time_manager.time(),
                            "reason": reason,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )
                    recorder.record(
                        EventKind.FINISH,
                        actor_id,
                        logical_time,
                        world_time=env.time_manager.time(),
                        action="dcore.activation_terminated",
                        status="error",
                        payload={
                            "reason": reason,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                        season_phase=phase_hint,
                    )
                    finished.add(actor_id)
                    continue
                journal_model_attempts(
                    actor_id,
                    logical_time=logical_time,
                    world_time=env.time_manager.time(),
                    activation_succeeded=True,
                )
                restored_ids = pending_context_restorations.pop(actor_id, set())
                if restored_ids:
                    final_prompt_ids = set(
                        getattr(controller, "last_prompt_item_ids", ())
                    )
                    missing_restored = sorted(restored_ids - final_prompt_ids)
                    restoration_status = {
                        "actor_id": actor_id,
                        "required_fact_version_ids": sorted(restored_ids),
                        "prompt_item_ids": sorted(final_prompt_ids),
                        "status": (
                            "successful_prompt_inclusion"
                            if not missing_restored
                            else "unsuccessful_prompt_omission"
                        ),
                        "missing_fact_version_ids": missing_restored,
                    }
                    if replay_repair_application is not None:
                        replay_repair_application.setdefault(
                            "context_restoration_checks", []
                        ).append(restoration_status)
                    journal_append("context_restoration_check", restoration_status)
                journal_append(
                    "model_exchange",
                    {
                        "actor_id": actor_id,
                        "logical_time": logical_time,
                        "world_time": env.time_manager.time(),
                        "prompt": getattr(controller, "last_prompt_payload", None),
                        "prompt_digest": getattr(
                            controller, "last_prompt_digest", None
                        ),
                        "response": getattr(
                            controller, "last_response_content", None
                        ),
                        "metadata": getattr(controller, "last_metadata", {}),
                        "parsed_intent": intent.model_dump(mode="json"),
                    },
                )
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
                journal_append(
                    "parsed_proposal",
                    {
                        "intent_id": decision.event_id,
                        "actor_id": actor_id,
                        "logical_time": logical_time,
                        "world_time": env.time_manager.time(),
                        "intent": intent.model_dump(mode="json"),
                        "checkpoint": predecision_checkpoint,
                    },
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
                    "arguments": dict(intent.args),
                    "intent_id": decision.event_id,
                    "season_phase": phase,
                }
                decision_finalized = False

                def finalize_decision_record() -> None:
                    nonlocal decision_finalized
                    if decision_finalized:
                        return
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
                            prompt_item_ids=getattr(
                                controller, "last_prompt_item_ids", snapshot.item_ids
                            ),
                            prompt_message_ids=getattr(
                                controller, "last_prompt_message_ids", ()
                            ),
                            prompt_omissions=getattr(
                                controller, "last_prompt_omissions", {}
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
                    decision_finalized = True

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
                        sent = transport.send(
                            envelope,
                            env.time_manager.time(),
                            phase=phase_hint,
                            evidence_valid_until=min(
                                (
                                    item.valid_until
                                    for item in stores[actor_id].for_keys(
                                        intent.claim_fact_keys
                                    )
                                    if item.valid_until is not None
                                ),
                                default=None,
                            ),
                        )
                        journal_append(
                            "message_send",
                            {
                                "intent_id": decision.event_id,
                                "message_id": sent.message_id,
                                "sender": actor_id,
                                "recipients": [sent.recipient],
                                "envelope": sent.model_dump(mode="json"),
                                "world_time": env.time_manager.time(),
                            },
                        )
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
                    high_impact_proposal_count += 1
                    finish_outcome = _farm_outcome(
                        farm_world,
                        initial_inventory,
                        combine_grain_kg=(
                            float(
                                tractor_app.get_state().get("grain_bin_kg", 0.0)
                            )
                            if tractor_app is not None
                            else 0.0
                        ),
                        scenario_horizon=scenario.start_time + scenario.duration,
                    )
                    duties_complete = bool(
                        finish_outcome["harvest_complete"]
                        and finish_outcome["storage_complete"]
                        and finish_outcome["postharvest_compliant"]
                    )
                    verify_finish = (
                        config.live_verification_policy == "always_verify"
                        or config.live_verification_policy == "dcore_selective"
                        and not duties_complete
                        or config.live_verification_policy == "periodic_verify"
                        and high_impact_proposal_count % config.verification_period == 0
                    )
                    finish_verifier_result = None
                    if verify_finish and config.live_verification_policy in {
                        "always_verify",
                        "periodic_verify",
                    }:
                        live_verification_count += 1
                        finish_verifier_result = call_live_verifier(
                            actor_id=actor_id,
                            decision_id=decision.event_id,
                            action="dcore_finish",
                            arguments={},
                            world_time=env.time_manager.time(),
                        )
                    defer_incomplete_finish = bool(
                        verify_finish
                        and not duties_complete
                        and (
                            config.live_verification_policy == "dcore_selective"
                            or finish_verifier_result is not None
                            and finish_verifier_result["verdict"] != "allow"
                        )
                    )
                    if defer_incomplete_finish:
                        if finish_verifier_result is None:
                            live_verification_count += 1
                        live_repair_deferral_count += 1
                        if hasattr(controller, "complete"):
                            controller.complete = False
                        recorder.record(
                            EventKind.GUARD,
                            actor_id,
                            logical_time + 0.005,
                            world_time=env.time_manager.time(),
                            action="dcore_finish",
                            causal_parents=(decision.event_id,),
                            status="deferred",
                            payload={
                                "live_verification_policy": (
                                    config.live_verification_policy
                                ),
                                "reason": "seasonal_duties_incomplete",
                                "repair": "request_reconsideration",
                                "verifier": finish_verifier_result,
                            },
                            season_phase=phase,
                        )
                        result_payload.update(
                            {
                                "executed": False,
                                "deferred": True,
                                "guard_verdict": "defer",
                                "feedback": (
                                    "Seasonal duties are incomplete. Inspect current "
                                    "harvest, trailer, drying, and warehouse state, "
                                    "then complete the remaining native operation."
                                ),
                            }
                        )
                        journal_append(
                            "live_repair",
                            {
                                "intent_id": decision.event_id,
                                "actor_id": actor_id,
                                "policy": config.live_verification_policy,
                                "repair": "request_reconsideration",
                                "reason": "seasonal_duties_incomplete",
                            },
                        )
                        live_interventions.append(
                            {
                                "intent_id": decision.event_id,
                                "policy": config.live_verification_policy,
                                "status": "applied",
                                "kind": "finish_deferral",
                                "verifier": finish_verifier_result,
                            }
                        )
                        finalize_decision_record()
                        previous_results[actor_id] = result_payload
                        result_payload["result_world_time"] = env.time_manager.time()
                        controller.observe(result_payload)
                        continue
                    termination_by_actor[actor_id] = (
                        "successful_completion"
                        if duties_complete
                        else "premature_abandonment"
                    )
                    journal_append(
                        "termination",
                        {
                            "intent_id": decision.event_id,
                            "actor_id": actor_id,
                            "reason": termination_by_actor[actor_id],
                            "duties_complete": duties_complete,
                            "world_time": env.time_manager.time(),
                        },
                    )
                    finished.add(actor_id)
                    recorder.record(
                        EventKind.FINISH,
                        actor_id,
                        logical_time + 0.01,
                        world_time=env.time_manager.time(),
                        action="farm.actor_complete",
                        causal_parents=(decision.event_id,),
                        status="ok" if duties_complete else "incomplete",
                        payload={
                            "duties_complete": duties_complete,
                            "harvest_complete": finish_outcome["harvest_complete"],
                            "storage_complete": finish_outcome["storage_complete"],
                            "postharvest_compliant": finish_outcome[
                                "postharvest_compliant"
                            ],
                        },
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
                    wake_at[actor_id] = logical_time + max(1.0, intent.wait)
                    result_payload.update(
                        {
                            "executed": False,
                            "deferred": True,
                            "requested_wait": intent.wait,
                            "wake_at_logical_time": wake_at[actor_id],
                            "new_handoff_may_wake_earlier": True,
                            "farm_time_advanced": False,
                        }
                    )
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
                        high_impact = bool(
                            metadata["high_impact"]
                            or intent.action.endswith(
                                (
                                    "__irrigate",
                                    "__harvest",
                                    "__unload_grain",
                                    "__dry_grain",
                                    "__store_grain",
                                    "__apply_fungicide",
                                    "__spray_pesticide",
                                )
                            )
                        )
                        review_selected = False
                        if high_impact:
                            high_impact_proposal_count += 1
                            policy = config.live_verification_policy
                            review_selected = (
                                policy in {"existing_guard", "always_verify"}
                                or policy == "audit_only"
                                or (
                                    policy == "periodic_verify"
                                    and high_impact_proposal_count
                                    % config.verification_period
                                    == 0
                                )
                                # Witness construction below decides whether a
                                # selective intervention is warranted.
                                or policy == "dcore_selective"
                            )
                        if high_impact and review_selected:
                            live_verification_count += 1
                            requirements = self._guard_requirements(
                                petri_net=petri_net,
                                actor_id=actor_id,
                                action=intent.action,
                                args=intent.args,
                                scope=intent.scope or scope_from_args(intent.args),
                                phase=phase,
                                world_time=env.time_manager.time(),
                            )
                            verification_knowledge = stores[actor_id]
                            verification_evidence_scope = "actor_local_prefix"
                            guard_result = self.guard.evaluate(
                                actor=actor_specs[actor_id],
                                action=intent.action,
                                requirements=requirements,
                                knowledge=verification_knowledge,
                                logical_time=env.time_manager.time(),
                                evidence_ids=provenance_ids,
                                transport_closed=transport.watermark(actor_id),
                                transport_gap=(
                                    any(
                                        stores[actor_id].latest(
                                            requirement.fact_key,
                                            scope=requirement.scope,
                                            at=env.time_manager.time(),
                                        )
                                        is None
                                        for requirement in requirements
                                    )
                                    and bool(transport.snapshot()["dropped"])
                                ),
                            )
                            verifier_result = None
                            if config.live_verification_policy in {
                                "always_verify",
                                "periodic_verify",
                            }:
                                verifier_result = call_live_verifier(
                                    actor_id=actor_id,
                                    decision_id=decision.event_id,
                                    action=intent.action,
                                    arguments=intent.args,
                                    world_time=env.time_manager.time(),
                                )
                                verifier_verdict = GuardVerdict(
                                    verifier_result["verdict"]
                                )
                                guard_result = guard_result.model_copy(
                                    update={
                                        "verdict": verifier_verdict,
                                        "reasons": (
                                            f"model_verifier:{verifier_result['reason']}",
                                        ),
                                        "supporting_item_ids": tuple(
                                            verifier_result["required_fact_ids"]
                                        ),
                                    }
                                )
                                verification_evidence_scope = (
                                    "actor_local_delivered_and_prompted_prefix"
                                )
                            dcore_live_application = None
                            if (
                                config.live_verification_policy == "dcore_selective"
                                and guard_result.verdict != GuardVerdict.ALLOW
                                and intent_key not in live_repair_attempted_keys
                            ):
                                from are.simulation.distributed.evaluation_adapters import (
                                    DiagnosticWitness,
                                )
                                from are.simulation.distributed.repair_study import (
                                    _default_observation_action,
                                    enumerate_repairs,
                                    load_repair_catalogue,
                                    select_repair,
                                )

                                failed_requirement = next(
                                    (
                                        requirement
                                        for requirement in requirements
                                        if guard_result.requirement_verdicts.get(
                                            requirement.requirement_id
                                        )
                                        != RequirementVerdict.TRUE
                                    ),
                                    None,
                                )
                                live_repair_attempted_keys.add(intent_key)
                                if failed_requirement is not None:
                                    required_scope = failed_requirement.scope
                                    local_same_key = [
                                        item
                                        for item in stores[actor_id].items
                                        if item.fact_key
                                        == failed_requirement.fact_key
                                    ]
                                    local_scoped = [
                                        item
                                        for item in local_same_key
                                        if required_scope is None
                                        or item.scope == required_scope
                                    ]
                                    selected_item = max(
                                        local_scoped,
                                        key=lambda item: (
                                            item.observed_at,
                                            item.learned_at,
                                            item.item_id,
                                        ),
                                        default=None,
                                    )
                                    other_holders = [
                                        (holder, item)
                                        for holder, store in stores.items()
                                        if holder != actor_id
                                        for item in store.items
                                        if item.fact_key
                                        == failed_requirement.fact_key
                                        and (
                                            required_scope is None
                                            or item.scope == required_scope
                                        )
                                    ]
                                    prompt_ids = set(
                                        getattr(
                                            controller,
                                            "last_prompt_item_ids",
                                            (),
                                        )
                                    )
                                    if selected_item is None:
                                        mechanism = (
                                            "incorrect_scope"
                                            if local_same_key
                                            else "failed_delivery"
                                            if other_holders
                                            else "missing_observation"
                                        )
                                        selected_item = (
                                            other_holders[0][1]
                                            if other_holders
                                            else None
                                        )
                                    elif (
                                        selected_item.valid_until is not None
                                        and env.time_manager.time()
                                        > selected_item.valid_until
                                    ):
                                        mechanism = "expired_evidence"
                                    elif selected_item.item_id not in prompt_ids:
                                        mechanism = "context_omission"
                                    else:
                                        mechanism = (
                                            "failure_to_use_available_evidence"
                                        )
                                    witness = DiagnosticWitness(
                                        witness_id=stable_digest(
                                            [
                                                decision.event_id,
                                                failed_requirement.requirement_id,
                                                mechanism,
                                            ]
                                        )[:24],
                                        decision_id=decision.event_id,
                                        obligation_id=(
                                            f"live:{failed_requirement.requirement_id}"
                                        ),
                                        prerequisite_id=(
                                            failed_requirement.requirement_id
                                        ),
                                        actor_id=actor_id,
                                        mechanism=mechanism,
                                        fact_key=failed_requirement.fact_key,
                                        fact_version_ids=(
                                            (selected_item.item_id,)
                                            if selected_item is not None
                                            else ()
                                        ),
                                        source_version_id=(
                                            selected_item.item_id
                                            if selected_item is not None
                                            else None
                                        ),
                                        root_support_group=(
                                            f"live:{failed_requirement.requirement_id}"
                                        ),
                                        determination="supported",
                                        target_scope=required_scope,
                                        decision_time=env.time_manager.time(),
                                        deadline=(
                                            failed_requirement.deadline
                                            if failed_requirement.deadline is not None
                                            else scenario_horizon
                                        ),
                                        prerequisite=failed_requirement.model_dump(
                                            mode="json"
                                        ),
                                        guard_reason=";".join(
                                            guard_result.reasons
                                        ),
                                        evidence_available_to_actor=(
                                            selected_item in stores[actor_id].items
                                            if selected_item is not None
                                            else False
                                        ),
                                        evidence_delivered=(
                                            selected_item in stores[actor_id].items
                                            if selected_item is not None
                                            else False
                                        ),
                                        evidence_in_prompt=(
                                            selected_item is not None
                                            and selected_item.item_id in prompt_ids
                                        ),
                                    )
                                    native_action = _default_observation_action(
                                        failed_requirement.fact_key
                                    )
                                    observer_by_fact = {}
                                    native_action_by_fact = {}
                                    if native_action is not None:
                                        try:
                                            owner = gateway.metadata(native_action)[
                                                "owner"
                                            ]
                                        except ValueError:
                                            native_action = None
                                        else:
                                            observer_by_fact[
                                                failed_requirement.fact_key
                                            ] = owner
                                            native_action_by_fact[
                                                failed_requirement.fact_key
                                            ] = native_action
                                    source_actor_by_version = {
                                        item.item_id: holder
                                        for holder, store in stores.items()
                                        for item in store.items
                                    }
                                    repair_catalogue = load_repair_catalogue()
                                    candidates = enumerate_repairs(
                                        witness,
                                        native_cost_by_primitive={
                                            str(key): float(value)
                                            for key, value in repair_catalogue[
                                                "costs"
                                            ].items()
                                        },
                                        duration_by_primitive={
                                            "acquire_observation": 0.001,
                                            "refresh_observation": 0.001,
                                            "route_evidence": config.delay,
                                            "redeliver_evidence": config.delay,
                                            "restore_context": 0.0,
                                            "request_reconsideration": 0.0,
                                        },
                                        observer_by_fact=observer_by_fact,
                                        source_actor_by_version=(
                                            source_actor_by_version
                                        ),
                                        native_action_by_fact=(
                                            native_action_by_fact
                                        ),
                                        response_lead_time_seconds=0.0,
                                    )
                                    selected_repair = select_repair(candidates)
                                    if (
                                        selected_repair is not None
                                        and selected_repair.feasibility == "feasible"
                                    ):
                                        dcore_live_application = apply_locked_repair(
                                            phase, selected_repair
                                        )
                                        intervention_status = "applied"
                                    else:
                                        intervention_status = (
                                            "infeasible"
                                            if selected_repair is not None
                                            else "rejected"
                                        )
                                    live_interventions.append(
                                        {
                                            "intent_id": decision.event_id,
                                            "policy": "dcore_selective",
                                            "status": intervention_status,
                                            "witness": witness.model_dump(
                                                mode="json"
                                            ),
                                            "candidate": (
                                                selected_repair.model_dump(
                                                    mode="json"
                                                )
                                                if selected_repair
                                                else None
                                            ),
                                            "application": dcore_live_application,
                                        }
                                    )
                                    journal_append(
                                        "live_intervention",
                                        live_interventions[-1],
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
                                    "verification_evidence_scope": (
                                        verification_evidence_scope
                                    ),
                                    "verifier": verifier_result,
                                },
                                season_phase=phase,
                            )
                        should_execute = (
                            guard_result is None
                            or config.enforcement_mode in {"off", "audit"}
                            or config.live_verification_policy == "audit_only"
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
                            if intent.action == "SystemApp__advance_time":
                                requested_advance = (
                                    int(intent.args.get("seconds", 0))
                                    + int(intent.args.get("minutes", 0)) * 60
                                    + int(intent.args.get("hours", 0)) * 3600
                                    + int(intent.args.get("days", 0)) * 86400
                                )
                            else:
                                requested_advance = 0
                            projected_completion = (
                                env.time_manager.time()
                                + requested_advance
                                + 0.001
                            )
                            if projected_completion > scenario_horizon:
                                recorder.record(
                                    EventKind.ACTION,
                                    actor_id,
                                    logical_time + 0.02,
                                    world_time=env.time_manager.time(),
                                    action=intent.action,
                                    args=intent.args,
                                    causal_parents=(decision.event_id,),
                                    decision_context_id=decision.event_id,
                                    status="horizon_overrun_rejected",
                                    payload={
                                        "scenario_horizon": scenario_horizon,
                                        "projected_completion": projected_completion,
                                        "blocked_before_farmare": True,
                                    },
                                    season_phase=phase,
                                )
                                result_payload.update(
                                    {
                                        "executed": False,
                                        "error": "operation would cross scenario horizon",
                                        "horizon_overrun_rejected": True,
                                    }
                                )
                                finalize_decision_record()
                                previous_results[actor_id] = result_payload
                                result_payload["result_world_time"] = (
                                    env.time_manager.time()
                                )
                                controller.observe(result_payload)
                                continue
                            for fact in adapter.authoritative_snapshot(
                                source_event_id=decision.event_id,
                                farmare_event_id=None,
                                action=intent.action,
                                args=intent.args,
                                world_time=env.time_manager.time(),
                                phase=phase,
                            ):
                                recorder.add_fact_version(fact)
                            if metadata["write"]:
                                journal_append(
                                    "native_write_intent",
                                    {
                                        "intent_id": decision.event_id,
                                        "actor_id": actor_id,
                                        "action": intent.action,
                                        "arguments": intent.args,
                                        "world_time": env.time_manager.time(),
                                    },
                                )
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
                            if metadata["write"]:
                                journal_append(
                                    "native_write_receipt",
                                    {
                                        "intent_id": decision.event_id,
                                        "actor_id": actor_id,
                                        "action": intent.action,
                                        "arguments": execution.arguments,
                                        "receipt": execution.receipt(
                                            decision.event_id
                                        ),
                                        "result": execution.result,
                                        "error": execution.error,
                                        "world_time": env.time_manager.time(),
                                    },
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
                                    "high_impact": high_impact,
                                    "tool_error": execution.error,
                                    "execution_receipt": execution.receipt(
                                        decision.event_id
                                    ),
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
                            if metadata["write"]:
                                self._record_observation_facts(
                                    recorder=recorder,
                                    store=stores[actor_id],
                                    all_stores=stores,
                                    shared=(
                                        config.visibility_mode == "shared_blackboard"
                                        or team.topology.kind == "shared_blackboard"
                                    ),
                                    evidence_actor=False,
                                    adapter=adapter,
                                    actor_id=actor_id,
                                    action_event_id=action_event.event_id,
                                    farmare_event_id=farmare_id,
                                    action="dcore.tool_receipt",
                                    args=execution.arguments,
                                    result=execution.receipt(decision.event_id),
                                    logical_time=logical_time + 0.028,
                                    world_time=env.time_manager.time(),
                                    phase=phase,
                                    provenance_ids=provenance_ids,
                                    receipt_fact_key=f"tool_receipt:{intent.action}",
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
                                native_execution_errors.append(execution.error)
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
                                    "execution_receipt": execution.receipt(
                                        decision.event_id
                                    ),
                                    "guard_verdict": (
                                        guard_result.verdict.value
                                        if guard_result
                                        else None
                                    ),
                                }
                            )
                    except Exception as exc:
                        infrastructure_errors.append(
                            f"{actor_id} gateway: {type(exc).__name__}: {exc}"
                        )
                        result_payload.update({"executed": False, "error": str(exc)})

                finalize_decision_record()
                previous_results[actor_id] = result_payload
                result_payload["result_world_time"] = env.time_manager.time()
                controller.observe(result_payload)
                deliver_due()

            if all(controller.is_complete() for controller in controllers.values()):
                current_outcome = _farm_outcome(
                    farm_world,
                    initial_inventory,
                    combine_grain_kg=(
                        float(tractor_app.get_state().get("grain_bin_kg", 0.0))
                        if tractor_app is not None
                        else 0.0
                    ),
                    scenario_horizon=float(scenario.start_time + scenario.duration),
                )
                duties_complete = bool(
                    current_outcome["harvest_complete"]
                    and current_outcome["storage_complete"]
                    and current_outcome["postharvest_compliant"]
                )
                for actor_id in actor_ids:
                    if actor_id not in finished:
                        termination_by_actor[actor_id] = (
                            "successful_completion"
                            if duties_complete
                            else "budget_termination"
                        )
                        recorder.record(
                            EventKind.FINISH,
                            actor_id,
                            logical_time + 0.1,
                            world_time=env.time_manager.time(),
                            action="dcore.activation_terminated",
                            status="ok" if duties_complete else "error",
                            payload={
                                "reason": termination_by_actor[actor_id],
                                "limit": "controller_cap",
                                "duties_complete": duties_complete,
                            },
                        )
                        finished.add(actor_id)
                break
            if finished == set(actor_ids):
                break
        else:
            for actor_id in actor_ids:
                if actor_id not in finished:
                    termination_by_actor[actor_id] = "budget_termination"
                    recorder.record(
                        EventKind.FINISH,
                        actor_id,
                        logical_time + 0.1,
                        world_time=env.time_manager.time(),
                        action="dcore.activation_terminated",
                        status="error",
                        payload={
                            "reason": "budget_termination",
                            "limit": "max_logical_steps",
                        },
                    )

        if replay_checkpoint is not None and replay_checkpoint_verification is None:
            raise ValueError("requested replay checkpoint was never reached")

        termination_phase = adapter.phase("", env.time_manager.time())
        for actor in actor_ids:
            recorder.record(
                EventKind.WATERMARK,
                actor,
                logical_time + 0.5,
                world_time=env.time_manager.time(),
                action="farm.transport_closed",
                payload={"pending": len(transport.pending_for(actor))},
                season_phase=termination_phase,
            )
        controller_termination_world_time = float(env.time_manager.time())
        physics_continuation = {
            "status": "not_needed",
            "from_world_time": controller_termination_world_time,
            "to_world_time": controller_termination_world_time,
            "advanced_seconds": 0.0,
            "management_actions_added": 0,
        }
        if controller_termination_world_time < scenario_horizon:
            farm_world.prepare_for_time_advance(controller_termination_world_time)
            delta = scenario_horizon - controller_termination_world_time
            env.time_manager.add_offset(delta)
            physics_result = farm_world.advance_physics_time(scenario_horizon)
            physics_continuation = {
                "status": "advanced_to_horizon",
                "from_world_time": controller_termination_world_time,
                "to_world_time": scenario_horizon,
                "advanced_seconds": delta,
                "management_actions_added": 0,
                "physics_result": physics_result,
            }
            recorder.record(
                EventKind.WORLD_EFFECT,
                "world",
                logical_time + 0.75,
                world_time=scenario_horizon,
                action="farm.physics_only_horizon_continuation",
                status="ok",
                payload=physics_continuation,
                season_phase=adapter.phase("", scenario_horizon),
            )
            journal_append(
                "physics_only_horizon_continuation", physics_continuation
            )
        combine_grain_kg = (
            float(tractor_app.get_state().get("grain_bin_kg", 0.0))
            if tractor_app is not None
            else 0.0
        )
        outcome = _farm_outcome(
            farm_world,
            initial_inventory,
            combine_grain_kg=combine_grain_kg,
            scenario_horizon=scenario_horizon,
        )
        recorder.record(
            EventKind.FINISH,
            "world",
            logical_time + 1.0,
            world_time=env.time_manager.time(),
            action="farm.season_complete"
            if outcome["harvest_complete"] and outcome["storage_complete"]
            else "farm.execution_terminated",
            season_phase="storage"
            if outcome["storage_complete"]
            else termination_phase,
        )
        validation = scenario.validate(env)
        from are.simulation.distributed.pilot_budget import current_request_usage

        request_usage = current_request_usage()
        if request_usage is None:
            from are.simulation.distributed.llm_budget import active_team_budget

            request_budget = active_team_budget()
            if request_budget and request_budget.provider_records:
                records = request_budget.provider_records
                request_usage = {
                    "accounting_basis": "provider_requests_v1",
                    "provider_request_count": len(records),
                    "provider_prompt_tokens": sum(
                        r["prompt_tokens"] or 0 for r in records
                    ),
                    "provider_completion_tokens": sum(
                        r["completion_tokens"] or 0 for r in records
                    ),
                    "provider_usage_unknown_count": sum(
                        r["status"] == "usage_unknown" for r in records
                    ),
                    "provider_reserved_or_used_tokens": request_budget.tokens,
                    "provider_requests": records,
                    "provider_use_by_purpose": {
                        purpose: sum(
                            record.get("purpose", "unknown") == purpose
                            for record in records
                        )
                        for purpose in sorted(
                            {record.get("purpose", "unknown") for record in records}
                        )
                    },
                }
        if request_usage is not None:
            outcome.update(request_usage)
        from are.simulation.distributed.recovery import native_retry_profile

        outcome["native_execution_retries"] = native_retry_profile(recorder.events)
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
        # Nonactivation is a valid assigned treatment outcome. Keep the trace
        # and denominator; transport diagnostics distinguish targeting from failure.
        total_model_calls = sum(
            item.llm_input_log_id is not None or item.model_name is not None
            for item in recorder.decisions
        )
        total_model_tokens = sum(item.total_tokens or 0 for item in recorder.decisions)
        if request_usage is not None:
            total_model_calls = request_usage["provider_request_count"]
            total_model_tokens = (
                request_usage["provider_prompt_tokens"]
                + request_usage["provider_completion_tokens"]
            )
        total_model_duration = sum(
            item.completion_duration or 0.0 for item in recorder.decisions
        )
        from are.simulation.distributed.llm_budget import summarize_budget_usage

        budget_status = summarize_budget_usage(
            {
                actor: {
                    "model_call_count": sum(
                        item.actor_id == actor
                        and (
                            item.llm_input_log_id is not None
                            or item.model_name is not None
                        )
                        for item in recorder.decisions
                    ),
                    "total_tokens": sum(
                        item.total_tokens or 0
                        for item in recorder.decisions
                        if item.actor_id == actor
                    ),
                }
                for actor in actor_ids
            },
            max_calls=team.team_call_budget or config.max_model_calls,
            max_tokens=team.team_token_budget,
            per_agent_calls=team.per_agent_call_budget,
            per_agent_tokens=team.per_agent_token_budget,
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
                "coordination_contract": COORDINATION_CONTRACT,
                "capability_cards": {
                    actor: capability_cards(team, actor) for actor in actor_ids
                },
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
                **budget_status,
                "exogenous_world_digest": exogenous_world_digest,
                "exogenous_world_days": len(exogenous_manifest.get("weather_days", [])),
                "effective_weather_seed": exogenous_manifest.get(
                    "effective_weather_seed"
                ),
                "schedule_id": stable_digest(activation_manifest)[:16],
                "activation_manifest": activation_manifest,
                "live_verification_policy": config.live_verification_policy,
                "high_impact_proposal_count": high_impact_proposal_count,
                "live_verification_count": live_verification_count,
                "live_repair_deferral_count": live_repair_deferral_count,
                "live_interventions": live_interventions,
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
                "infrastructure_errors": infrastructure_errors,
                "infrastructure_failure": bool(infrastructure_errors),
                "native_execution_errors": native_execution_errors,
                "termination_by_actor": termination_by_actor,
                "controller_termination_world_time": controller_termination_world_time,
                "physics_only_continuation": physics_continuation,
                "replay_checkpoint_verification": replay_checkpoint_verification,
                "replay_repair_application": replay_repair_application,
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
            and outcome["postharvest_compliant"]
            and validation.success is True
            and not controller_errors
            and not infrastructure_errors
            and "premature_abandonment" not in termination_by_actor.values()
        )
        journal_append(
            "run_completed",
            {
                "outcome": outcome,
                "termination_by_actor": termination_by_actor,
                "world_time": env.time_manager.time(),
            },
        )
        trace = recorder.build(
            configuration={
                **config.model_dump(mode="json"),
                **(
                    {
                        "review_attestation": json.loads(
                            Path(config.scientific_gate_manifest).read_text()
                        ).get("review_attestation"),
                        "review_protocol_digest": json.loads(
                            Path(config.scientific_gate_manifest).read_text()
                        ).get("analysis_protocol_digest"),
                    }
                    if config.scientific_gate_manifest
                    else {}
                ),
                "runtime_semantics": "agent_driven_native_tools_v3",
                "team_spec": team.model_dump(mode="json"),
                "team_spec_digest": team_digest(team),
                "coordination_contract": COORDINATION_CONTRACT,
                "capability_cards": {
                    actor: capability_cards(team, actor) for actor in actor_ids
                },
                "team_size": len(actor_ids),
                "topology_edges": list(team.topology.edges),
                "activation_policy_resolved": team.activation_policy,
                "role_refinement": (
                    refinement.model_dump(mode="json") if refinement else None
                ),
                "controller_task_briefing_policy": (
                    "nonprocedural_public_task_contract_v1"
                ),
                "public_task_contract": self._task_briefing(
                    config.scenario_id, process_spec
                ),
                "controller_task_briefing_digest": stable_digest(
                    self._task_briefing(config.scenario_id, process_spec)
                ),
                "exogenous_world_digest": exogenous_world_digest,
                "exogenous_world_manifest": exogenous_manifest,
                "app_random_seeds": app_random_seeds,
                "committed_branches": committed_branches,
                "branch_commitment_evidence": branch_evidence,
                "committed_world_context": committed_world_context,
                "controller_adapter": (
                    "native_base_agent_step_v1"
                    if config.controller_mode
                    in {"llm", "mock_llm", "response_replay"}
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
            decision_guards_at_execution=process_spec is not None,
            unresolved_branches=frozenset(
                b.branch_id
                for b in petri_net.exogenous_branches
                if b.branch_id not in committed_branches
            )
            if process_spec is not None
            else frozenset(),
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
        receipt_fact_key: str | None = None,
    ) -> None:
        if receipt_fact_key is not None:
            from are.simulation.distributed.farm_adapter import ExtractedFact

            # A receipt is a durable historical execution record. It conveys no
            # current agronomic readiness and cannot satisfy phase evidence.
            facts = (
                ExtractedFact(receipt_fact_key, result, scope_from_args(args), None),
            )
        else:
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
        # Capture evaluator-only truth at observation time and returned coverage.
        # Opaque sensor IDs cannot supply that coverage before the read. Binding
        # by timestamp alone could select an unrelated request or regional max.
        observation_origins = {}
        if receipt_fact_key is None:
            for scope in dict.fromkeys(fact.scope for fact in facts):
                if not isinstance(scope, tuple):
                    continue
                for source in adapter.authoritative_snapshot(
                    source_event_id=action_event_id,
                    farmare_event_id=farmare_event_id,
                    action=action,
                    args={"start_ridge": scope[0], "end_ridge": scope[1]},
                    world_time=world_time,
                    phase=phase,
                ):
                    recorder.add_fact_version(source)
                    observation_origins[(source.fact_key, source.scope)] = source
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
            authoritative_origin = observation_origins.get((fact.key, fact.scope))
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
        selected_items = stores[actor_id].for_keys(intent.claim_fact_keys)
        available_keys = {item.fact_key for item in selected_items}
        unresolved.extend(
            key for key in intent.claim_fact_keys if key not in available_keys
        )
        for item in selected_items:
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
                    else (config.delay or 4 * 86400)
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
        selectors = config.fault_target_ids
        if selectors and all(target.startswith("selector:") for target in selectors):
            for target in selectors:
                parts = target.split(":")
                if len(parts) != 5 or not parts[-1].isdigit() or int(parts[-1]) < 1:
                    raise ValueError(
                        "fault selector must be selector:phase:sender:recipient:positive-send-order"
                    )
            if config.fault == "reorder":
                if len(selectors) != 2:
                    raise ValueError(
                        "reorder needs two frozen route/send-order selectors"
                    )
                return FaultSchedule(
                    by_route_selector={
                        selectors[0]: rules["reorder"],
                        selectors[1]: FaultRule(),
                    }
                )
            if config.fault == "mixed":
                if len(selectors) != 3:
                    raise ValueError(
                        "mixed needs frozen delay, drop and duplicate selectors"
                    )
                return FaultSchedule(
                    by_route_selector=dict(
                        zip(
                            selectors,
                            (
                                FaultRule(FaultMode.DELAY, delay=4 * 86400),
                                rules["drop"],
                                rules["duplicate"],
                            ),
                        )
                    )
                )
            return FaultSchedule(
                by_route_selector={target: rule for target in selectors}
            )
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
        process_spec: Any | None = None,
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

                task_briefing = self._task_briefing(config.scenario_id, process_spec)
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
                    harvest_deadlines=petri_net.metadata.get(
                        "reference_harvest_deadlines"
                    )
                    if petri_net.metadata.get("reference_harvest_policy")
                    in {"authored_harvest_calendar_v5", "authored_opening_harvest_v6"}
                    else None,
                    harvest_openings=petri_net.metadata.get(
                        "reference_harvest_openings"
                    )
                    if petri_net.metadata.get("reference_harvest_policy")
                    == "authored_opening_harvest_v6"
                    else None,
                    retry_wet_soil=petri_net.metadata.get("reference_harvest_policy")
                    == "authored_opening_harvest_v6",
                    harvest_clock_actor=next(
                        (
                            a.actor_id
                            for a in team.actors
                            if "SystemApp__advance_time" in a.permitted_actions
                        ),
                        actor,
                    ),
                )
                for actor in actor_ids
            }
        if config.controller_mode == "replay":
            from are.simulation.distributed.controllers import (
                CoordinatedReplayController,
                TraceReplayCoordinator,
            )

            payload = json.loads(
                Path(config.replay_trace or "").read_text(encoding="utf-8")
            )
            replay = DistributedTrace.model_validate(payload)
            complete_when_exhausted = {
                event.actor_id
                for event in replay.events
                if event.action == "dcore.activation_terminated"
                and event.payload.get("limit") == "controller_cap"
            }
            coordinator = TraceReplayCoordinator(
                replay.decisions,
                complete_when_exhausted=complete_when_exhausted,
            )
            return {
                actor: CoordinatedReplayController(actor, coordinator)
                for actor in actor_ids
            }
        if config.controller_mode == "response_replay":
            from are.simulation.agents.agent_builder import AgentBuilder
            from are.simulation.agents.agent_config_builder import AgentConfigBuilder
            from are.simulation.distributed.controllers import (
                FarmAREBaseAgentController,
                RecordedReactResponseEngine,
                RecordedThenLiveEngine,
            )
            from are.simulation.distributed.journal import load_journal

            trace_path = Path(config.replay_trace or "")
            payload = json.loads(trace_path.read_text(encoding="utf-8"))
            source_trace = DistributedTrace.model_validate(payload)
            journal_path = trace_path.parent / "progress.dcore.jsonl"
            if not journal_path.is_file():
                raise ValueError("response replay requires the durable source journal")
            requests: dict[str, list[dict[str, Any]]] = {
                actor: [] for actor in actor_ids
            }
            pending_prompts: dict[str, list[str]] = {actor: [] for actor in actor_ids}
            accepted_phases: dict[str, list[str | None]] = {
                actor: [
                    decision.season_phase
                    for decision in source_trace.decisions
                    if decision.actor_id == actor
                ]
                for actor in actor_ids
            }
            accepted_index = {actor: 0 for actor in actor_ids}
            for record in load_journal(journal_path):
                item = record.get("payload", {})
                actor = str(item.get("actor_id", ""))
                if actor not in requests:
                    continue
                if record.get("kind") == "model_request":
                    pending_prompts[actor].append(stable_digest(item.get("prompt", [])))
                elif record.get("kind") == "model_response":
                    if not pending_prompts[actor]:
                        raise ValueError("model response lacks its preceding request")
                    phase = None
                    if item.get("proposal_status") == "accepted":
                        index = accepted_index[actor]
                        if index >= len(accepted_phases[actor]):
                            raise ValueError("accepted response lacks a source decision")
                        phase = accepted_phases[actor][index]
                        accepted_index[actor] += 1
                    requests[actor].append(
                        {
                            "actor_id": actor,
                            "prompt_digest": pending_prompts[actor].pop(0),
                            "response": item.get("response", ""),
                            "metadata": item.get("metadata", {}),
                            "season_phase": phase,
                        }
                    )
            if any(pending_prompts.values()) or any(
                accepted_index[actor] != len(accepted_phases[actor])
                for actor in actor_ids
            ):
                raise ValueError("source journal has incomplete model exchanges")

            class _RecordedEngineBuilder:
                def __init__(self, engine):
                    self.engine = engine

                def create_engine(self, engine_config, mock_responses=None):
                    return self.engine

            task_briefing = self._task_briefing(config.scenario_id, process_spec)
            built = {}
            for actor, spec in actor_specs.items():
                recorded_engine = RecordedReactResponseEngine(actor, requests[actor])
                engine = recorded_engine
                if config.replay_live_suffix:
                    from are.simulation.agents.are_simulation_agent_config import (
                        LLMEngineConfig,
                    )
                    from are.simulation.agents.llm.llm_engine_builder import (
                        LLMEngineBuilder,
                    )

                    model = config.model_by_actor.get(actor)
                    if not model:
                        raise ValueError(
                            f"live repaired suffix requires model_by_actor[{actor!r}]"
                        )
                    provider = config.provider_by_actor.get(actor, "openai")
                    live_engine = LLMEngineBuilder().create_engine(
                        LLMEngineConfig(
                            model_name=model,
                            provider=(
                                "openai-json" if provider == "openai" else provider
                            ),
                            endpoint=config.endpoint_by_actor.get(actor),
                            temperature=config.temperature_by_actor.get(actor, 0.0),
                        ),
                        mock_responses=(
                            list(config.replay_live_responses_by_actor[actor])
                            if actor in config.replay_live_responses_by_actor
                            else None
                        ),
                    )
                    if hasattr(live_engine, "model_config"):
                        live_engine.model_config.max_tokens = config.max_output_tokens
                    engine = RecordedThenLiveEngine(
                        actor, recorded_engine, live_engine
                    )
                family = config.agent_family_by_actor.get(actor, "default")
                agent_config = AgentConfigBuilder().build(family)
                base_config = agent_config.get_base_agent_config()
                base_config.use_custom_logger = False
                base_config.history_window = config.history_window_by_actor.get(actor, 8)
                base_config.system_prompt = self._distributed_prompt(
                    str(base_config.system_prompt), spec, task_briefing, team
                )
                farmare_agent = AgentBuilder(_RecordedEngineBuilder(engine)).build(
                    agent_config, env=env
                )
                built[actor] = FarmAREBaseAgentController(
                    farmare_agent,
                    max_decisions=config.max_logical_steps,
                    # The prefix must expose the original call cap.  The suffix
                    # allocation is installed only after checkpoint verification.
                    max_model_calls=max(1, len(requests[actor])),
                    max_total_tokens=None,
                )
            return built
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
            if config.engineering_llm_pilot:
                from are.simulation.distributed.pilot_budget import (
                    require_pilot_request_scope,
                )

                require_pilot_request_scope()
            families = AgentConfigBuilder()
            built = {}
            task_briefing = self._task_briefing(config.scenario_id, process_spec)
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
                farmare_agent.react_agent.distributed_prompt_tokens = (
                    config.max_prompt_tokens
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
    def _task_briefing(scenario_id: str, process_spec: Any | None = None) -> str:
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
            briefing = briefings[scenario_id]
        except KeyError as error:
            raise ValueError(
                f"no non-procedural task briefing for {scenario_id!r}"
            ) from error
        if process_spec is None:
            return briefing
        windows = [
            {
                "phase": window.phase,
                "start_utc": datetime.fromtimestamp(
                    window.start_world_time, timezone.utc
                ).isoformat(),
                "end_utc_exclusive": datetime.fromtimestamp(
                    window.end_world_time, timezone.utc
                ).isoformat(),
            }
            for window in process_spec.phase_windows
        ]
        policies = []
        for policy in process_spec.information_policies:
            scopes = []
            for requirement in policy.requirements:
                if requirement.scope is not None and requirement.scope not in scopes:
                    scopes.append(requirement.scope)
            policies.append(
                {
                    "policy_id": policy.policy_id,
                    "actor_id": policy.actor_id,
                    "phases": list(policy.phases),
                    "action_patterns": list(policy.action_patterns),
                    "scopes": scopes,
                }
            )
        contract = {
            "specification_digest": process_spec.digest,
            "phase_windows": windows,
            "information_policy_assignments": policies,
            "interpretation": (
                "Phase windows are part of the public task contract. Keep scoped "
                "high-impact decisions inside their declared half-open window."
            ),
        }
        return (
            briefing
            + "\n<declared_process_contract>\n"
            + json.dumps(contract, sort_keys=True)
            + "\n</declared_process_contract>"
        )

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
            + "\n\n<coordination_contract>\n"
            + COORDINATION_CONTRACT
            + "\n"
            + json.dumps(capability_cards(team, spec.actor_id), sort_keys=True)
            + "\n</coordination_contract>"
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
            item = knowledge.latest(fact_key, scope=requirement.scope, at=world_time)
            if item is None:
                verdicts[fact_key] = RequirementVerdict.UNKNOWN
                continue
            supporting.append(item.item_id)
            stale = item.valid_until is not None and world_time > item.valid_until
            stale = stale or (
                requirement.max_age is not None
                and world_time - item.observed_at > requirement.max_age
            )
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
