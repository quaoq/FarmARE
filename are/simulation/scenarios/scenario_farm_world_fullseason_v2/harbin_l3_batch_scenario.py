from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    DroneApp,
    FarmWorldApp,
    FieldOpsApp,
    RobotApp,
    SensorApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.physics.soil_engine import SoilHydraulicModifier
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
    HEINONG84_SPACING_CM,
    RIDGE_WIDTH_M,
    PriorFieldHistoryPreset,
    advance_days,
    apply_prior_field_history,
    collect_event_graph,
    configure_common_field,
    harbin_start_time,
    harvest_range,
    install_common_farm_apps,
    plant_range,
    spray_blocks,
)
from are.simulation.types import EventRegisterer

ActionKind = Literal[
    "fertigation",
    "irrigation",
    "fungicide",
    "insecticide",
    "herbicide",
    "mechanical_weed",
    "replant",
]


@dataclass(frozen=True)
class PlantingZone:
    label: str
    start: int
    end: int
    seed_type: str
    spacing_cm: float = HEINONG84_SPACING_CM
    wait_days_before: int = 0


@dataclass(frozen=True)
class ScenarioAction:
    window: str
    kind: ActionKind
    start: int
    end: int
    amount: float = 0.0
    water_mm: float = 1.5
    hours: float = 1.0
    liters_per_ridge: float = 4.0
    reason: str = "routine_supported_action"
    target_wait_days: int = 3
    manual: bool = False


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    class_name: str
    slug: str
    profile_name: str
    description: str
    briefing_text: str
    cultivar: str
    seed_stocks: dict[str, int]
    primary_seed: str
    detailed_briefing_text: str | None = None
    start_date: str = "2026-05-05"
    density_target_plants_m2: float = 23.0
    initial_vwc: float = 0.30
    pesticide_liters: float = 1200.0
    fertilizer_kg: float = 2600.0
    base_fertilizer_kg: float = 360.0
    tractor_fuel_l: float = 190.0
    management_regime: dict[str, Any] = field(default_factory=dict)
    postharvest_market: dict[str, Any] = field(default_factory=dict)
    planting_zones: tuple[PlantingZone, ...] = field(default_factory=tuple)
    enforce_planting_windows: bool = False
    prior_histories: tuple[tuple[str, int, int], ...] = field(default_factory=tuple)
    custom_histories: tuple[tuple[PriorFieldHistoryPreset, int, int], ...] = field(
        default_factory=tuple
    )
    hydraulic_modifiers: tuple[tuple[int, int, SoilHydraulicModifier], ...] = field(
        default_factory=tuple
    )
    actions: tuple[ScenarioAction, ...] = field(default_factory=tuple)
    zones: tuple[tuple[str, int, int], ...] = field(default_factory=tuple)
    waits: dict[str, int] = field(
        default_factory=lambda: {
            "emergence": 15,
            "r1": 24,
            "mid": 18,
            "r5": 24,
            "harvest": 66,
        }
    )
    harvest_zones: tuple[tuple[str, int, int], ...] = (("whole_field", 0, 63),)
    harvest_zone_waits: dict[str, int] = field(default_factory=dict)
    postharvest_drying_zones: tuple[str, ...] = ()


