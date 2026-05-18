from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    FieldOpsApp,
    FarmWorldApp,
    GrowthStage,
    SeasonPhase,
    SensorApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.apps.farm_world.farm_world_app import DEFAULT_RIDGE_WIDTH_M, FIELD_LENGTH_M
from are.simulation.apps.farm_world.physics_orchestrator import _generate_weather_day
from are.simulation.apps.system import SystemApp
from are.simulation.physics import WeatherGenerator
from are.simulation.physics.weather_engine import default_harbin_soybean_config
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.time_manager import TimeManager
from are.simulation.types import EventRegisterer


CALIBRATION_PROFILE_NONE = "none"
DEFAULT_TANGYAN5_DATA_SOURCE = "embedded:scenario_tangyan5_stress_free_oracle_full_season.py"
MANAGEMENT_PATH_STRESS_FREE_ORACLE = "stress_free_oracle"
WEATHER_SOURCE_ENGINE = "engine"


@dataclass(frozen=True)
class Tangyan5EventPathPlot:
    plot_id: str
    variety: str
    seed_type: str
    planting_date: date
    topdress_date: date
    harvest_date: date
    plot_area_m2: float
    plot_yield_kg: float
    seed_density_plants_ha: float
    seed_depth_cm: float
    seed_spacing_cm: float
    topdress_n_kg_ha: float
    topdress_p_kg_ha: float
    topdress_k_kg_ha: float
    nutrient_index: float

    @property
    def simulated_field_area_m2(self) -> float:
        return FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M * 64

    @property
    def actual_yield_kg_ha(self) -> float:
        return self.plot_yield_kg / self.plot_area_m2 * 10000.0

    @property
    def actual_scaled_field_yield_kg(self) -> float:
        return self.actual_yield_kg_ha * self.simulated_field_area_m2 / 10000.0


TANGYAN5_PLOT = Tangyan5EventPathPlot(
    plot_id="Nor_HH43",
    variety="黑河43",
    seed_type="HEIHE43",
    planting_date=date(2025, 5, 19),
    topdress_date=date(2025, 6, 27),
    harvest_date=date(2025, 9, 15),
    plot_area_m2=35.1,
    plot_yield_kg=14.12,
    seed_density_plants_ha=224360.0,
    seed_depth_cm=4.0,
    seed_spacing_cm=8.1,
    topdress_n_kg_ha=3.45,
    topdress_p_kg_ha=3.01125,
    topdress_k_kg_ha=2.86395,
    nutrient_index=0.75,
)

TANGYAN5_WEATHER_SOURCE = WEATHER_SOURCE_ENGINE
TANGYAN5_WEATHER_SEED = 5
FERTIGATION_CARRIER_WATER_MM = 0.1
IRRIGATION_MM_PER_HOUR = 5.0


def _local_7am_timestamp(day: date) -> float:
    from datetime import datetime, timezone

    return (
        datetime(day.year, day.month, day.day, 7, 0, 0, tzinfo=timezone.utc).timestamp()
        - 8 * 3600
    )


def _weather_app_day_dict(weather_day: Any) -> dict[str, Any]:
    return {
        "date": weather_day.day.isoformat(),
        "temp_c": float(weather_day.air_temp_mean_c),
        "humidity_pct": 55.0,
        "wind_speed_ms": float(weather_day.wind_ms),
        "rainfall_mm": float(weather_day.rain_mm),
        "solar_radiation": float(weather_day.solar_rad_mj_m2) / 0.0864,
    }


