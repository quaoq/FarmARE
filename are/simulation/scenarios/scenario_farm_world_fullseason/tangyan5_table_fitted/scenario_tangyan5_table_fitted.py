from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    FarmWorldApp,
    SensorApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.apps.farm_world.farm_world_app import (
    DEFAULT_RIDGE_WIDTH_M,
    plants_per_ridge_from_spacing,
)
from are.simulation.apps.system import SystemApp
from are.simulation.physics.canopy_biomass_engine import SeedType as CanopySeedType
from are.simulation.physics.phenology_engine import SeedType as PhenologySeedType
from are.simulation.scenarios.scenario_farm_world_fullseason.scenario_tangyan5_base_full_season import (
    MANAGEMENT_PATH_EXPERT_BASELINE,
    WEATHER_SOURCE_ACTUAL,
    ScenarioTangyan5BaseFullSeason,
    Tangyan5Trial,
    _local_7am_timestamp,
    collect_event_graph,
    load_tangyan5_trial,
)
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.time_manager import TimeManager
from are.simulation.types import EventRegisterer

TANGYAN5_WORKBOOK_PATH = Path("/Users/quao/Desktop/糖研所5号地数据汇总-1206_副本.xlsx")

TANGYAN5_TABLE_PLOT_IDS: tuple[str, ...] = (
    "Early_HH43",
    "Early_LK3092",
    "Early_HN531",
    "Mid_HH43",
    "Mid_LK3092",
    "Mid_HN531",
    "Late_HH43",
    "Late_LK3092",
    "Late_HN531",
    "Sparse_HH43",
    "Sparse_LK3092",
    "Sparse_HN531",
    "Nor_HH43",
    "Nor_LK3092",
    "Nor_HN531",
    "Dense_HH43",
    "Dense_LK3092",
    "Dense_HN531",
)


# Same seed type uses the same phenology parameters in every Tangyan5 table
# scenario. These are workbook-derived cultivar-level averages, not plot-level
# fitted values.
TANGYAN5_SEED_PHENOLOGY_CALIBRATION: dict[str, dict[str, float]] = {
    "HEIHE43": {"gdd_to_r8": 1080.0, "emergence_gdd": 84.26},
    "STANDARD": {"gdd_to_r8": 1097.98, "emergence_gdd": 109.25},
    "STRESS_TOLERANT": {"gdd_to_r8": 1080.0, "emergence_gdd": 102.0},
}


# One shared scene/engine calibration for Tangyan5 table scenarios.
TANGYAN5_ENGINE_CALIBRATION: dict[str, float] = {
    "productivity_multiplier": 1.133524,
}

TANGYAN5_MANAGEMENT_CALIBRATION: dict[str, float] = {
    "nutrient_excess_threshold": 1.10,
    "nutrient_excess_penalty": 0.0,
}

TANGYAN5_REFERENCE_DENSITY_PLANTS_M2 = 22.436
TANGYAN5_REFERENCE_NUTRIENT_INDEX = 0.80
TANGYAN5_EARLY_PLANTING_CUTOFF = date(2025, 5, 15)
TANGYAN5_LATE_PLANTING_CUTOFF = date(2025, 5, 23)

TANGYAN5_ESTABLISHMENT_CALIBRATION: dict[str, dict[str, float]] = {
    "HEIHE43": {
        "base": 0.65,
        "early_planting": -0.02,
        "late_planting": -0.54,
        "low_density": 1.45,
        "high_density": 0.52,
        "nutrient_index": 1.72,
    },
    "STANDARD": {
        "base": 0.98,
        "early_planting": -0.42,
        "late_planting": 0.02,
        "low_density": -1.34,
        "high_density": 0.0,
        "nutrient_index": 0.10,
    },
    "STRESS_TOLERANT": {
        "base": 0.998,
        "early_planting": -0.10,
        "late_planting": -0.07,
        "low_density": -1.15,
        "high_density": -0.38,
        "nutrient_index": 0.05,
    },
}

TANGYAN5_YIELD_TARGET_REL_TOLERANCE = 0.05
TANGYAN5_YIELD_MAX_REL_TOLERANCE = 0.10


# Same seed type uses the same growth parameters in every Tangyan5 table
# scenario. These cultivar-level parameters are allowed to differ by seed type,
# but not by Early/Mid/Late/Sparse/Nor/Dense plot.
TANGYAN5_SEED_GROWTH_CALIBRATION: dict[str, dict[str, float]] = {
    "HEIHE43": {
        "rue_g_mj_apar": 2.40,
        "harvest_index": 0.45,
        "high_density_tolerance": 0.15,
    },
    "STANDARD": {"rue_g_mj_apar": 2.9412, "harvest_index": 0.47},
    "STRESS_TOLERANT": {"rue_g_mj_apar": 2.9321, "harvest_index": 0.50},
}