def init_batch_apps(scenario: Scenario, spec: ScenarioSpec) -> None:
    scenario_start_date = date.fromisoformat(spec.start_date)
    scenario.start_time = harbin_start_time(
        scenario_start_date.year,
        scenario_start_date.month,
        scenario_start_date.day,
    )
    install_common_farm_apps(scenario, thermal=True, field_ops=True)
    configure_common_field(
        scenario,
        profile_name=spec.profile_name,
        cultivar=spec.cultivar,
        seed_stocks=spec.seed_stocks,
        start_date=spec.start_date,
        density_target_plants_m2=spec.density_target_plants_m2,
        pesticide_liters=spec.pesticide_liters,
        fertilizer_kg=spec.fertilizer_kg,
        tractor_fuel_l=spec.tractor_fuel_l,
        initial_vwc=spec.initial_vwc,
    )
    farm_world = scenario.get_typed_app(FarmWorldApp)
    if spec.management_regime:
        farm_world.configure_management_regime(**spec.management_regime)
    if spec.postharvest_market:
        farm_world.configure_postharvest_market(**spec.postharvest_market)
    _configure_planting_windows(farm_world, spec)
    for preset, start, end in spec.prior_histories:
        apply_prior_field_history(scenario, preset, start_ridge=start, end_ridge=end)
    for history, start, end in spec.custom_histories:
        apply_prior_field_history(scenario, history, start_ridge=start, end_ridge=end)
    from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_batch_catalog import (
        NORMAL_BLACK_SOIL,
    )

    modifiers: dict[int, SoilHydraulicModifier] = {
        ridge_id: NORMAL_BLACK_SOIL for ridge_id in range(farm_world.num_ridges)
    }
    for start, end, modifier in spec.hydraulic_modifiers:
        for ridge_id in range(start, end + 1):
            modifiers[ridge_id] = merge_hydraulic_modifier(
                NORMAL_BLACK_SOIL, modifier
            )
    farm_world.physics.soil.set_hydraulic_modifiers(modifiers)


def _configure_planting_windows(farm_world: FarmWorldApp, spec: ScenarioSpec) -> None:
    if not spec.enforce_planting_windows:
        return
    windows = _planting_windows_from_spec(spec)
    farm_world.configure_planting_windows(windows)


def _planting_windows_from_spec(spec: ScenarioSpec) -> list[dict[str, Any]]:
    zones = spec.planting_zones or (
        PlantingZone("whole_field", 0, 63, spec.primary_seed),
    )
    base_date = date.fromisoformat(spec.start_date)
    elapsed_days = 0
    windows: list[dict[str, Any]] = []
    for zone in zones:
        elapsed_days += max(0, int(zone.wait_days_before))
        windows.append(
            {
                "label": zone.label,
                "start": zone.start,
                "end": zone.end,
                "seed_type": zone.seed_type,
                "earliest_date": (base_date + timedelta(days=elapsed_days)).isoformat(),
            }
        )
    return windows


def _format_zone_plan(spec: ScenarioSpec) -> str:
    zones = spec.planting_zones or (
        PlantingZone("whole_field", 0, 63, spec.primary_seed),
    )
    windows_by_label = {
        item["label"]: item for item in _planting_windows_from_spec(spec)
    }
    parts: list[str] = []
    for zone in zones:
        window = windows_by_label[zone.label]
        timing = (
            f"，最早播种日期 {window['earliest_date']}"
            if spec.enforce_planting_windows
            else f"，建议从 {spec.start_date} 起在工具返回允许时播种"
        )
        parts.append(
            f"{zone.label}: ridges {zone.start}-{zone.end}, seed_type={zone.seed_type}, "
            f"seed_spacing_cm={zone.spacing_cm:.1f}{timing}"
        )
    return "；".join(parts)


def _with_planting_briefing_details(text: str, spec: ScenarioSpec) -> str:
    """Make agent-facing planting density/window instructions unambiguous."""

    density_note = (
        "\n\n播种参数约束：所有播种必须按下面的 seed_spacing_cm 执行；"
        "不要只根据 plants/m2 或万株/ha 自行反推株距。"
        f"\n分区播种计划：{_format_zone_plan(spec)}。"
    )
    if spec.enforce_planting_windows and len(spec.planting_zones) > 1:
        density_note += (
            "\n错期播种约束：不同分区不能同日提前播完；每个分区只能在其"
            "最早播种日期当天或之后，且天气、土壤水分/温度和设备状态允许时播种。"
        )
    return f"{text.rstrip()}{density_note}"