TANGYAN5_STRESS_FREE_INTERVENTIONS: tuple[tuple[date, float, float], ...] = (
    (date(2025, 5, 20), 0.0995, 0.1),
    (date(2025, 5, 21), 0.03, 0.1),
    (date(2025, 5, 24), 0.03, 0.1),
    (date(2025, 5, 27), 0.03, 0.1),
    (date(2025, 5, 30), 0.03, 0.1),
    (date(2025, 6, 2), 0.03, 0.1),
    (date(2025, 6, 6), 0.03, 0.1),
    (date(2025, 6, 9), 0.03, 0.1),
    (date(2025, 6, 12), 0.03, 0.1),
    (date(2025, 6, 15), 0.03, 0.1),
    (date(2025, 6, 18), 0.03, 0.1),
    (date(2025, 6, 22), 0.03, 0.1),
    (date(2025, 6, 25), 0.03, 0.1),
    (date(2025, 6, 28), 0.03, 2.0),
    (date(2025, 6, 29), 0.01, 2.0),
    (date(2025, 6, 30), 0.01, 2.0),
    (date(2025, 7, 1), 0.01, 2.0),
    (date(2025, 7, 2), 0.01, 2.12),
    (date(2025, 7, 3), 0.01, 2.0),
    (date(2025, 7, 4), 0.01, 2.0),
    (date(2025, 7, 5), 0.01, 2.0),
    (date(2025, 7, 6), 0.01, 2.08),
    (date(2025, 7, 7), 0.01, 2.54),
    (date(2025, 7, 8), 0.01, 2.0),
    (date(2025, 7, 12), 0.03, 0.1),
    (date(2025, 7, 15), 0.03, 0.1),
    (date(2025, 7, 18), 0.03, 0.1),
    (date(2025, 7, 21), 0.03, 0.1),
    (date(2025, 7, 25), 0.03, 0.1),
    (date(2025, 7, 28), 0.03, 5.0),
    (date(2025, 7, 29), 0.01, 5.0),
    (date(2025, 7, 30), 0.01, 4.0),
    (date(2025, 7, 31), 0.01, 4.0),
    (date(2025, 8, 1), 0.01, 4.0),
    (date(2025, 8, 2), 0.01, 4.0),
    (date(2025, 8, 3), 0.01, 4.0),
    (date(2025, 8, 4), 0.01, 0.1),
    (date(2025, 8, 7), 0.03, 0.1),
    (date(2025, 8, 11), 0.03, 0.1),
    (date(2025, 8, 13), 0.01, 2.0),
    (date(2025, 8, 14), 0.01, 2.0),
    (date(2025, 8, 16), 0.03, 0.1),
)


