from are.simulation.scenario_runner import _resolve_a2a_agent_name_for_app
from are.simulation.scenarios.config import ScenarioRunnerConfig


class WeatherApp:
    pass


class SensorApp:
    pass


class TractorApp:
    pass


class FieldOpsApp:
    pass


class FarmWorldApp:
    pass


class DroneApp:
    pass


class RobotApp:
    pass


class UnknownApp:
    pass


def test_generic_policy_uses_single_app_agent():
    config = ScenarioRunnerConfig(
        a2a_policy="generic",
        a2a_app_agent="default_app_agent",
    )
    assert _resolve_a2a_agent_name_for_app(WeatherApp(), config) == "default_app_agent"
    assert _resolve_a2a_agent_name_for_app(UnknownApp(), config) == "default_app_agent"


def test_typed_policy_routes_to_expected_experts():
    config = ScenarioRunnerConfig(
        a2a_policy="typed_experts",
        a2a_app_agent="default_app_agent",
    )
    assert _resolve_a2a_agent_name_for_app(WeatherApp(), config) == "weather_expert_app_agent"
    assert _resolve_a2a_agent_name_for_app(SensorApp(), config) == "sensor_expert_app_agent"
    assert _resolve_a2a_agent_name_for_app(TractorApp(), config) == "machinery_expert_app_agent"
    assert _resolve_a2a_agent_name_for_app(FieldOpsApp(), config) == "machinery_expert_app_agent"
    assert _resolve_a2a_agent_name_for_app(FarmWorldApp(), config) == "operations_expert_app_agent"
    assert _resolve_a2a_agent_name_for_app(DroneApp(), config) == "operations_expert_app_agent"
    assert _resolve_a2a_agent_name_for_app(RobotApp(), config) == "operations_expert_app_agent"
    assert _resolve_a2a_agent_name_for_app(UnknownApp(), config) == "default_app_agent"