def merge_hydraulic_modifier(
    base: SoilHydraulicModifier,
    override: SoilHydraulicModifier,
) -> SoilHydraulicModifier:
    """Apply a local soil override on top of the V2 normal black-soil preset."""

    return SoilHydraulicModifier(
        root_depth_m=override.root_depth_m
        if override.root_depth_m is not None
        else base.root_depth_m,
        field_capacity_vwc=override.field_capacity_vwc
        if override.field_capacity_vwc is not None
        else base.field_capacity_vwc,
        top_drainage_rate=override.top_drainage_rate
        if override.top_drainage_rate is not None
        else base.top_drainage_rate,
        root_drainage_rate=override.root_drainage_rate
        if override.root_drainage_rate is not None
        else base.root_drainage_rate,
        rainfall_capture_efficiency=override.rainfall_capture_efficiency
        if override.rainfall_capture_efficiency is not None
        else base.rainfall_capture_efficiency,
        irrigation_efficiency=override.irrigation_efficiency
        if override.irrigation_efficiency is not None
        else base.irrigation_efficiency,
        max_infiltration_mm_day=override.max_infiltration_mm_day
        if override.max_infiltration_mm_day is not None
        else base.max_infiltration_mm_day,
    )


def build_batch_events(scenario: Scenario, spec: ScenarioSpec) -> None:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    mavic = scenario.get_typed_app(DroneApp, "Mavic3M")
    matrice = scenario.get_typed_app(DroneApp, "Matrice4T")
    robot = scenario.get_typed_app(RobotApp, "Robot0")
    tractor = scenario.get_typed_app(TractorApp)
    field_ops = scenario.get_typed_app(FieldOpsApp)
    system = scenario.get_typed_app(SystemApp)

    with EventRegisterer.capture_mode():
        briefing_text = (
            spec.detailed_briefing_text
            if getattr(scenario, "detailed_briefing", True)
            and spec.detailed_briefing_text
            else spec.briefing_text
        )
        briefing_text = _with_planting_briefing_details(briefing_text, spec)
        briefing = (
            aui.send_message_to_agent(content=briefing_text)
            .with_id("briefing")
            .depends_on(None, delay_seconds=5)
        )
        prev = _prep_field(weather, sensor, farm_world, tractor, briefing, spec)
        for zone in spec.planting_zones or (
            PlantingZone("whole_field", 0, 63, spec.primary_seed),
        ):
            if zone.wait_days_before > 0:
                prev = advance_days(
                    scenario,
                    prev,
                    zone.wait_days_before,
                    f"o_wait_before_{zone.label}_planting",
                )
            prev = _plant_zone(weather, sensor, tractor, prev, zone, f"o_{zone.label}")
            prev = (
                farm_world.commit_daily_physics()
                .oracle()
                .with_id(f"o_commit_{zone.label}_planting")
                .depends_on(prev, delay_seconds=1)
            )
            prev = _after_named_step(scenario, prev, f"after_{zone.label}_planting")

        prev = _wait_check_window(
            scenario,
            prev,
            spec,
            "emergence",
            weather,
            sensor,
            farm_world,
            mavic,
            matrice,
            robot,
            system,
        )
        prev = _run_window_actions(
            scenario,
            spec,
            "emergence",
            prev,
            weather,
            sensor,
            farm_world,
            mavic,
            matrice,
            robot,
            tractor,
            field_ops,
            system,
        )
        for window in ("r1", "mid", "r5"):
            prev = _wait_check_window(
                scenario,
                prev,
                spec,
                window,
                weather,
                sensor,
                farm_world,
                mavic,
                matrice,
                robot,
                system,
            )
            prev = _run_window_actions(
                scenario,
                spec,
                window,
                prev,
                weather,
                sensor,
                farm_world,
                mavic,
                matrice,
                robot,
                tractor,
                field_ops,
                system,
            )

        prev = advance_days(
            scenario, prev, spec.waits.get("harvest", 42), "o_wait_harvest"
        )
        for label, start, end in spec.harvest_zones:
            zone_wait_days = spec.harvest_zone_waits.get(label, 0)
            if zone_wait_days > 0:
                prev = advance_days(
                    scenario,
                    prev,
                    zone_wait_days,
                    f"o_wait_{label}_harvest_zone",
                )
            prev = _harvest_checks(weather, sensor, farm_world, prev, label, start, end)
            prev = harvest_range(
                tractor,
                farm_world,
                prev,
                start_ridge=start,
                end_ridge=end,
                id_prefix=f"o_{label}",
                dry_after_harvest=_requires_postharvest_drying(spec, label),
            )
            prev = _after_named_step(scenario, prev, f"after_{label}_harvest")

        prev = (
            aui.send_message_to_user(
                content=f"Completed expert oracle flow for {spec.scenario_id}."
            )
            .oracle()
            .with_id("o_report")
            .depends_on(prev, delay_seconds=1)
        )
        scenario.events = collect_event_graph(briefing)


