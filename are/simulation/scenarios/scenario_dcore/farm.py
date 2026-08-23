"""Scout/Operator D-CORE episode backed by the real FarmARE wet-June state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from are.simulation.apps.farm_world import (
    FarmWorldApp,
    SensorApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.data_handler.exporter import JsonScenarioExporter
from are.simulation.distributed.controllers import MockLLMController, ScriptedController
from are.simulation.distributed.models import (
    ActorSpec,
    AgentIntent,
    CausalEdgeSpec,
    DistributedRunnerConfig,
    DistributedTaskSpec,
    FactRequirement,
    IntentKind,
    ReferenceEventSpec,
)
from are.simulation.environment import Environment
from are.simulation.scenarios.scenario_dcore.base import (
    DistributedScenarioBundle,
    WorldEventDefinition,
)
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.l2_l1_splits.hb_wetjune_disease_recheck_after_fungicide.scenario_l1_hb_wetjune_recheck_mid_fungicide_20_43_action import (
    ScenarioL1HbWetjuneRecheckMidFungicide2043Action,
)

TARGET_SCOPE = (20, 43)


def farm_spec() -> DistributedTaskSpec:
    scout_actions = (
        "farm.observe_weather",
        "farm.reobserve_weather",
        "farm.observe_trafficability",
        "farm.confirm_disease",
    )
    return DistributedTaskSpec(
        task_id="farm_wetjune_fungicide_dcore",
        actors=(
            ActorSpec(
                actor_id="scout",
                role="Diagnostic evidence owner",
                permitted_actions=scout_actions,
                observable_facts=(
                    "spray_window_open",
                    "trafficable",
                    "disease_confirmed",
                    "target_scope",
                ),
            ),
            ActorSpec(
                actor_id="operator",
                role="Resource and treatment owner",
                permitted_actions=(
                    "farm.check_resources",
                    "farm.apply_fungicide",
                    "farm.verify_treatment",
                ),
                observable_facts=("resources_ready",),
            ),
        ),
        events=(
            ReferenceEventSpec(
                event_id="weather", actor_id="scout", action="farm.observe_weather"
            ),
            ReferenceEventSpec(
                event_id="traffic",
                actor_id="scout",
                action="farm.observe_trafficability",
            ),
            ReferenceEventSpec(
                event_id="disease",
                actor_id="scout",
                action="farm.confirm_disease",
                scope=TARGET_SCOPE,
            ),
            ReferenceEventSpec(
                event_id="resources", actor_id="operator", action="farm.check_resources"
            ),
            ReferenceEventSpec(
                event_id="treatment",
                actor_id="operator",
                action="farm.apply_fungicide",
                args={
                    "start_ridge": 20,
                    "end_ridge": 43,
                    "liters_per_ridge": 3.4,
                },
                scope=TARGET_SCOPE,
                required=False,
            ),
            ReferenceEventSpec(
                event_id="verification",
                actor_id="operator",
                action="farm.verify_treatment",
                scope=TARGET_SCOPE,
            ),
        ),
        causal_edges=(
            CausalEdgeSpec(source="weather", target="treatment", reason="spray window"),
            CausalEdgeSpec(
                source="traffic", target="treatment", reason="trafficability"
            ),
            CausalEdgeSpec(source="disease", target="treatment", reason="diagnosis"),
            CausalEdgeSpec(source="resources", target="treatment", reason="resources"),
        ),
        fact_requirements=tuple(
            FactRequirement(
                requirement_id=f"farm:{fact}",
                action="farm.apply_fungicide",
                actor_id="operator",
                fact_key=fact,
                expected_value=(TARGET_SCOPE if fact == "target_scope" else True),
                scope=(
                    TARGET_SCOPE
                    if fact in {"disease_confirmed", "target_scope"}
                    else None
                ),
                max_age=4.0,
                deadline=8.0,
                require_evidence=True,
            )
            for fact in (
                "spray_window_open",
                "trafficable",
                "disease_confirmed",
                "target_scope",
                "resources_ready",
            )
        ),
        observability={
            "spray_window_open": ("scout",),
            "trafficable": ("scout",),
            "disease_confirmed": ("scout",),
            "target_scope": ("scout",),
            "resources_ready": ("operator",),
        },
        ownership={
            **{action: "scout" for action in scout_actions},
            "farm.check_resources": "operator",
            "farm.apply_fungicide": "operator",
            "farm.verify_treatment": "operator",
        },
        outcome_adapter="farmare_wetjune",
    )


def build_farm_bundle(config: DistributedRunnerConfig) -> DistributedScenarioBundle:
    scenario = ScenarioL1HbWetjuneRecheckMidFungicide2043Action(
        seed=config.scheduler_seed
    )
    scenario.initialize()
    env = Environment()
    env.time_manager.reset(start_time=scenario.start_time or 0)
    env.register_apps(scenario.apps or [])
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    tractor = scenario.get_typed_app(TractorApp)
    system = scenario.get_typed_app(SystemApp)
    state: dict[str, Any] = {
        "attempted": False,
        "treated": False,
        "blocked": False,
        "tool_errors": [],
        "verification": None,
        "continuation": None,
    }
    world_truth: dict[str, Any] = {
        "spray_window_open": True,
        "trafficable": True,
        "disease_confirmed": True,
        "target_scope": TARGET_SCOPE,
    }

    def last_event_id() -> str | None:
        events = env.event_log.list_view()
        return events[-1].event_id if events else None

    def observe_weather(args: dict[str, Any], logical_time: float) -> dict[str, Any]:
        result = weather.get_current_weather()
        return {
            "result": result,
            "farmare_event_id": last_event_id(),
            "observations": [
                {
                    "fact_key": "spray_window_open",
                    "value": bool(weather.is_sprayable),
                    "observed_at": logical_time,
                    "valid_until": logical_time + 8,
                }
            ],
        }

    def observe_trafficability(
        args: dict[str, Any], logical_time: float
    ) -> dict[str, Any]:
        result = sensor.read_soil_sensors()
        return {
            "result": result,
            "farmare_event_id": last_event_id(),
            "observations": [
                {
                    "fact_key": "trafficable",
                    "value": True,
                    "observed_at": logical_time,
                    "valid_until": logical_time + 8,
                }
            ],
        }

    def confirm_disease(args: dict[str, Any], logical_time: float) -> dict[str, Any]:
        result = farm_world.get_ridge_range_state(*TARGET_SCOPE)
        common = {
            "scope": TARGET_SCOPE,
            "observed_at": logical_time,
            "valid_until": logical_time + 8,
        }
        return {
            "result": result,
            "farmare_event_id": last_event_id(),
            "observations": [
                {"fact_key": "disease_confirmed", "value": True, **common},
                {"fact_key": "target_scope", "value": TARGET_SCOPE, **common},
            ],
        }

    def check_resources(args: dict[str, Any], logical_time: float) -> dict[str, Any]:
        inventory = farm_world.get_inventory()
        status = tractor.get_status()
        ready = not inventory.get("error") and not status.get("error")
        return {
            "result": {"inventory": inventory, "tractor": status},
            "farmare_event_id": last_event_id(),
            "observations": [
                {
                    "fact_key": "resources_ready",
                    "value": ready,
                    "observed_at": logical_time,
                    "valid_until": logical_time + 10,
                }
            ],
        }

    def apply_fungicide(args: dict[str, Any], logical_time: float) -> dict[str, Any]:
        state["attempted"] = True
        load = tractor.load_fungicide(83.3)
        results = [load]
        if not load.get("error"):
            for start, end in ((20, 29), (30, 39), (40, 43)):
                results.append(tractor.apply_fungicide(start, end, 3.4))
        errors = [result["error"] for result in results if result.get("error")]
        state["tool_errors"].extend(errors)
        state["treated"] = not errors
        violated = []
        if not world_truth["spray_window_open"]:
            violated.append("farm:spray_window_open")
        if not world_truth["trafficable"]:
            violated.append("farm:trafficable")
        if world_truth["target_scope"] != TARGET_SCOPE:
            violated.append("farm:target_scope")
        return {
            "result": {"load": load, "applications": results[1:]},
            "farmare_event_id": last_event_id(),
            "violated_requirements": violated,
            "stale_facts": [
                requirement.removeprefix("farm:") for requirement in violated
            ],
            "harmful": bool(violated),
        }

    def verify_treatment(args: dict[str, Any], logical_time: float) -> dict[str, Any]:
        result = farm_world.get_ridge_range_state(*TARGET_SCOPE)
        state["verification"] = {
            "logical_time": logical_time,
            "treated": state["treated"],
            "ridge_count": len(result.get("ridges", [])),
        }
        return {
            "result": result,
            "farmare_event_id": last_event_id(),
        }

    def close_spray_window(logical_time: float) -> dict[str, Any]:
        current = weather.get_current_weather_snapshot()
        weather.set_weather(
            date=current.get("date", datetime.now(timezone.utc).date().isoformat()),
            temp_c=float(current.get("temp_c", 20)),
            humidity_pct=float(current.get("humidity_pct", 90)),
            wind_speed_ms=12.0,
            rainfall_mm=8.0,
            solar_radiation=float(current.get("solar_radiation", 100)),
            forecast=current.get("forecast", []),
        )
        world_truth["spray_window_open"] = False
        return {
            "effect": "spray_window_closed",
            "fact_key": "spray_window_open",
            "value": False,
        }

    def deteriorate_trafficability(logical_time: float) -> dict[str, Any]:
        for ridge_id in range(64):
            farm_world.get_ridge(ridge_id).soil_vwc = 0.48
        weather.set_avg_soil_vwc(0.48)
        world_truth["trafficable"] = False
        return {
            "effect": "trafficability_deteriorated",
            "fact_key": "trafficable",
            "value": False,
        }

    def change_disease_scope(logical_time: float) -> dict[str, Any]:
        world_truth["target_scope"] = (30, 43)
        return {
            "effect": "disease_scope_changed",
            "fact_key": "target_scope",
            "value": (30, 43),
        }

    scout_intents = [
        AgentIntent(kind=IntentKind.ACT, action="farm.observe_weather"),
        AgentIntent(kind=IntentKind.ACT, action="farm.observe_trafficability"),
        AgentIntent(
            kind=IntentKind.ACT, action="farm.confirm_disease", scope=TARGET_SCOPE
        ),
        AgentIntent(
            kind=IntentKind.SEND,
            recipient="operator",
            text="Diagnostic evidence supports targeted fungicide treatment on ridges 20-43.",
            claim_fact_keys=(
                "spray_window_open",
                "trafficable",
                "disease_confirmed",
                "target_scope",
            ),
        ),
        AgentIntent(kind=IntentKind.ACT, action="farm.reobserve_weather"),
        AgentIntent(
            kind=IntentKind.SEND,
            recipient="operator",
            text=(
                "Updated weather evidence; trafficability and target scope "
                "remain unresolved pending reinspection."
            ),
            claim_fact_keys=("spray_window_open",),
            unresolved_requirements=("trafficable", "target_scope"),
        ),
        AgentIntent(kind=IntentKind.FINISH),
    ]
    operator_intents = [
        AgentIntent(kind=IntentKind.ACT, action="farm.check_resources"),
        AgentIntent(kind=IntentKind.WAIT, wait=3.0),
        AgentIntent(
            kind=IntentKind.ACT,
            action="farm.apply_fungicide",
            args={"start_ridge": 20, "end_ridge": 43, "liters_per_ridge": 3.4},
        ),
        AgentIntent(
            kind=IntentKind.ACT,
            action="farm.verify_treatment",
            scope=TARGET_SCOPE,
        ),
        AgentIntent(kind=IntentKind.FINISH),
    ]
    controller_cls = MockLLMController if config.controller_mode == "mock_llm" else None
    controllers = {
        "scout": controller_cls("scout", scout_intents)
        if controller_cls
        else ScriptedController(scout_intents),
        "operator": controller_cls("operator", operator_intents)
        if controller_cls
        else ScriptedController(operator_intents),
    }
    faulted = config.fault in {"delay_past_deadline", "drop", "reorder"}
    world_events = (
        [
            WorldEventDefinition(
                logical_time=3.25, name="close_spray_window", handler=close_spray_window
            ),
            WorldEventDefinition(
                logical_time=3.5,
                name="deteriorate_trafficability",
                handler=deteriorate_trafficability,
            ),
            WorldEventDefinition(
                logical_time=3.75,
                name="change_disease_scope",
                handler=change_disease_scope,
            ),
        ]
        if faulted
        else []
    )

    def outcome() -> dict[str, Any]:
        overview = farm_world.get_farm_overview()
        ridge_state = farm_world.get_ridge_range_state(0, 63)
        yield_potentials = [
            float(ridge.get("yield_potential", 0.0))
            for ridge in ridge_state.get("ridges", [])
            if ridge.get("yield_potential") is not None
        ]
        success = state["treated"] and not state["tool_errors"]
        if state["blocked"]:
            success = True
        return {
            **state,
            "success": success,
            "farm_overview": overview,
            "mean_yield_potential": (
                sum(yield_potentials) / len(yield_potentials)
                if yield_potentials
                else None
            ),
            "biological_yield_kg": overview.get("inventory", {}).get(
                "harvest_grain_kg"
            ),
            "marketable_yield_kg": overview.get("inventory", {}).get(
                "warehouse_grain_kg"
            ),
            "yield_is_final": bool(
                overview.get("ridges_overview")
                and all(ridge.get("harvested") for ridge in overview["ridges_overview"])
            ),
            "continuation": state["continuation"],
        }

    def continuation() -> dict[str, Any]:
        """Fixed, controller-independent physics continuation through harvest."""
        current = weather.get_current_weather_snapshot()
        weather.set_weather(
            date=current.get("date", datetime.now(timezone.utc).date().isoformat()),
            temp_c=22.0,
            humidity_pct=55.0,
            wind_speed_ms=2.0,
            rainfall_mm=0.0,
            solar_radiation=220.0,
            forecast=current.get("forecast", []),
        )
        advanced = system.advance_time(days=100)
        detached = tractor.detach_implement()
        attached = tractor.attach_implement("harvester")
        harvest_results: list[dict[str, Any]] = []
        unload_results: list[dict[str, Any]] = []
        for start in range(0, 64, 4):
            harvest = tractor.harvest(start, start + 3)
            harvest_results.append(harvest)
            if not harvest.get("error"):
                unload_results.append(tractor.unload_grain())
        errors = [
            result["error"]
            for result in (*harvest_results, *unload_results)
            if result.get("error")
        ]
        state["continuation"] = {
            "advanced": advanced,
            "detach": detached,
            "attach": attached,
            "harvest_passes": len(harvest_results),
            "unload_passes": len(unload_results),
            "errors": errors,
        }
        return state["continuation"]

    def source_trace_builder() -> str:
        return JsonScenarioExporter().export_to_json(
            env,
            scenario,
            scenario.scenario_id,
            model_id="dcore",
            agent_id="distributed",
        )

    return DistributedScenarioBundle(
        spec=farm_spec(),
        controllers=controllers,
        tool_handlers={
            "farm.observe_weather": observe_weather,
            "farm.reobserve_weather": observe_weather,
            "farm.observe_trafficability": observe_trafficability,
            "farm.confirm_disease": confirm_disease,
            "farm.check_resources": check_resources,
            "farm.apply_fungicide": apply_fungicide,
            "farm.verify_treatment": verify_treatment,
        },
        world_events=world_events,
        outcome=outcome,
        continuation=continuation,
        source_trace_builder=source_trace_builder,
    )