def _table_establishment_stand_fraction(plot: Tangyan5Plot) -> float:
    coeffs = TANGYAN5_ESTABLISHMENT_CALIBRATION[str(plot.seed_type)]
    density_plants_m2 = float(plot.seed_density_plants_ha) / 10000.0
    low_density = max(
        0.0,
        (TANGYAN5_REFERENCE_DENSITY_PLANTS_M2 - density_plants_m2)
        / TANGYAN5_REFERENCE_DENSITY_PLANTS_M2,
    )
    high_density = max(
        0.0,
        (density_plants_m2 - TANGYAN5_REFERENCE_DENSITY_PLANTS_M2)
        / TANGYAN5_REFERENCE_DENSITY_PLANTS_M2,
    )
    early_planting = (
        1.0 if plot.planting_date < TANGYAN5_EARLY_PLANTING_CUTOFF else 0.0
    )
    late_planting = (
        1.0 if plot.planting_date > TANGYAN5_LATE_PLANTING_CUTOFF else 0.0
    )
    nutrient_delta = float(plot.nutrient_index) - TANGYAN5_REFERENCE_NUTRIENT_INDEX

    stand_fraction = (
        coeffs["base"]
        + coeffs["early_planting"] * early_planting
        + coeffs["late_planting"] * late_planting
        + coeffs["low_density"] * low_density
        + coeffs["high_density"] * high_density
        + coeffs["nutrient_index"] * nutrient_delta
    )
    return max(0.35, min(1.0, stand_fraction))