def _prep_field(
    weather: WeatherApp,
    sensor: SensorApp,
    farm_world: FarmWorldApp,
    tractor: TractorApp,
    briefing: Any,
    spec: ScenarioSpec,
) -> Any:
    slug = spec.slug
    prev = (
        weather.get_current_weather()
        .oracle()
        .with_id(f"o_{slug}_weather_before_prep")
        .depends_on(briefing, delay_seconds=1)
    )
    prev = (
        weather.get_forecast(days=5)
        .oracle()
        .with_id(f"o_{slug}_forecast_before_prep")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        sensor.read_soil_sensors()
        .oracle()
        .with_id(f"o_{slug}_soil_before_prep")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        farm_world.get_inventory()
        .oracle()
        .with_id(f"o_{slug}_inventory_before_prep")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        tractor.get_status()
        .oracle()
        .with_id(f"o_{slug}_tractor_before_prep")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        tractor.attach_implement("grader")
        .oracle()
        .with_id(f"o_{slug}_attach_grader")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        tractor.level()
        .oracle()
        .with_id(f"o_{slug}_level_field")
        .depends_on(prev, delay_seconds=2)
    )
    prev = (
        tractor.detach_implement()
        .oracle()
        .with_id(f"o_{slug}_detach_grader")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        tractor.load_fertilizer(spec.base_fertilizer_kg)
        .oracle()
        .with_id(f"o_{slug}_load_base_fertilizer")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        tractor.base_fertilize()
        .oracle()
        .with_id(f"o_{slug}_apply_base_fertilizer")
        .depends_on(prev, delay_seconds=2)
    )
    prev = (
        tractor.attach_implement("furrower")
        .oracle()
        .with_id(f"o_{slug}_attach_furrower")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        tractor.form_ridges(RIDGE_WIDTH_M)
        .oracle()
        .with_id(f"o_{slug}_form_ridges")
        .depends_on(prev, delay_seconds=2)
    )
    return (
        tractor.detach_implement()
        .oracle()
        .with_id(f"o_{slug}_detach_furrower")
        .depends_on(prev, delay_seconds=1)
    )


def _plant_zone(
    weather: WeatherApp,
    sensor: SensorApp,
    tractor: TractorApp,
    prev: Any,
    zone: PlantingZone,
    prefix: str,
) -> Any:
    prev = (
        weather.get_current_weather()
        .oracle()
        .with_id(f"{prefix}_plant_weather_check")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        weather.get_forecast(days=3)
        .oracle()
        .with_id(f"{prefix}_plant_forecast_check")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        sensor.read_soil_sensors()
        .oracle()
        .with_id(f"{prefix}_plant_soil_check")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        tractor.get_status()
        .oracle()
        .with_id(f"{prefix}_tractor_before_planting")
        .depends_on(prev, delay_seconds=1)
    )
    return plant_range(
        tractor,
        prev,
        start_ridge=zone.start,
        end_ridge=zone.end,
        seed_type=zone.seed_type,
        spacing_cm=zone.spacing_cm,
        id_prefix=prefix,
    )


