"""
Physics orchestrator — single integration site for the seven engines.

Daily-tick order (verbatim from
scenario_farm_world_physics/physics_action_tick_integration_guide.md
section "Time advancement should run world ticks"):

  1. apply weather for the day
  2. apply pending management water/nutrient/treatment inputs
  3. update soil moisture and soil temperature
  4. update phenology using weather + soil
  5. initialize canopy growth if emergence occurred
  6. update canopy/biomass using weather + phenology + soil + stress
  7. update biotic pressure using weather + crop stage + canopy + treatment residuals
  8. update yield/recovery if R8 or harvested
  9. generate or update observation caches
  10. sync compatibility fields

Contract:
    advance_physics_time(farm_world_app, target_sim_time) is idempotent.
    It reads farm_world_app.physics.last_physics_sim_time and runs one daily
    tick per crossed UTC date boundary up to target_sim_time. Sub-daily lag
    (no boundary crossed) triggers an immediate-soil-input update when
    irrigation actions are queued. After running, last_physics_sim_time is
    set to target_sim_time, the action queues are drained, and compatibility
    shadows on RidgeState are refreshed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from are.simulation.physics import (
    BioticCropInput,
    BioticSoilInput,
    BioticWeatherInput,
    CanopyPhenologyInput,
    GrowthSoilInput,
    GrowthWeatherInput,
    HarvestAction,
    HiddenRidgeTruth,
    ManagementAction,
    ManagementCropInput,
    ManagementSoilInput,
    ManagementStressInput,
    ManagementWeatherInput,
    PhenologySoilInput,
    PhenologyWeatherInput,
    SeedType,
    SoilWeatherInput,
    SoybeanStage,
    TreatmentApplication,
    YieldGrowthInput,
    YieldPhenologyInput,
    YieldStressInput,
    YieldWeatherInput,
)
from are.simulation.physics.biotic_pressure_engine import (
    GrowthStage as BioticGrowthStage,
)
from are.simulation.physics.canopy_biomass_engine import (
    GrowthStage as CanopyGrowthStage,
    SeedType as CanopySeedType,
)
from are.simulation.physics.management_effect_engine import (
    GrowthStage as ManagementGrowthStage,
)
from are.simulation.physics.yield_recovery_engine import (
    GrowthStage as YieldGrowthStage,
)

if TYPE_CHECKING:
    from are.simulation.apps.farm_world.farm_physics_state import FarmPhysicsState
    from are.simulation.apps.farm_world.farm_world_app import FarmWorldApp
    from are.simulation.apps.farm_world.weather_app import WeatherApp


_SECONDS_PER_DAY: float = 86400.0
_DEFAULT_DIURNAL_RANGE_C: float = 5.0
_W_PER_M2_TO_MJ_PER_M2_PER_DAY: float = 0.0864  # 1 W/m^2 * 86400 s * 1e-6


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def advance_physics_time(
    farm_world_app: "FarmWorldApp", target_sim_time: float
) -> dict[str, Any]:
    """Advance physics to ``target_sim_time``.

    Idempotent. Returns a status dict describing how many daily ticks ran
    and whether a sub-daily soil update was applied.
    """
    physics = farm_world_app._physics
    if physics is None or not physics.engines_active:
        return {"status": "skipped", "reason": "physics_inactive"}

    weather_app = _find_weather_app(farm_world_app)

    # First call seeds engine state from the scenario's initial ridge values.
    if physics.last_physics_sim_time is None:
        _seed_physics_from_ridges(farm_world_app, target_sim_time)
        subdaily_first_call = False
        # If a tool queued actions before the first advance_physics_time call
        # (e.g. plant_seeds calls advance after queuing), apply them now via
        # the sub-daily injection so the next state read reflects the action.
        if (
            physics.pending_management_actions_by_ridge
            or physics.pending_treatments_by_ridge
        ):
            subdaily_first_call = _run_subdaily_injection(
                physics, weather_app, target_sim_time
            )
        sync_compatibility_fields_from_physics(farm_world_app)
        return {
            "status": "initialized",
            "day_ticks_run": 0,
            "subdaily_irrigation": subdaily_first_call,
            "last_physics_sim_time": target_sim_time,
        }

    elapsed = target_sim_time - physics.last_physics_sim_time
    if elapsed <= 0.0:
        return {"status": "noop", "day_ticks_run": 0, "elapsed_s": elapsed}

    days_to_run = _dates_between(physics.last_physics_sim_time, target_sim_time)

    subdaily_irrigation_applied = False
    if not days_to_run and (
        physics.pending_management_actions_by_ridge
        or physics.pending_treatments_by_ridge
    ):
        subdaily_irrigation_applied = _run_subdaily_injection(
            physics, weather_app, target_sim_time
        )

    for tick_day in days_to_run:
        _run_daily_tick(physics, weather_app, tick_day)

    physics.last_physics_sim_time = target_sim_time
    sync_compatibility_fields_from_physics(farm_world_app)

    return {
        "status": "advanced",
        "day_ticks_run": len(days_to_run),
        "subdaily_irrigation": subdaily_irrigation_applied,
        "elapsed_s": elapsed,
        "last_physics_sim_time": target_sim_time,
    }


# ---------------------------------------------------------------------------
# Helpers — date arithmetic
# ---------------------------------------------------------------------------


def _dates_between(t_from: float, t_to: float) -> list[date]:
    """Return the list of UTC dates strictly after t_from and up to t_to.

    For the irrigation scenario starting at 07:00 UTC and waiting 2 hours
    to 09:00 same day, this returns []. For a 26-hour wait across midnight,
    it returns [next_day].
    """
    if t_to <= t_from:
        return []
    d_from = datetime.fromtimestamp(t_from, tz=timezone.utc).date()
    d_to = datetime.fromtimestamp(t_to, tz=timezone.utc).date()
    if d_to <= d_from:
        return []
    out: list[date] = []
    current = d_from
    while current < d_to:
        current = current + timedelta(days=1)
        out.append(current)
    return out


# ---------------------------------------------------------------------------
# Helpers — initial seeding from scenario state
# ---------------------------------------------------------------------------


def _seed_physics_from_ridges(
    farm_world_app: "FarmWorldApp", target_sim_time: float
) -> None:
    """Seed engine state from the ridge values the scenario set up.

    Most baseline scenarios (irrigation, planting, etc.) configure
    ``r.soil_vwc``, ``r.soil_temp_c``, ``r.growth_stage``, ``r.planted``,
    ``r.days_since_planted``, ``r.seed_type`` directly in
    ``_configure_initial_state``. We mirror those into the engines so the
    first daily tick starts from the scenario's intended world state, not
    from engine defaults (0.25 VWC, NOT_PLANTED).
    """
    physics = farm_world_app.physics
    physics.last_physics_sim_time = target_sim_time
    today = datetime.fromtimestamp(target_sim_time, tz=timezone.utc).date()

    for ridge in farm_world_app._ridges:
        rid = ridge.ridge_id

        # Soil layer.
        soil_state = physics.soil.states[rid]
        soil_state.top_vwc = float(ridge.soil_vwc)
        soil_state.root_vwc = float(ridge.soil_vwc)
        soil_state.top_temp_c = float(ridge.soil_temp_c)
        soil_state.root_temp_c = float(ridge.soil_temp_c)

        # Phenology layer.
        # Skip if the engine already has explicit planting state (i.e., a
        # previous plant_seeds call has already initialised phenology). This
        # preserves plant_seeds's PLANTED_PRE_EMERGENCE intent against the
        # legacy growth_stage="VE" that set_ridge_planted writes.
        phen_state = physics.phenology.states[rid]
        if ridge.planted and not phen_state.planted:
            phen_state.planted = True
            phen_state.planting_date = today - timedelta(days=int(ridge.days_since_planted))
            phen_state.seed_type = _coerce_seed_type(ridge.seed_type)
            phen_state.days_after_planting = int(ridge.days_since_planted)
            mapped = _map_legacy_growth_stage_to_soybean_stage(ridge.growth_stage)
            phen_state.stage = mapped
            phen_state.emerged = mapped not in {
                SoybeanStage.NOT_PLANTED,
                SoybeanStage.PLANTED_PRE_EMERGENCE,
            }
            if phen_state.emerged and phen_state.emergence_date is None:
                phen_state.emergence_date = phen_state.planting_date
            # Seed effective_development_gdd so the engine's
            # _stage_from_effective_gdd doesn't snap back to VE on the next
            # daily tick. We pick the stage's threshold fraction × gdd_to_r8.
            seed_type = phen_state.seed_type
            if seed_type is not None and seed_type in physics.phenology.seed_type_params:
                seed_params = physics.phenology.seed_type_params[seed_type]
                fraction = physics.phenology.params.stage_fraction_thresholds.get(
                    mapped, 0.0
                )
                phen_state.effective_development_gdd = float(
                    seed_params.gdd_to_r8 * fraction
                )
                phen_state.accumulated_gdd = max(
                    phen_state.accumulated_gdd,
                    phen_state.effective_development_gdd,
                )
                if mapped == SoybeanStage.R8 and phen_state.maturity_date is None:
                    phen_state.maturity_date = phen_state.planting_date

        # Biotic baseline reflects whatever the scenario configured for pest
        # outbreaks via _configure_initial_state (carries through r.pest_pressure_base).
        biotic_state = physics.biotic.states[rid]
        biotic_state.insect_pressure = max(
            biotic_state.insect_pressure, float(ridge.pest_pressure_base)
        )
        biotic_state.disease_pressure = max(
            biotic_state.disease_pressure, float(ridge.disease_pressure_base)
        )

        # Canopy initialization for already-emerged crops in the seeded
        # scenario state. Use the phenology engine's stage as the source of
        # truth: if phenology says PLANTED_PRE_EMERGENCE (e.g. a freshly
        # plant_seeds-ed ridge), skip canopy init even though the legacy
        # ridge.growth_stage may temporarily say "VE". Daily ticks will init
        # canopy when emergence actually happens.
        canopy_state = physics.canopy.states[rid]
        if ridge.planted and not canopy_state.initialized:
            phen_stage = phen_state.stage
            if phen_stage in {SoybeanStage.NOT_PLANTED, SoybeanStage.PLANTED_PRE_EMERGENCE}:
                # Engine has authoritative pre-emergence state — don't init.
                pass
            else:
                # Engine already at a post-emergence stage (or this ridge had
                # planted=True from _configure_initial_state and we just set
                # the engine stage from ridge.growth_stage).
                seed_type = _coerce_seed_type(ridge.seed_type) or SeedType.STANDARD
                physics.canopy.initialize_ridges(
                    [rid],
                    seed_type=CanopySeedType(seed_type.value),
                    initial_stand_fraction=1.0,
                )


def _coerce_seed_type(seed_value: str | None) -> SeedType | None:
    if seed_value is None:
        return None
    try:
        return SeedType(seed_value)
    except ValueError:
        return SeedType.STANDARD


def _map_legacy_growth_stage_to_soybean_stage(legacy_stage: str) -> SoybeanStage:
    """Map farm_world.models.GrowthStage values to phenology SoybeanStage.

    Legacy farm_world enum: BARE, VE, V1..V8, R1..R8.
    SoybeanStage:           NOT_PLANTED, PLANTED_PRE_EMERGENCE, VE, VC, V1..V3,
                            V4_PLUS, R1..R8 (R values use longer string codes).
    """
    if not legacy_stage or legacy_stage == "bare":
        return SoybeanStage.NOT_PLANTED
    direct_map = {
        "VE": SoybeanStage.VE,
        "VC": SoybeanStage.VC,
        "V1": SoybeanStage.V1,
        "V2": SoybeanStage.V2,
        "V3": SoybeanStage.V3,
    }
    if legacy_stage in direct_map:
        return direct_map[legacy_stage]
    if legacy_stage.startswith("V"):
        return SoybeanStage.V4_PLUS
    r_map = {
        "R1": SoybeanStage.R1,
        "R3": SoybeanStage.R3,
        "R5": SoybeanStage.R5,
        "R6": SoybeanStage.R6,
        "R7": SoybeanStage.R7,
        "R8": SoybeanStage.R8,
    }
    if legacy_stage in r_map:
        return r_map[legacy_stage]
    if legacy_stage in {"R2"}:
        return SoybeanStage.R1
    if legacy_stage in {"R4"}:
        return SoybeanStage.R3
    return SoybeanStage.NOT_PLANTED


def _map_soybean_stage_to_legacy(stage: SoybeanStage) -> str:
    """Inverse mapping for compatibility shadowing."""
    if stage == SoybeanStage.NOT_PLANTED:
        return "bare"
    if stage == SoybeanStage.PLANTED_PRE_EMERGENCE:
        return "bare"
    if stage == SoybeanStage.V4_PLUS:
        return "V4"
    legacy_map = {
        SoybeanStage.VE: "VE",
        SoybeanStage.VC: "VE",
        SoybeanStage.V1: "V1",
        SoybeanStage.V2: "V2",
        SoybeanStage.V3: "V3",
        SoybeanStage.R1: "R1",
        SoybeanStage.R3: "R3",
        SoybeanStage.R5: "R5",
        SoybeanStage.R6: "R6",
        SoybeanStage.R7: "R7",
        SoybeanStage.R8: "R8",
    }
    return legacy_map.get(stage, "bare")


# ---------------------------------------------------------------------------
# Helpers — daily tick
# ---------------------------------------------------------------------------


def _run_daily_tick(
    physics: "FarmPhysicsState",
    weather_app: "WeatherApp | None",
    day: date,
) -> None:
    """Run one full daily tick following the integration-guide order."""
    weather = _build_weather_inputs(weather_app, day, physics=physics)
    _apply_biotic_outbreaks_for_day(physics, day)

    pending_actions = physics.drain_pending_management_actions()
    pending_treatments = physics.drain_pending_treatments()
    pending_harvest = physics.drain_pending_harvest_actions()

    # 1+2. Management engine consumes today's actions and updates residuals.
    soil_inputs_for_management = _build_management_soil_inputs(physics)
    crop_inputs_for_management = _build_management_crop_inputs(physics)
    physics.management.update_day(
        weather=weather["management"],
        actions_by_ridge=pending_actions,
        soil_by_ridge=soil_inputs_for_management,
        crop_by_ridge=crop_inputs_for_management,
    )

    # 3. Soil engine consumes today's irrigation as water input.
    irrigation_mm = physics.management.irrigation_mm_by_ridge(pending_actions)
    canopy_cover_by_ridge = {
        rid: state.canopy_cover for rid, state in physics.canopy.states.items()
    }
    physics.soil.update_day(
        weather=weather["soil"],
        irrigation_mm_by_ridge=irrigation_mm,
        canopy_cover_by_ridge=canopy_cover_by_ridge,
    )

    # 4. Phenology consumes weather + soil.
    phenology_soil_inputs = {
        rid: PhenologySoilInput(
            top_temp_c=physics.soil.states[rid].top_temp_c,
            top_vwc=physics.soil.states[rid].top_vwc,
        )
        for rid in physics.soil.states
    }
    phenology_results = physics.phenology.update_day(
        weather=weather["phenology"],
        soil_by_ridge=phenology_soil_inputs,
    )

    # 5. Initialize canopy state for ridges that just emerged.
    for result in phenology_results:
        if result.emerged:
            canopy_state = physics.canopy.states[result.ridge_id]
            if not canopy_state.initialized:
                phen_state = physics.phenology.states[result.ridge_id]
                seed_type_value = (
                    phen_state.seed_type.value
                    if phen_state.seed_type is not None
                    else SeedType.STANDARD.value
                )
                stand_fraction = physics.management.states[
                    result.ridge_id
                ].stand_fraction or 1.0
                physics.canopy.initialize_ridges(
                    [result.ridge_id],
                    seed_type=CanopySeedType(seed_type_value),
                    initial_stand_fraction=stand_fraction,
                )

    # 6. Canopy / biomass.
    canopy_phenology_inputs = {
        rid: CanopyPhenologyInput(
            stage=CanopyGrowthStage(physics.phenology.states[rid].stage.value),
            development_fraction=_phenology_development_fraction(
                physics.phenology.states[rid]
            ),
        )
        for rid in physics.phenology.states
    }
    canopy_soil_inputs = {
        rid: GrowthSoilInput(
            water_stress=_compute_water_stress(physics.soil.states[rid].root_vwc),
            root_vwc=physics.soil.states[rid].root_vwc,
        )
        for rid in physics.soil.states
    }
    canopy_management_inputs = {
        rid: ManagementStressInput(
            nutrient_stress=physics.management.states[rid].nutrient_stress,
            biotic_stress=_compute_biotic_stress(physics.biotic.states[rid]),
            stand_fraction=physics.management.states[rid].stand_fraction or 1.0,
        )
        for rid in physics.management.states
    }
    canopy_results = physics.canopy.update_day(
        weather=weather["canopy"],
        phenology_by_ridge=canopy_phenology_inputs,
        soil_by_ridge=canopy_soil_inputs,
        management_by_ridge=canopy_management_inputs,
    )

    # 7. Biotic pressure.
    biotic_crop_inputs = {
        rid: BioticCropInput(
            stage=BioticGrowthStage(physics.phenology.states[rid].stage.value),
            canopy_cover=physics.canopy.states[rid].canopy_cover,
        )
        for rid in physics.phenology.states
    }
    biotic_soil_inputs = {
        rid: BioticSoilInput(
            top_vwc=physics.soil.states[rid].top_vwc,
            root_vwc=physics.soil.states[rid].root_vwc,
        )
        for rid in physics.soil.states
    }
    physics.biotic.update_day(
        weather=weather["biotic"],
        crop_by_ridge=biotic_crop_inputs,
        soil_by_ridge=biotic_soil_inputs,
        treatments_by_ridge=pending_treatments,
    )

    # 8. Yield recovery (cheap; ignores ridges not yet at R5+).
    yield_phenology = {
        rid: YieldPhenologyInput(
            stage=YieldGrowthStage(physics.phenology.states[rid].stage.value),
            maturity_date=physics.phenology.states[rid].maturity_date,
        )
        for rid in physics.phenology.states
    }
    yield_growth = {
        rid: YieldGrowthInput(
            yield_potential_g_m2=state.yield_potential_g_m2,
            aboveground_biomass_g_m2=state.aboveground_biomass_g_m2,
        )
        for rid, state in physics.canopy.states.items()
    }
    yield_stress = {
        rid: YieldStressInput(
            disease_severity=physics.biotic.states[rid].disease_pressure,
            insect_pod_damage=physics.biotic.states[rid].insect_pressure,
        )
        for rid in physics.biotic.states
    }
    physics.yield_recovery.update_day(
        weather=weather["yield"],
        phenology_by_ridge=yield_phenology,
        growth_by_ridge=yield_growth,
        stress_by_ridge=yield_stress,
        harvest_actions_by_ridge=pending_harvest,
    )

    # 9. Observation cache regeneration is deferred — observation tools
    # call ObservationModel directly with the current truth on demand.

    # 10. Compatibility-field sync runs once after the loop in the entry
    # point, since multiple daily ticks can happen back-to-back.


def _build_weather_inputs(
    weather_app: "WeatherApp | None",
    day: date,
    physics: "FarmPhysicsState | None" = None,
) -> dict[str, Any]:
    """Translate the day's weather into per-engine inputs.

    Two pathways:

    1. **Round-4 profile mode**: when ``physics.weather_generator`` is set
       (because ``configure_physics_profile`` matched a registered
       PhysicsProfile), we draw the day's weather deterministically from
       the generator and *push it into WeatherApp* so the agent's
       ``get_current_weather()`` reflects the generated value.

    2. **Round 1+2+3 static mode**: read whatever ``WeatherApp.set_weather``
       last installed.

    All seven engines accept slightly different fields that cover the same
    underlying day. We approximate min/max temp around the mean when the
    static path is in use.
    """
    generated_day = None
    if physics is not None and getattr(physics, "weather_generator", None) is not None:
        try:
            generated_day = _generate_weather_day(physics.weather_generator, day)
        except Exception:  # pragma: no cover — defensive
            generated_day = None

    if generated_day is not None:
        temp_mean = float(generated_day.air_temp_mean_c)
        temp_min = float(generated_day.air_temp_min_c)
        temp_max = float(generated_day.air_temp_max_c)
        rain_mm = float(generated_day.rain_mm)
        wind_ms = float(generated_day.wind_ms)
        solar_mj = float(generated_day.solar_rad_mj_m2)
        solar_w = solar_mj / _W_PER_M2_TO_MJ_PER_M2_PER_DAY
        # Push the generated values into WeatherApp so agent reads see them.
        if weather_app is not None:
            weather_app.set_weather(
                date=day.isoformat(),
                temp_c=temp_mean,
                humidity_pct=55.0,  # weather generator doesn't model RH; static default
                wind_speed_ms=wind_ms,
                rainfall_mm=rain_mm,
                solar_radiation=solar_w,
                forecast=[],
            )
    elif weather_app is not None:
        snap = weather_app.get_current_weather_snapshot()
        temp_mean = float(snap.get("temp_c", 18.0))
        rain_mm = float(snap.get("rainfall_mm", 0.0))
        wind_ms = float(snap.get("wind_speed_ms", 1.0))
        solar_w = float(snap.get("solar_radiation", 400.0))
        temp_min = temp_mean - _DEFAULT_DIURNAL_RANGE_C
        temp_max = temp_mean + _DEFAULT_DIURNAL_RANGE_C
        solar_mj = solar_w * _W_PER_M2_TO_MJ_PER_M2_PER_DAY
    else:
        temp_mean = 18.0
        rain_mm = 0.0
        wind_ms = 1.0
        solar_w = 400.0
        temp_min = temp_mean - _DEFAULT_DIURNAL_RANGE_C
        temp_max = temp_mean + _DEFAULT_DIURNAL_RANGE_C
        solar_mj = solar_w * _W_PER_M2_TO_MJ_PER_M2_PER_DAY

    return {
        "soil": SoilWeatherInput(
            day=day,
            air_temp_mean_c=temp_mean,
            air_temp_min_c=temp_min,
            air_temp_max_c=temp_max,
            rain_mm=rain_mm,
            solar_rad_mj_m2=solar_mj,
            wind_ms=wind_ms,
        ),
        "phenology": PhenologyWeatherInput(
            day=day,
            air_temp_min_c=temp_min,
            air_temp_max_c=temp_max,
            air_temp_mean_c=temp_mean,
        ),
        "canopy": GrowthWeatherInput(
            day=day,
            solar_rad_mj_m2=solar_mj,
            air_temp_mean_c=temp_mean,
        ),
        "biotic": BioticWeatherInput(
            day=day,
            air_temp_mean_c=temp_mean,
            rain_mm=rain_mm,
            is_raining=rain_mm > 0.0,
        ),
        "management": ManagementWeatherInput(
            day=day,
            rain_mm=rain_mm,
            wind_ms=wind_ms,
        ),
        "yield": YieldWeatherInput(
            day=day,
            air_temp_mean_c=temp_mean,
            rain_mm=rain_mm,
            solar_rad_mj_m2=solar_mj,
            wind_ms=wind_ms,
        ),
    }


def _build_management_soil_inputs(
    physics: "FarmPhysicsState",
) -> dict[int, ManagementSoilInput]:
    return {
        rid: ManagementSoilInput(
            top_vwc=state.top_vwc,
            root_vwc=state.root_vwc,
        )
        for rid, state in physics.soil.states.items()
    }


def _build_management_crop_inputs(
    physics: "FarmPhysicsState",
) -> dict[int, ManagementCropInput]:
    return {
        rid: ManagementCropInput(
            stage=ManagementGrowthStage(physics.phenology.states[rid].stage.value),
            daily_biomass_g_m2=0.0,
        )
        for rid in physics.phenology.states
    }


def _phenology_development_fraction(state: Any) -> float:
    """Approximate development fraction in [0, 1] for canopy LAI curve."""
    stage_progress = {
        SoybeanStage.NOT_PLANTED: 0.0,
        SoybeanStage.PLANTED_PRE_EMERGENCE: 0.0,
        SoybeanStage.VE: 0.05,
        SoybeanStage.VC: 0.08,
        SoybeanStage.V1: 0.12,
        SoybeanStage.V2: 0.18,
        SoybeanStage.V3: 0.25,
        SoybeanStage.V4_PLUS: 0.40,
        SoybeanStage.R1: 0.55,
        SoybeanStage.R3: 0.70,
        SoybeanStage.R5: 0.82,
        SoybeanStage.R6: 0.90,
        SoybeanStage.R7: 0.95,
        SoybeanStage.R8: 1.0,
    }
    return stage_progress.get(state.stage, 0.0)


def _compute_water_stress(root_vwc: float) -> float:
    """0–1 multiplier where 1 is no water stress, 0 is severe stress.

    Linear ramp between wilting (0.12) and field capacity (0.30) using the
    soil engine's defaults; matches the soil engine's internal stress curve
    closely enough for canopy daily growth.
    """
    if root_vwc >= 0.22:
        return 1.0
    if root_vwc <= 0.12:
        return 0.0
    return (root_vwc - 0.12) / (0.22 - 0.12)


def _compute_biotic_stress(biotic_state: Any) -> float:
    """Translate biotic pressures to a 0–1 growth multiplier."""
    weighted = (
        0.28 * biotic_state.weed_pressure
        + 0.22 * biotic_state.insect_pressure
        + 0.30 * biotic_state.disease_pressure
    )
    return max(0.35, 1.0 - weighted)


def _apply_biotic_outbreaks_for_day(
    physics: "FarmPhysicsState", day: date
) -> None:
    """Inject scheduled biotic outbreaks from the active PhysicsProfile.

    Each ``BioticOutbreak`` raises insect / disease / weed pressure on the
    affected ridge range to its target severity for the duration of the
    outbreak. Applied at the start of the daily tick so subsequent biotic
    engine evolution starts from the elevated baseline.
    """
    profile = getattr(physics, "profile", None)
    if profile is None:
        return
    outbreaks = getattr(profile, "biotic_outbreaks", None) or []
    if not outbreaks:
        return
    start_date = getattr(profile, "start_date", None)
    if start_date is None:
        return
    days_since_start = (day - start_date).days
    if days_since_start < 0:
        return
    from are.simulation.physics.biotic_pressure_engine import TreatmentType

    for outbreak in outbreaks:
        if not (
            outbreak.start_day_offset
            <= days_since_start
            < outbreak.start_day_offset + outbreak.duration_days
        ):
            continue
        ridge_ids = list(range(outbreak.ridge_start, outbreak.ridge_end + 1))
        if outbreak.pressure_type == TreatmentType.INSECTICIDE:
            physics.biotic.set_pressure(
                ridge_ids, insect_pressure=float(outbreak.severity)
            )
        elif outbreak.pressure_type == TreatmentType.FUNGICIDE:
            physics.biotic.set_pressure(
                ridge_ids, disease_pressure=float(outbreak.severity)
            )
        elif outbreak.pressure_type == TreatmentType.HERBICIDE:
            physics.biotic.set_pressure(
                ridge_ids, weed_pressure=float(outbreak.severity)
            )


def _generate_weather_day(weather_generator, day: date):
    """Generate one day from the WeatherGenerator including event overrides.

    The generator produces a window of days; we ask for a one-day slice
    using a per-day cache to keep day-to-day determinism (re-asking for
    the same date returns the same value across a single physics state).
    """
    cache = getattr(weather_generator, "_day_cache", None)
    if cache is None:
        cache = {}
        try:
            weather_generator._day_cache = cache  # type: ignore[attr-defined]
        except Exception:
            pass
    if day in cache:
        return cache[day]
    days = weather_generator.generate(start_date=day, end_date=day)
    if not days:
        return None
    cache[day] = days[0]
    return days[0]


# ---------------------------------------------------------------------------
# Helpers — sub-daily injection (irrigation, treatments)
# ---------------------------------------------------------------------------


def _run_subdaily_injection(
    physics: "FarmPhysicsState",
    weather_app: "WeatherApp | None",
    target_sim_time: float,
) -> bool:
    """Process pending actions inside a sub-daily lag (no UTC date crossed).

    Feeds pending management actions to the management engine (so
    cumulative_irrigation_mm / treatment residuals are recorded), pushes
    irrigation water into soil VWC so sensor reads reflect it, and applies
    treatments to the biotic engine. Returns True if any irrigation was
    injected.
    """
    today = datetime.fromtimestamp(target_sim_time, tz=timezone.utc).date()
    weather = _build_weather_inputs(weather_app, today, physics=physics)
    actions = physics.drain_pending_management_actions()
    treatments = physics.drain_pending_treatments()
    physics.drain_pending_harvest_actions()  # harvest is daily-only

    if actions:
        physics.management.update_day(
            weather=weather["management"],
            actions_by_ridge=actions,
            soil_by_ridge=_build_management_soil_inputs(physics),
            crop_by_ridge=_build_management_crop_inputs(physics),
        )
    if treatments:
        physics.biotic.update_day(
            weather=weather["biotic"],
            crop_by_ridge={
                rid: BioticCropInput(
                    stage=BioticGrowthStage(physics.phenology.states[rid].stage.value),
                    canopy_cover=physics.canopy.states[rid].canopy_cover,
                )
                for rid in physics.phenology.states
            },
            soil_by_ridge={
                rid: BioticSoilInput(
                    top_vwc=physics.soil.states[rid].top_vwc,
                    root_vwc=physics.soil.states[rid].root_vwc,
                )
                for rid in physics.soil.states
            },
            treatments_by_ridge=treatments,
        )

    irrigation_mm = physics.management.irrigation_mm_by_ridge(actions)
    if irrigation_mm:
        _apply_subdaily_irrigation(physics, irrigation_mm)
        return True
    return False


def _apply_subdaily_irrigation(
    physics: "FarmPhysicsState", irrigation_mm_by_ridge: dict[int, float]
) -> None:
    """Inject pending irrigation directly into soil VWC, bypassing ET.

    Used when a tool calls advance_physics_time inside a sub-daily lag (e.g.
    the irrigation 2-hour wait). The full daily cycle (ET, drainage,
    phenology, canopy) is deferred to the next day boundary; only the
    irrigation inflow is reflected immediately so sensor reads see the
    expected VWC bump.
    """
    p = physics.soil.params
    top_depth_mm = p.top_depth_m * 1000.0
    root_depth_mm = p.root_depth_m * 1000.0
    top_sat = p.saturation_vwc * top_depth_mm
    top_fc = p.field_capacity_vwc * top_depth_mm
    root_sat = p.saturation_vwc * root_depth_mm

    for ridge_id, mm in irrigation_mm_by_ridge.items():
        state = physics.soil.states.get(ridge_id)
        if state is None or mm <= 0.0:
            continue
        infiltrated = min(
            float(mm) * p.irrigation_efficiency, p.max_infiltration_mm_day
        )
        top_storage = state.top_vwc * top_depth_mm + infiltrated
        if top_storage > top_sat:
            top_storage = top_sat
        # Move the portion above field capacity into the root zone.
        top_excess_above_fc = max(0.0, top_storage - top_fc)
        percolation = p.top_drainage_rate * top_excess_above_fc
        top_storage -= percolation
        root_storage = state.root_vwc * root_depth_mm + percolation
        if root_storage > root_sat:
            root_storage = root_sat
        state.top_vwc = max(p.wilting_point_vwc, min(p.saturation_vwc, top_storage / top_depth_mm))
        state.root_vwc = max(p.wilting_point_vwc, min(p.saturation_vwc, root_storage / root_depth_mm))


# ---------------------------------------------------------------------------
# Helpers — compatibility sync (physics → RidgeState)
# ---------------------------------------------------------------------------


def sync_compatibility_fields_from_physics(farm_world_app: "FarmWorldApp") -> None:
    """Mirror engine outputs back onto RidgeState fields legacy code reads.

    The mappings follow the integration guide's "Compatibility synchronization"
    table. Pest/disease/yield are exposed as truth-mirror fields here so the
    legacy validators and the sensor cache continue to work; in the long term
    these reads should go through ObservationModel.
    """
    physics = farm_world_app._physics
    if physics is None or not physics.engines_active:
        return

    for ridge in farm_world_app._ridges:
        rid = ridge.ridge_id
        soil = physics.soil.states[rid]
        # ridge.soil_vwc represents the 5-cm-depth sensor measurement
        # (per RidgeState comment); that is the top layer in physics terms.
        ridge.soil_vwc = float(soil.top_vwc)
        ridge.soil_temp_c = float(soil.top_temp_c)

        phen = physics.phenology.states[rid]
        if phen.planted:
            ridge.planted = True
            ridge.days_since_planted = int(phen.days_after_planting)
            ridge.growth_stage = _map_soybean_stage_to_legacy(phen.stage)

        biotic = physics.biotic.states[rid]
        ridge.pest_pressure = float(biotic.insect_pressure)
        ridge.disease_pressure = float(biotic.disease_pressure)
        # Pest/disease baselines mirror the latent pressure so legacy code
        # reading the *_base fields gets a consistent number.
        ridge.pest_pressure_base = float(biotic.insect_pressure)
        ridge.disease_pressure_base = float(biotic.disease_pressure)

        canopy = physics.canopy.states[rid]
        if canopy.initialized:
            # Leave ridge.ndvi alone — that field is the agent-visible last
            # observation populated by drone/sensor tools through
            # ObservationModel. Truth is in canopy.ndvi_proxy and stays
            # behind the observation boundary.
            ridge.yield_potential = float(canopy.yield_potential_g_m2 / 350.0)
            ridge.yield_potential = max(0.0, min(1.0, ridge.yield_potential))

        yld = physics.yield_recovery.states[rid]
        if yld.grain_moisture_frac is not None:
            ridge.grain_moisture_pct = float(yld.grain_moisture_frac * 100.0)


# ---------------------------------------------------------------------------
# Helpers — finding the WeatherApp from the current Environment
# ---------------------------------------------------------------------------


def _find_weather_app(farm_world_app: "FarmWorldApp") -> "WeatherApp | None":
    """Return the WeatherApp attached during scenario init.

    Set by FieldOpsApp / TractorApp / DroneApp / RobotApp __init__ via
    farm_world_app.attach_weather_app(...). Returns None for tests that
    construct FarmWorldApp standalone.
    """
    return getattr(farm_world_app, "_weather_app", None)
