from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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
from are.simulation.scenarios.scenario import Scenario


RIDGE_WIDTH_M = 1.1
HEINONG84_SPACING_CM = 7.9


def harbin_start_time(year: int = 2026, month: int = 5, day: int = 5) -> float:
    return datetime(year, month, day, 7, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600


def install_common_farm_apps(
    scenario: Scenario,
    *,
    thermal: bool = True,
    field_ops: bool = False,
) -> None:
    aui = AgentUserInterface()
    farm_world = FarmWorldApp()
    weather = WeatherApp()
    sensor = SensorApp(farm_world_app=farm_world)
    mavic = DroneApp(
        farm_world_app=farm_world,
        weather_app=weather,
        name="Mavic3M",
        description="DJI Mavic 3 Multispectral - multispectral NDVI mapping drone",
        speed_ms=5.0,
        effective_ridges_per_pass=7,
        battery_pct_per_ridge=1.0,
    )
    apps: list[Any] = [aui, farm_world, weather, sensor, mavic]
    if thermal:
        apps.append(
            DroneApp(
                farm_world_app=farm_world,
                weather_app=weather,
                name="Matrice4T",
                description="DJI Matrice 4T - thermal imaging drone",
                speed_ms=4.0,
                effective_ridges_per_pass=5,
                battery_pct_per_ridge=1.5,
            )
        )
    robot = RobotApp(
        farm_world_app=farm_world,
        weather_app=weather,
        name="Robot0",
        description="Ground inspection robot",
    )
    tractor = TractorApp(farm_world_app=farm_world, weather_app=weather)
    system = SystemApp()
    apps.extend([robot, tractor])
    if field_ops:
        apps.append(FieldOpsApp(farm_world_app=farm_world, weather_app=weather))
    apps.append(system)
    scenario.apps = apps
    farm_world.attach_system_app(system)


def configure_common_field(
    scenario: Scenario,
    *,
    profile_name: str,
    cultivar: str,
    seed_stocks: dict[str, int],
    density_target_plants_m2: float = 23.0,
    start_date: str = "2026-05-05",
    pesticide_liters: float = 1200.0,
    fertilizer_kg: float = 2500.0,
    fuel_liters: float = 1200.0,
    tractor_fuel_l: float = 180.0,
    initial_vwc: float = 0.30,
) -> None:
    farm_world = scenario.get_typed_app(FarmWorldApp)
    weather = scenario.get_typed_app(WeatherApp)
    tractor = scenario.get_typed_app(TractorApp)
    mavic = scenario.get_typed_app(DroneApp, "Mavic3M")

    first_seed = next(iter(seed_stocks))
    farm_world.configure_physics_profile(
        profile_name=profile_name,
        seed_type=first_seed,
        cultivar=cultivar,
        density_target_plants_m2=density_target_plants_m2,
        location="Harbin/Heilongjiang",
        start_date=start_date,
    )
    weather.set_weather(
        date=start_date,
        temp_c=16.0,
        humidity_pct=58.0,
        wind_speed_ms=2.4,
        rainfall_mm=0.0,
        solar_radiation=20.0,
        forecast=[
            {"date": "2026-05-06", "temp_c": 16.5, "humidity_pct": 56.0, "wind_speed_ms": 2.0, "rainfall_mm": 0.0, "solar_radiation": 20.5},
            {"date": "2026-05-07", "temp_c": 17.0, "humidity_pct": 55.0, "wind_speed_ms": 2.4, "rainfall_mm": 0.0, "solar_radiation": 21.0},
            {"date": "2026-05-08", "temp_c": 18.0, "humidity_pct": 54.0, "wind_speed_ms": 2.2, "rainfall_mm": 0.0, "solar_radiation": 21.0},
        ],
        avg_soil_vwc=initial_vwc,
    )

    farm_world.set_season_phase("full_season")
    for seed_type, count in seed_stocks.items():
        farm_world._inventory.seed_stock[seed_type] = int(count)
    farm_world._inventory.fertilizer_kg = fertilizer_kg
    farm_world._inventory.pesticide_liters = pesticide_liters
    farm_world._inventory.fuel_liters = fuel_liters

    tractor._fuel_tank_l = tractor_fuel_l
    tractor._seed_hopper = 0
    tractor._fertilizer_spreader_kg = 0.0
    tractor._pesticide_tank_l = 0.0
    tractor._fungicide_tank_l = 0.0
    mavic._battery_pct = 95.0
    try:
        scenario.get_typed_app(DroneApp, "Matrice4T")._battery_pct = 92.0
    except Exception:
        pass

    for i in range(64):
        ridge = farm_world.get_ridge(i)
        ridge.planted = False
        ridge.seed_type = None
        ridge.days_since_planted = 0
        ridge.growth_stage = "NOT_PLANTED"
        ridge.soil_vwc = initial_vwc + ((i % 5) - 2) * 0.003
        ridge.soil_temp_c = 13.1 + (i % 4) * 0.25
        ridge.ndvi = 0.18
        ridge.yield_potential = 0.0
        ridge.pest_pressure_base = 0.02
        ridge.pest_pressure = 0.02
        ridge.disease_pressure_base = 0.02
        ridge.disease_pressure = 0.02
        ridge.nutrient_index = 0.76
        ridge.stand_fraction = 1.0


def advance_days(scenario: Scenario, prev: Any, days: int, prefix: str) -> Any:
    system = scenario.get_typed_app(SystemApp)
    after_daily_advance = getattr(scenario, "_after_daily_advance", None)
    for day_index in range(1, days + 1):
        prev = (
            system.advance_time(days=1)
            .oracle()
            .with_id(f"{prefix}_advance_day_{day_index:03d}")
            .depends_on(prev, delay_seconds=1)
        )
        if callable(after_daily_advance):
            prev = after_daily_advance(prev, f"{prefix}_day_{day_index:03d}")
    return prev


def plant_range(
    tractor: TractorApp,
    prev: Any,
    *,
    start_ridge: int,
    end_ridge: int,
    seed_type: str,
    spacing_cm: float = HEINONG84_SPACING_CM,
    id_prefix: str,
) -> Any:
    for start in range(start_ridge, end_ridge + 1, 4):
        end = min(start + 3, end_ridge)
        prev = (
            tractor.load_seeds(seed_type, 300000)
            .oracle()
            .with_id(f"{id_prefix}_load_seed_before_{start}_{end}")
            .depends_on(prev, delay_seconds=1)
        )
        prev = (
            tractor.plant_seeds(start, end, 4.0, spacing_cm)
            .oracle()
            .with_id(f"{id_prefix}_plant_{start}_{end}")
            .depends_on(prev, delay_seconds=2)
        )
    return prev


def spray_blocks(
    tractor: TractorApp,
    prev: Any,
    *,
    start_ridge: int,
    end_ridge: int,
    liters_per_ridge: float,
    fungicide: bool,
    id_prefix: str,
) -> Any:
    for start in range(start_ridge, end_ridge + 1, 10):
        end = min(start + 9, end_ridge)
        if fungicide:
            event = tractor.apply_fungicide(start, end, liters_per_ridge)
        else:
            event = tractor.spray_pesticide(start, end, liters_per_ridge=liters_per_ridge)
        prev = (
            event.oracle()
            .with_id(f"{id_prefix}_spray_{start}_{end}")
            .depends_on(prev, delay_seconds=2)
        )
    return prev


def harvest_range(
    tractor: TractorApp,
    farm_world: FarmWorldApp,
    prev: Any,
    *,
    start_ridge: int,
    end_ridge: int,
    id_prefix: str,
) -> Any:
    for start in range(start_ridge, end_ridge + 1, 4):
        end = min(start + 3, end_ridge)
        prev = (
            tractor.harvest(start, end)
            .oracle()
            .with_id(f"{id_prefix}_harvest_{start}_{end}")
            .depends_on(prev, delay_seconds=2)
        )
        prev = (
            tractor.unload_grain()
            .oracle()
            .with_id(f"{id_prefix}_unload_after_{end}")
            .depends_on(prev, delay_seconds=1)
        )
    prev = (
        farm_world.dry_grain(target_moisture_pct=13.0)
        .oracle()
        .with_id(f"{id_prefix}_dry_grain")
        .depends_on(prev, delay_seconds=2)
    )
    return (
        farm_world.store_grain()
        .oracle()
        .with_id(f"{id_prefix}_store_grain")
        .depends_on(prev, delay_seconds=2)
    )


def collect_event_graph(root: Any) -> list[Any]:
    ordered: list[Any] = []
    seen: set[int] = set()
    stack = [root]
    while stack:
        event = stack.pop(0)
        event_key = id(event)
        if event_key in seen:
            continue
        seen.add(event_key)
        ordered.append(event)
        stack[0:0] = list(getattr(event, "successors", []))
    return ordered