def _wait_check_window(
    scenario: Scenario,
    prev: Any,
    spec: ScenarioSpec,
    window: str,
    weather: WeatherApp,
    sensor: SensorApp,
    farm_world: FarmWorldApp,
    mavic: DroneApp,
    matrice: DroneApp,
    robot: RobotApp,
    system: SystemApp,
) -> Any:
    prev = advance_days(scenario, prev, spec.waits.get(window, 1), f"o_wait_{window}")
    prev = (
        weather.get_current_weather()
        .oracle()
        .with_id(f"o_{window}_weather_check")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        weather.get_forecast(days=4)
        .oracle()
        .with_id(f"o_{window}_forecast_check")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        farm_world.get_farm_overview()
        .oracle()
        .with_id(f"o_{window}_farm_overview")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        sensor.read_soil_sensors()
        .oracle()
        .with_id(f"o_{window}_soil_sensor_check")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        sensor.read_canopy_sensors()
        .oracle()
        .with_id(f"o_{window}_canopy_sensor_check")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        robot.check_status()
        .oracle()
        .with_id(f"o_{window}_robot_status")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        robot.charge()
        .oracle()
        .with_id(f"o_{window}_charge_robot_before_routine")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        system.advance_time(hours=1)
        .oracle()
        .with_id(f"o_{window}_wait_robot_charge")
        .depends_on(prev, delay_seconds=1)
    )
    if window == "emergence":
        prev = (
            robot.inspect_emergence(0, 15)
            .oracle()
            .with_id(f"o_{window}_routine_ground_emergence_sample")
            .depends_on(prev, delay_seconds=2)
        )
    else:
        prev = (
            robot.inspect_crop_health(0, 15)
            .oracle()
            .with_id(f"o_{window}_routine_ground_health_sample")
            .depends_on(prev, delay_seconds=2)
        )
    return _after_named_step(scenario, prev, f"after_{window}_routine_check")


def _run_window_actions(
    scenario: Scenario,
    spec: ScenarioSpec,
    window: str,
    prev: Any,
    weather: WeatherApp,
    sensor: SensorApp,
    farm_world: FarmWorldApp,
    mavic: DroneApp,
    matrice: DroneApp,
    robot: RobotApp,
    tractor: TractorApp,
    field_ops: FieldOpsApp,
    system: SystemApp,
) -> Any:
    for index, action in enumerate(a for a in spec.actions if a.window == window):
        prefix = f"o_{window}_{action.kind}_{index}_{action.start}_{action.end}"
        if index > 0:
            prev = (
                system.advance_time(days=3)
                .oracle()
                .with_id(f"{prefix}_wait_between_targeted_actions")
                .depends_on(prev, delay_seconds=1)
            )
        prev = _diagnose_target(
            prev,
            action,
            prefix,
            weather,
            sensor,
            farm_world,
            mavic,
            matrice,
            robot,
            system,
        )
        if action.kind == "fertigation":
            prev = (
                farm_world.apply_fertigation(
                    action.start, action.end, action.amount, action.water_mm
                )
                .oracle()
                .with_id(f"{prefix}_apply_fertigation")
                .depends_on(prev, delay_seconds=2)
            )
        elif action.kind == "irrigation":
            prev = (
                field_ops.irrigate(action.start, action.end, action.hours)
                .oracle()
                .with_id(f"{prefix}_irrigate")
                .depends_on(prev, delay_seconds=2)
            )
            prev = (
                system.advance_time(hours=6)
                .oracle()
                .with_id(f"{prefix}_wait_irrigation_response")
                .depends_on(prev, delay_seconds=1)
            )
        elif action.kind == "fungicide":
            prev = _load_and_spray(tractor, prev, action, prefix, chemical="fungicide")
        elif action.kind == "insecticide":
            if action.manual:
                prev = _manual_pesticide_spray(field_ops, prev, action, prefix)
            else:
                prev = _load_and_spray(
                    tractor, prev, action, prefix, chemical="insecticide"
                )
        elif action.kind == "herbicide":
            prev = _load_and_spray(tractor, prev, action, prefix, chemical="herbicide")
        elif action.kind == "mechanical_weed":
            prev = _mechanical_blocks(tractor, prev, action, prefix)
        elif action.kind == "replant":
            prev = _replant_blocks(tractor, prev, action, prefix, spec.primary_seed)
        prev = _after_named_step(scenario, prev, f"after_{prefix}")
    return prev


