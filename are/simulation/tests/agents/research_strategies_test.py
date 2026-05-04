from are.simulation.agents.are_simulation_agent_config import ResearchAgentProfileConfig
from are.simulation.agents.research_suite.research_strategies import (
    ResearchStrategyCoordinator,
    StrategyLogSnapshot,
)


def test_rewoo_plan_parsing_and_fallback():
    profile = ResearchAgentProfileConfig(
        family_id="farm_rewoo_modular",
        rewoo={"enabled": True, "max_plan_steps": 8, "max_replans": 1},
    )
    coordinator = ResearchStrategyCoordinator(profile)
    telemetry: dict[str, object] = {}
    coordinator.reset(telemetry)

    context = coordinator.build_context(
        "1) Check weather\n2) Read sensors\n3) Apply irrigation where needed"
    )
    assert "ReWOO plan-work-solve" in context
    assert telemetry["planner_calls"] == 1
    assert telemetry["planned_steps"] == 3
    assert telemetry["executed_steps"] == 1
    assert telemetry["plan_parse_failures"] == 0

    coordinator.reset(telemetry)
    fallback_context = coordinator.build_context("go")
    assert "ReWOO plan fallback" in fallback_context
    assert telemetry["plan_parse_failures"] == 1


def test_tree_search_scoring_and_backtrack_is_deterministic():
    profile = ResearchAgentProfileConfig(
        family_id="farm_tree_search",
        tree_search={
            "enabled": True,
            "branch_factor": 3,
            "search_depth": 2,
            "max_expansions": 6,
        },
    )
    coordinator = ResearchStrategyCoordinator(profile)
    telemetry: dict[str, object] = {}
    coordinator.reset(telemetry)

    initial_context = coordinator.build_context(
        "Check weather and sensors. Then irrigate dry ridges."
    )
    assert "Execute candidate 1 first" in initial_context
    assert telemetry["branches_generated"] == 3
    assert telemetry["branches_scored"] == 3
    assert telemetry["backtracks"] == 0

    coordinator.consume_snapshot(
        StrategyLogSnapshot(tool_calls=[], observations=[], llm_outputs=[], errors=["E"])
    )
    retry_context = coordinator.build_context(
        "Check weather and sensors. Then irrigate dry ridges."
    )
    assert "Execute candidate 2 first" in retry_context
    assert telemetry["backtracks"] == 1


def test_critic_refiner_revision_bounds_and_blocked_actions():
    profile = ResearchAgentProfileConfig(
        family_id="farm_critic_refiner",
        critic={
            "enabled": True,
            "max_revision_cycles": 5,
            "enforce_precondition_checks": True,
        },
    )
    coordinator = ResearchStrategyCoordinator(profile)
    telemetry: dict[str, object] = {}
    coordinator.reset(telemetry)

    blocked_context = coordinator.build_context("Apply pesticide to ridge 10.")
    assert "Action blocked until preconditions verified: yes" in blocked_context
    assert telemetry["critic_cycles"] == 1
    assert telemetry["revision_cycles"] == 2
    assert telemetry["blocked_actions"] == 1

    allowed_context = coordinator.build_context(
        "Check weather forecast and verify inventory, then apply pesticide to ridge 10."
    )
    assert "Action blocked until preconditions verified: no" in allowed_context
    assert telemetry["blocked_actions"] == 1


def test_graph_memory_retrieval_and_contradiction_detection():
    profile = ResearchAgentProfileConfig(
        family_id="farm_graph_memory",
        graph_memory={
            "enabled": True,
            "max_nodes": 10,
            "retrieval_top_k": 5,
            "contradiction_check": True,
        },
    )
    coordinator = ResearchStrategyCoordinator(profile)
    telemetry: dict[str, object] = {}
    coordinator.reset(telemetry)

    coordinator.consume_snapshot(
        StrategyLogSnapshot(
            tool_calls=["WeatherApp__get_forecast"],
            observations=["no rain expected tomorrow", "rain expected tomorrow"],
            llm_outputs=[],
            errors=[],
        )
    )
    context = coordinator.build_context("rain status and spray window")
    assert "Contradiction alert: conflicting state evidence detected." in context
    assert int(telemetry["graph_nodes"]) >= 3
    assert int(telemetry["graph_edges"]) >= 2
    assert int(telemetry["graph_retrieval_hits"]) >= 1
    assert telemetry["contradiction_alerts"] == 1