@register_scenario("scenario_tangyan5_stress_free_oracle_full_season")
class ScenarioTangyan5StressFreeOracleFullSeason(Scenario):
    """Tangyan5 stress-free oracle as a standalone ARE full-season scenario."""

    weather_source: str = TANGYAN5_WEATHER_SOURCE
    weather_seed: int = TANGYAN5_WEATHER_SEED
    calibration_profile: str = CALIBRATION_PROFILE_NONE
    management_path: str = MANAGEMENT_PATH_STRESS_FREE_ORACLE
    initial_ridge_vwc: float = 0.24
    duration: float | None = 180 * 24 * 3600
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    nb_turns: int | None = 1

    detailed_briefing: bool = True

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        self.plot = TANGYAN5_PLOT
        self.start_time = _local_7am_timestamp(self.plot.planting_date)

        aui = AgentUserInterface()
        farm_world = FarmWorldApp()
        weather = WeatherApp()
        sensor = SensorApp(farm_world_app=farm_world)
        tractor = TractorApp(farm_world_app=farm_world, weather_app=weather)
        field_ops = FieldOpsApp(farm_world_app=farm_world, weather_app=weather)
        system = SystemApp()

        self.apps = [aui, farm_world, weather, sensor, tractor, field_ops, system]
        self._install_shared_time_manager()
        self._configure_initial_state()
        farm_world.attach_system_app(system)

    def _install_shared_time_manager(self) -> None:
        tm = TimeManager()
        tm.reset(float(self.start_time))
        for app in self.apps or []:
            app.register_time_manager(tm)

    def _configure_initial_state(self) -> None:
        plot = self.plot
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        tractor = self.get_typed_app(TractorApp)

        farm_world.configure_physics_profile(
            profile_name="tangyan5_actual_2025_base",
            location="糖研院5号地",
            scenario_type="stress_free_oracle_full_season",
            random_seed=TANGYAN5_WEATHER_SEED,
            plot_id=plot.plot_id,
            variety=plot.variety,
            source_data=DEFAULT_TANGYAN5_DATA_SOURCE,
            calibration_profile=self.calibration_profile,
        )
        self._install_weather_generator()
        self._set_weather_for_date(plot.planting_date)
        farm_world.set_season_phase(SeasonPhase.PLANTING.value)

        seeds_needed = int(
            plot.seed_density_plants_ha * plot.simulated_field_area_m2 / 10000.0
        )
        farm_world._inventory.seed_stock[plot.seed_type] = max(seeds_needed * 2, 500000)
        farm_world._inventory.fertilizer_kg = 2000.0
        farm_world._inventory.pesticide_liters = 2000.0
        farm_world._inventory.fuel_liters = 1500.0
        tractor._fuel_tank_l = 100.0

        planting_weather = self._weather_day_for_date(plot.planting_date)
        soil_temp = float(planting_weather.air_temp_mean_c) if planting_weather is not None else 13.0
        for ridge_id in range(farm_world.num_ridges):
            ridge = farm_world.get_ridge(ridge_id)
            ridge.planted = False
            ridge.seed_type = None
            ridge.days_since_planted = 0
            ridge.growth_stage = GrowthStage.BARE.value
            ridge.soil_vwc = self.initial_ridge_vwc
            ridge.soil_temp_c = soil_temp
            ridge.nutrient_index = plot.nutrient_index
            ridge.yield_potential = 1.0
            ridge.pest_pressure_base = 0.03
            ridge.pest_pressure = 0.03
            ridge.disease_pressure_base = 0.03
            ridge.disease_pressure = 0.03

        weather._avg_soil_vwc = farm_world.get_avg_vwc()

    def _install_weather_generator(self) -> None:
        physics = self.get_typed_app(FarmWorldApp).physics
        physics.weather_generator = WeatherGenerator(
            config=default_harbin_soybean_config(),
            seed=TANGYAN5_WEATHER_SEED,
        )

    def _set_weather_for_date(self, day: date) -> None:
        weather = self.get_typed_app(WeatherApp)
        current = self._weather_day_for_date(day)
        if current is None:
            return
        forecast = []
        for offset in range(1, 8):
            item = self._weather_day_for_date(day + timedelta(days=offset))
            if item is not None:
                forecast.append(_weather_app_day_dict(item))
        weather.set_weather(
            date=day.isoformat(),
            temp_c=float(current.air_temp_mean_c),
            humidity_pct=55.0,
            wind_speed_ms=float(current.wind_ms),
            rainfall_mm=float(current.rain_mm),
            solar_radiation=float(current.solar_rad_mj_m2) / 0.0864,
            forecast=forecast,
            avg_soil_vwc=self.get_typed_app(FarmWorldApp).get_avg_vwc(),
        )

    def _weather_day_for_date(self, day: date) -> Any | None:
        physics = self.get_typed_app(FarmWorldApp).physics
        generator = physics.weather_generator
        if generator is None:
            return None

        generated = _generate_weather_day(generator, day)
        if generated is None:
            return None
        return generated

    def _wait_daily(self, prev: Any, current: date, target: date, prefix: str) -> tuple[Any, date]:
        system = self.get_typed_app(SystemApp)
        while current < target:
            next_date = current + timedelta(days=1)
            prev = (
                system.advance_time(days=1)
                .oracle()
                .with_id(f"{prefix}_advance_to_{next_date.isoformat()}")
                .depends_on(prev, delay_seconds=1)
            )
            current = next_date
        return prev, current

    def build_events_flow(self) -> None:
        plot = self.plot
        aui = self.get_typed_app(AgentUserInterface)
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        tractor = self.get_typed_app(TractorApp)
        field_ops = self.get_typed_app(FieldOpsApp)

        with EventRegisterer.capture_mode():
            prev = (
                aui.send_message_to_agent(
                    content=(
                        f"按糖研院5号地 {plot.plot_id}/{plot.variety} 建立 stress-free oracle 路径："
                        "同样整块地播种和基肥，之后按每日 engine 状态做肥水干预，"
                        "目标是 R8 前 canopy stress days 为 0。"
                    )
                )
                .with_id("tangyan5_oracle_briefing")
                .depends_on(None, delay_seconds=5)
            )
            root = prev

            prev = (
                weather.get_current_weather()
                .oracle()
                .with_id("tangyan5_oracle_weather_planting")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("tangyan5_oracle_soil_planting")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.get_status()
                .oracle()
                .with_id("tangyan5_oracle_tractor_before_prep")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                farm_world.get_inventory()
                .oracle()
                .with_id("tangyan5_oracle_inventory_before_prep")
                .depends_on(prev, delay_seconds=1)
            )

            prev = (
                tractor.attach_implement("grader")
                .oracle()
                .with_id("tangyan5_oracle_attach_grader")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.level()
                .oracle()
                .with_id("tangyan5_oracle_level")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.detach_implement()
                .oracle()
                .with_id("tangyan5_oracle_detach_grader")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.load_fertilizer(500.0)
                .oracle()
                .with_id("tangyan5_oracle_load_base_fertilizer")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.base_fertilize()
                .oracle()
                .with_id("tangyan5_oracle_base_fertilize")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.form_ridges(DEFAULT_RIDGE_WIDTH_M)
                .oracle()
                .with_id("tangyan5_oracle_form_ridges")
                .depends_on(prev, delay_seconds=1)
            )
            for start in range(0, 64, 4):
                seeds_needed = int((4 * FIELD_LENGTH_M * 2 * 100.0) / plot.seed_spacing_cm)
                if start in {0, 44}:
                    prev = (
                        tractor.load_seeds(plot.seed_type, max(300000, seeds_needed * 4))
                        .oracle()
                        .with_id(f"tangyan5_oracle_load_seed_before_{start}")
                        .depends_on(prev, delay_seconds=1)
                    )
                prev = (
                    tractor.plant_seeds(
                        start,
                        start + 3,
                        plot.seed_depth_cm,
                        plot.seed_spacing_cm,
                    )
                    .oracle()
                    .with_id(f"tangyan5_oracle_plant_{start}_{start + 3}")
                    .depends_on(prev, delay_seconds=1)
                )
            prev = (
                farm_world.commit_daily_physics()
                .oracle()
                .with_id("tangyan5_oracle_commit_planting")
                .depends_on(prev, delay_seconds=1)
            )

            current = date(2025, 5, 20)
            for intervention_date, nutrient_amount, irrigation_mm in TANGYAN5_STRESS_FREE_INTERVENTIONS:
                prev, current = self._wait_daily(
                    prev,
                    current=current,
                    target=intervention_date,
                    prefix=f"tangyan5_oracle_wait_to_{intervention_date.isoformat()}",
                )
                prev = (
                    farm_world.apply_fertigation(
                        0,
                        63,
                        nutrient_amount,
                        FERTIGATION_CARRIER_WATER_MM,
                    )
                    .oracle()
                    .with_id(f"tangyan5_oracle_fertigation_{intervention_date.isoformat()}")
                    .depends_on(prev, delay_seconds=1)
                )
                # if irrigation_mm > FERTIGATION_CARRIER_WATER_MM:
                #     irrigation_hours = (
                #         irrigation_mm - FERTIGATION_CARRIER_WATER_MM
                #     ) / IRRIGATION_MM_PER_HOUR
                #     prev = (
                #         field_ops.irrigate(0, 63, round(irrigation_hours, 3))
                #         .oracle()
                #         .with_id(f"tangyan5_oracle_irrigation_{intervention_date.isoformat()}")
                #         .depends_on(prev, delay_seconds=1)
                #     )

            prev, current = self._wait_daily(
                prev,
                current=current,
                target=plot.harvest_date,
                prefix="tangyan5_oracle_wait_to_harvest",
            )
            prev = (
                weather.get_current_weather()
                .oracle()
                .with_id("tangyan5_oracle_weather_harvest")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                farm_world.get_farm_overview()
                .oracle()
                .with_id("tangyan5_oracle_overview_harvest")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.refuel(100.0)
                .oracle()
                .with_id("tangyan5_oracle_refuel_harvest")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.attach_implement("harvester")
                .oracle()
                .with_id("tangyan5_oracle_attach_harvester")
                .depends_on(prev, delay_seconds=1)
            )
            for start in range(0, 64, 4):
                prev = (
                    tractor.harvest(start, start + 3)
                    .oracle()
                    .with_id(f"tangyan5_oracle_harvest_{start}_{start + 3}")
                    .depends_on(prev, delay_seconds=1)
                )
                if start % 8 == 4:
                    prev = (
                        tractor.unload_grain()
                        .oracle()
                        .with_id(f"tangyan5_oracle_unload_after_{start + 3}")
                        .depends_on(prev, delay_seconds=1)
                    )
            prev = (
                farm_world.dry_grain(13.5)
                .oracle()
                .with_id("tangyan5_oracle_dry_grain")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                farm_world.store_grain()
                .oracle()
                .with_id("tangyan5_oracle_store_grain")
                .depends_on(prev, delay_seconds=1)
            )
            (
                aui.send_message_to_user(
                    content="Tangyan5 stress-free oracle event path complete."
                )
                .oracle()
                .with_id("tangyan5_oracle_report")
                .depends_on(prev, delay_seconds=1)
            )
            events = []
            seen = set()

            def collect_event_chain(event):
                marker = id(event)
                if marker in seen:
                    return
                seen.add(marker)
                events.append(event)
                for successor in event.successors:
                    collect_event_chain(successor)

            collect_event_chain(root)
            self.events = events