def _requires_postharvest_drying(spec: ScenarioSpec, harvest_label: str) -> bool:
    """Return whether this pre-reviewed harvest batch needs drying."""

    return harvest_label in spec.postharvest_drying_zones


def _diagnose_target(
    prev: Any,
    action: ScenarioAction,
    prefix: str,
    weather: WeatherApp,
    sensor: SensorApp,
    farm_world: FarmWorldApp,
    mavic: DroneApp,
    matrice: DroneApp,
    robot: RobotApp,
    system: SystemApp,
) -> Any:
    prev = (
        weather.get_current_weather()
        .oracle()
        .with_id(f"{prefix}_weather_before_action")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        weather.get_forecast(days=3)
        .oracle()
        .with_id(f"{prefix}_forecast_before_action")
        .depends_on(prev, delay_seconds=1)
    )
    if action.reason == "wet_soil_trafficability_delay":
        prev = (
            sensor.read_soil_sensors()
            .oracle()
            .with_id(f"{prefix}_initial_soil_trafficability_check")
            .depends_on(prev, delay_seconds=1)
        )
        prev = (
            sensor.read_canopy_sensors()
            .oracle()
            .with_id(f"{prefix}_initial_canopy_weed_signal_check")
            .depends_on(prev, delay_seconds=1)
        )
        prev = (
            farm_world.get_ridge_range_state(action.start, action.end)
            .oracle()
            .with_id(f"{prefix}_initial_range_state_before_delay")
            .depends_on(prev, delay_seconds=1)
        )
        prev = (
            system.advance_time(days=action.target_wait_days)
            .oracle()
            .with_id(f"{prefix}_wait_for_soil_trafficability_window")
            .depends_on(prev, delay_seconds=1)
        )
        prev = (
            weather.get_current_weather()
            .oracle()
            .with_id(f"{prefix}_weather_after_soil_drying_wait")
            .depends_on(prev, delay_seconds=1)
        )
        prev = (
            weather.get_forecast(days=3)
            .oracle()
            .with_id(f"{prefix}_forecast_after_soil_drying_wait")
            .depends_on(prev, delay_seconds=1)
        )
    elif action.target_wait_days > 0:
        prev = (
            system.advance_time(days=action.target_wait_days)
            .oracle()
            .with_id(f"{prefix}_wait_for_target_action_window")
            .depends_on(prev, delay_seconds=1)
        )
    prev = (
        sensor.read_soil_sensors()
        .oracle()
        .with_id(f"{prefix}_soil_before_action")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        sensor.read_canopy_sensors()
        .oracle()
        .with_id(f"{prefix}_canopy_before_action")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        farm_world.get_ridge_range_state(action.start, action.end)
        .oracle()
        .with_id(f"{prefix}_range_state_before_action")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        mavic.charge()
        .oracle()
        .with_id(f"{prefix}_charge_mavic_before_target_survey")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        system.advance_time(hours=1)
        .oracle()
        .with_id(f"{prefix}_wait_mavic_charge")
        .depends_on(prev, delay_seconds=1)
    )
    survey_start, survey_end = (
        (0, 63)
        if action.reason == "routine_whole_field_scouting"
        else (action.start, action.end)
    )
    prev = (
        mavic.fly_survey(survey_start, survey_end)
        .oracle()
        .with_id(f"{prefix}_target_ndvi_survey")
        .depends_on(prev, delay_seconds=2)
    )
    if action.kind == "irrigation":
        prev = (
            matrice.charge()
            .oracle()
            .with_id(f"{prefix}_charge_matrice_before_thermal")
            .depends_on(prev, delay_seconds=1)
        )
        prev = (
            system.advance_time(hours=1)
            .oracle()
            .with_id(f"{prefix}_wait_matrice_charge")
            .depends_on(prev, delay_seconds=1)
        )
        prev = (
            matrice.fly_survey(action.start, action.end)
            .oracle()
            .with_id(f"{prefix}_target_thermal_survey")
            .depends_on(prev, delay_seconds=2)
        )
    prev = (
        robot.check_status()
        .oracle()
        .with_id(f"{prefix}_robot_status")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        robot.charge()
        .oracle()
        .with_id(f"{prefix}_charge_robot_before_ground")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        system.advance_time(hours=1)
        .oracle()
        .with_id(f"{prefix}_wait_robot_charge")
        .depends_on(prev, delay_seconds=1)
    )
    if action.kind in {"insecticide"}:
        prev = (
            robot.inspect_pests(action.start, action.end)
            .oracle()
            .with_id(f"{prefix}_routine_ground_pest_confirmation")
            .depends_on(prev, delay_seconds=2)
        )
    elif action.kind == "replant":
        prev = (
            robot.inspect_emergence(action.start, action.end)
            .oracle()
            .with_id(f"{prefix}_routine_ground_emergence_confirmation")
            .depends_on(prev, delay_seconds=2)
        )
    elif action.reason == "wet_soil_trafficability_delay":
        for block_index, start in enumerate(range(action.start, action.end + 1, 8)):
            if block_index > 0 and block_index % 2 == 0:
                prev = (
                    robot.charge()
                    .oracle()
                    .with_id(f"{prefix}_recharge_robot_before_full_field_{start}")
                    .depends_on(prev, delay_seconds=1)
                )
                prev = (
                    system.advance_time(hours=1)
                    .oracle()
                    .with_id(f"{prefix}_wait_robot_recharge_{start}")
                    .depends_on(prev, delay_seconds=1)
                )
            end = min(start + 7, action.end)
            prev = (
                robot.inspect_crop_health(start, end)
                .oracle()
                .with_id(f"{prefix}_full_field_ground_health_{start}_{end}")
                .depends_on(prev, delay_seconds=2)
            )
    else:
        prev = (
            robot.inspect_crop_health(action.start, action.end)
            .oracle()
            .with_id(f"{prefix}_routine_ground_health_confirmation")
            .depends_on(prev, delay_seconds=2)
        )
    return prev