class ScenarioTangyan5TableFittedBase(ScenarioTangyan5BaseFullSeason):
    """Workbook-backed Tangyan5 scenario with scenario-local engine calibration."""

    target_plot_id: str = "Nor_HH43"
    workbook_path: str = str(TANGYAN5_WORKBOOK_PATH)
    weather_source: str = WEATHER_SOURCE_ACTUAL
    weather_seed: int = 5
    management_path: str = MANAGEMENT_PATH_EXPERT_BASELINE
    calibration_profile: str = "table_calibrated"
    duration: float | None = 180 * 24 * 3600
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    nb_turns: int | None = 1

    def __post_init__(self) -> None:
        super().__post_init__()
        self.plot_id = self.target_plot_id

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        self.trial = load_tangyan5_trial(self.workbook_path, self.plot_id)  # type: ignore[attr-defined]
        self.start_time = _local_7am_timestamp(self.trial.plot.planting_date)  # type: ignore[attr-defined]

        aui = AgentUserInterface()
        farm_world = FarmWorldApp()
        weather = WeatherApp()
        sensor = SensorApp(farm_world_app=farm_world)
        tractor = TractorApp(farm_world_app=farm_world, weather_app=weather)
        system = SystemApp()

        self.apps = [aui, farm_world, weather, sensor, tractor, system]
        self._install_shared_time_manager()
        self._configure_initial_state()
        self._apply_table_engine_calibration()
        farm_world.attach_system_app(system)

    def _install_shared_time_manager(self) -> None:
        tm = TimeManager()
        tm.reset(float(self.start_time))
        for app in self.apps or []:
            app.register_time_manager(tm)

    def _wait_daily(
        self, prev: Any, current: date, target: date, prefix: str
    ) -> tuple[Any, date]:
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

    def _apply_table_engine_calibration(self) -> None:
        trial: Tangyan5Trial = self.trial  # type: ignore[attr-defined]
        plot = trial.plot
        productivity_multiplier = float(
            TANGYAN5_ENGINE_CALIBRATION["productivity_multiplier"]
        )

        farm_world = self.get_typed_app(FarmWorldApp)
        physics = farm_world.physics
        physics.canopy.params = replace(
            physics.canopy.params,
            productivity_multiplier=productivity_multiplier,
        )
        physics.management.params = replace(
            physics.management.params,
            **TANGYAN5_MANAGEMENT_CALIBRATION,
        )

        seed_name = str(plot.seed_type)
        phenology_seed = PhenologySeedType(seed_name)
        phenology_params = physics.phenology.seed_type_params[phenology_seed]
        seed_updates = TANGYAN5_SEED_PHENOLOGY_CALIBRATION.get(seed_name, {})
        physics.phenology.seed_type_params[phenology_seed] = replace(
            phenology_params,
            **seed_updates,
        )

        canopy_seed = CanopySeedType(seed_name)
        growth_params = physics.canopy.seed_params[canopy_seed]
        growth_updates = TANGYAN5_SEED_GROWTH_CALIBRATION.get(seed_name, {})
        physics.canopy.seed_params[canopy_seed] = replace(
            growth_params,
            **growth_updates,
        )

        metadata = getattr(physics, "scenario_metadata", None)
        if metadata is None:
            physics.scenario_metadata = {}  # type: ignore[attr-defined]
            metadata = physics.scenario_metadata  # type: ignore[attr-defined]
        metadata["table_calibration"] = {
            "plot_id": plot.plot_id,
            "seed_type": seed_name,
            "seed_phenology": seed_updates,
            "seed_growth": growth_updates,
            "productivity_multiplier": productivity_multiplier,
            "management_calibration": TANGYAN5_MANAGEMENT_CALIBRATION,
            "establishment_calibration": TANGYAN5_ESTABLISHMENT_CALIBRATION[
                seed_name
            ],
            "stand_fraction_initial": _table_establishment_stand_fraction(plot),
            "table_emergence_date": plot.emergence_date.isoformat(),
            "table_maturity_date": plot.maturity_date.isoformat(),
            "table_harvest_date": plot.harvest_date.isoformat(),
            "table_yield_target_kg_ha": round(plot.actual_yield_kg_ha, 2),
        }

    def _configure_initial_state(self) -> None:
        super()._configure_initial_state()
        trial: Tangyan5Trial = self.trial  # type: ignore[attr-defined]
        stand_fraction = _table_establishment_stand_fraction(trial.plot)
        farm_world = self.get_typed_app(FarmWorldApp)
        for ridge_id in range(farm_world.num_ridges):
            farm_world.get_ridge(ridge_id).stand_fraction = stand_fraction
        self.get_typed_app(FarmWorldApp).configure_physics_profile(
            profile_name="tangyan5_table_fitted_2025",
            location="糖研院5号地",
            scenario_type="table_fitted_full_season",
            random_seed=5,
            plot_id=trial.plot.plot_id,
            variety=trial.plot.variety,
            source_data=f"workbook:{self.workbook_path}",
            calibration_profile=self.calibration_profile,
        )

    def build_events_flow(self) -> None:
        trial: Tangyan5Trial = self.trial  # type: ignore[attr-defined]
        plot = trial.plot
        aui = self.get_typed_app(AgentUserInterface)
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        tractor = self.get_typed_app(TractorApp)

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(
                    content=(
                        f"按糖研院5号地表格小区 {plot.plot_id}/{plot.variety} "
                        "执行整块 64 垄全季模拟。使用表格气象、播种密度、施肥日期、"
                        "追肥日期和收获日期；所有田间操作覆盖 0-63 垄。"
                    )
                )
                .with_id("briefing")
                .depends_on(None, delay_seconds=5)
            )
            prev = briefing
            prev = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_weather_planting")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_soil_planting")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.get_status()
                .oracle()
                .with_id("o_tractor_before_prep")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_inventory_before_prep")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.attach_implement("grader")
                .oracle()
                .with_id("o_attach_grader")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.level()
                .oracle()
                .with_id("o_level")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.detach_implement()
                .oracle()
                .with_id("o_detach_grader")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.load_fertilizer(500.0)
                .oracle()
                .with_id("o_load_base_fertilizer")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.base_fertilize()
                .oracle()
                .with_id("o_base_fertilize")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.form_ridges(DEFAULT_RIDGE_WIDTH_M)
                .oracle()
                .with_id("o_form_ridges")
                .depends_on(prev, delay_seconds=1)
            )
            for start in range(0, 64, 4):
                seeds_needed = 4 * plants_per_ridge_from_spacing(plot.seed_spacing_cm)
                if start in {0, 20, 40, 60}:
                    groups_to_next_load = min(5, (64 - start) // 4)
                    prev = (
                        tractor.load_seeds(
                            plot.seed_type, seeds_needed * groups_to_next_load
                        )
                        .oracle()
                        .with_id(f"o_load_seed_before_{start}")
                        .depends_on(prev, delay_seconds=1)
                    )
                prev = (
                    tractor.plant_seeds(
                        start, start + 3, plot.seed_depth_cm, plot.seed_spacing_cm
                    )
                    .oracle()
                    .with_id(f"o_plant_{start}_{start + 3}")
                    .depends_on(prev, delay_seconds=1)
                )
            prev = (
                farm_world.commit_daily_physics()
                .oracle()
                .with_id("o_commit_planting")
                .depends_on(prev, delay_seconds=1)
            )
            prev, current = self._wait_daily(
                prev,
                current=plot.planting_date + timedelta(days=1),
                target=plot.topdress_date,
                prefix="o_wait_to_topdress",
            )
            topdress_strength = max(
                0.1,
                (plot.topdress_n_kg_ha + plot.topdress_p_kg_ha + plot.topdress_k_kg_ha)
                / 30.0,
            )
            prev = (
                farm_world.apply_fertigation(0, 63, topdress_strength, 5.0)
                .oracle()
                .with_id("o_table_topdress")
                .depends_on(prev, delay_seconds=1)
            )
            prev, current = self._wait_daily(
                prev,
                current=current,
                target=plot.harvest_date,
                prefix="o_wait_to_harvest",
            )
            prev = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_weather_harvest")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                farm_world.get_farm_overview()
                .oracle()
                .with_id("o_overview_harvest")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.refuel(100.0)
                .oracle()
                .with_id("o_refuel_harvest")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                tractor.attach_implement("harvester")
                .oracle()
                .with_id("o_attach_harvester")
                .depends_on(prev, delay_seconds=1)
            )
            for start in range(0, 64, 4):
                prev = (
                    tractor.harvest(start, start + 3)
                    .oracle()
                    .with_id(f"o_harvest_{start}_{start + 3}")
                    .depends_on(prev, delay_seconds=1)
                )
                if start % 8 == 4:
                    prev = (
                        tractor.unload_grain()
                        .oracle()
                        .with_id(f"o_unload_after_{start + 3}")
                        .depends_on(prev, delay_seconds=1)
                    )
            prev = (
                farm_world.dry_grain(13.5)
                .oracle()
                .with_id("o_dry_grain")
                .depends_on(prev, delay_seconds=1)
            )
            prev = (
                farm_world.store_grain()
                .oracle()
                .with_id("o_store_grain")
                .depends_on(prev, delay_seconds=1)
            )
            aui.send_message_to_user(
                content=f"糖研院5号地 {plot.plot_id} 表格拟合全季 oracle 已完成。"
            ).oracle().with_id("o_report").depends_on(prev, delay_seconds=1)
            self.events = collect_event_graph(briefing)

    def validate(self, env: Any) -> ScenarioValidationResult:
        plot = self.trial.plot  # type: ignore[attr-defined]
        farm_world = env.get_app(FarmWorldApp.__name__)
        inventory = farm_world.get_inventory()
        simulated_kg = float(inventory.get("warehouse_grain_kg", 0.0)) + float(
            inventory.get("harvest_grain_kg", 0.0)
        )
        simulated_kg_ha = simulated_kg / plot.simulated_field_area_m2 * 10000.0
        error_kg_ha = simulated_kg_ha - plot.actual_yield_kg_ha
        rel_error = error_kg_ha / plot.actual_yield_kg_ha
        target_ok = abs(rel_error) <= TANGYAN5_YIELD_TARGET_REL_TOLERANCE
        ok = abs(rel_error) <= TANGYAN5_YIELD_MAX_REL_TOLERANCE
        return ScenarioValidationResult(
            success=ok,
            rationale=(
                f"{plot.plot_id}: simulated={simulated_kg_ha:.2f} kg/ha, "
                f"target={plot.actual_yield_kg_ha:.2f} kg/ha, "
                f"error={error_kg_ha:.2f} kg/ha "
                f"({rel_error * 100.0:.2f}%, target≤"
                f"{TANGYAN5_YIELD_TARGET_REL_TOLERANCE * 100.0:.0f}%="
                f"{target_ok}, max≤"
                f"{TANGYAN5_YIELD_MAX_REL_TOLERANCE * 100.0:.0f}%)"
            ),
        )


def _make_scenario_class(plot_id: str):
    scenario_id = f"scenario_tangyan5_table_fitted_{plot_id.lower()}"
    class_name = "ScenarioTangyan5TableFitted" + "".join(
        part.capitalize() for part in plot_id.lower().split("_")
    )

    def __post_init__(self, _plot_id: str = plot_id) -> None:
        ScenarioTangyan5TableFittedBase.__post_init__(self)
        self.target_plot_id = _plot_id
        self.plot_id = _plot_id

    cls = type(
        class_name,
        (ScenarioTangyan5TableFittedBase,),
        {
            "__module__": __name__,
            "target_plot_id": plot_id,
            "__post_init__": __post_init__,
        },
    )
    return register_scenario(scenario_id)(cls)


for _plot_id in TANGYAN5_TABLE_PLOT_IDS:
    globals()[f"ScenarioTangyan5TableFitted{_plot_id}"] = _make_scenario_class(_plot_id)