def _load_and_spray(
    tractor: TractorApp,
    prev: Any,
    action: ScenarioAction,
    prefix: str,
    *,
    chemical: str,
) -> Any:
    if chemical == "fungicide":
        prev = (
            tractor.load_fungicide(
                (action.end - action.start + 1) * action.liters_per_ridge * 1.02
            )
            .oracle()
            .with_id(f"{prefix}_load_fungicide")
            .depends_on(prev, delay_seconds=1)
        )
        return spray_blocks(
            tractor,
            prev,
            start_ridge=action.start,
            end_ridge=action.end,
            liters_per_ridge=action.liters_per_ridge,
            fungicide=True,
            id_prefix=prefix,
        )
    prev = (
        tractor.load_pesticide(
            (action.end - action.start + 1) * action.liters_per_ridge * 1.02
        )
        .oracle()
        .with_id(f"{prefix}_load_chemical")
        .depends_on(prev, delay_seconds=1)
    )
    for start in range(action.start, action.end + 1, 10):
        end = min(start + 9, action.end)
        if chemical == "herbicide":
            event = tractor.apply_herbicide(start, end, action.liters_per_ridge)
        else:
            event = tractor.spray_pesticide(start, end, action.liters_per_ridge)
        prev = (
            event.oracle()
            .with_id(f"{prefix}_{chemical}_spray_{start}_{end}")
            .depends_on(prev, delay_seconds=2)
        )
    return prev


def _manual_pesticide_spray(
    field_ops: FieldOpsApp,
    prev: Any,
    action: ScenarioAction,
    prefix: str,
) -> Any:
    for ridge_id in range(action.start, action.end + 1, 2):
        ridge_count = min(2, action.end - ridge_id + 1)
        event = (
            field_ops.apply_pesticide_manual(
                ridge_id=ridge_id,
                liters_per_ridge=action.liters_per_ridge,
                advance_time=False,
                ridge_count=ridge_count,
            )
            .oracle()
            .with_id(f"{prefix}_manual_spray_{ridge_id}_{ridge_id + ridge_count - 1}")
            .depends_on(prev, delay_seconds=2)
        )
        prev = event
    return prev


def _mechanical_blocks(
    tractor: TractorApp, prev: Any, action: ScenarioAction, prefix: str
) -> Any:
    prev = (
        tractor.attach_implement("cultivator")
        .oracle()
        .with_id(f"{prefix}_attach_cultivator")
        .depends_on(prev, delay_seconds=1)
    )
    for start in range(action.start, action.end + 1, 10):
        end = min(start + 9, action.end)
        prev = (
            tractor.mechanical_weed_control(start, end)
            .oracle()
            .with_id(f"{prefix}_mechanical_weed_{start}_{end}")
            .depends_on(prev, delay_seconds=2)
        )
    return (
        tractor.detach_implement()
        .oracle()
        .with_id(f"{prefix}_detach_cultivator")
        .depends_on(prev, delay_seconds=1)
    )


def _replant_blocks(
    tractor: TractorApp,
    prev: Any,
    action: ScenarioAction,
    prefix: str,
    seed_type: str,
) -> Any:
    for start in range(action.start, action.end + 1, 4):
        end = min(start + 3, action.end)
        prev = (
            tractor.load_seeds(seed_type, 100000)
            .oracle()
            .with_id(f"{prefix}_load_replant_seed_{start}_{end}")
            .depends_on(prev, delay_seconds=1)
        )
        prev = (
            tractor.replant_seeds(start, end, 4.0, HEINONG84_SPACING_CM)
            .oracle()
            .with_id(f"{prefix}_replant_{start}_{end}")
            .depends_on(prev, delay_seconds=2)
        )
    return prev


def _harvest_checks(
    weather: WeatherApp,
    sensor: SensorApp,
    farm_world: FarmWorldApp,
    prev: Any,
    label: str,
    start: int,
    end: int,
) -> Any:
    prev = (
        weather.get_current_weather()
        .oracle()
        .with_id(f"o_{label}_harvest_weather")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        weather.get_forecast(days=3)
        .oracle()
        .with_id(f"o_{label}_harvest_forecast")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        sensor.read_soil_sensors()
        .oracle()
        .with_id(f"o_{label}_harvest_soil")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        farm_world.get_farm_overview()
        .oracle()
        .with_id(f"o_{label}_harvest_overview")
        .depends_on(prev, delay_seconds=1)
    )
    return (
        farm_world.get_ridge_range_state(start, end)
        .oracle()
        .with_id(f"o_{label}_harvest_range_state")
        .depends_on(prev, delay_seconds=1)
    )


def _after_named_step(scenario: Scenario, prev: Any, label: str) -> Any:
    hook = getattr(scenario, "_after_named_step", None)
    if callable(hook):
        return hook(prev, label)
    return prev


class HarbinL3BatchScenario(Scenario):
    start_time: float | None = harbin_start_time()
    duration: float | None = 180 * 24 * 3600
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    nb_turns: int = 1
