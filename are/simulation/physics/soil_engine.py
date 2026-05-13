from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Mapping


@dataclass
class SoilParameters:
    """
    Parameters for the Farm-ARE reduced soil physics engine.

    Scientific basis:
        The engine follows the standard daily soil-water-balance structure used in
        crop simulators such as DSSAT and APSIM-SoilWat: water enters through
        rainfall and irrigation, then exits through runoff, drainage, soil
        evaporation, and plant transpiration. It also follows the FAO-56 crop
        water accounting idea that evapotranspiration can be separated into
        soil evaporation and crop transpiration components.

    Engineering simplification:
        This is not a full DSSAT/APSIM soil module and does not solve Richards'
        equation. It collapses the soil into two conceptual layers:
          - top layer: planting readiness, seed-zone moisture/temperature,
            evaporation, and trafficability
          - root-zone layer: crop-accessible water and water stress

        The parameter values below are defaults for scenario generation and should
        be treated as tunable hyperparameters, not site-calibrated measurements.
    """

    # Layer depths.
    # The top layer approximates the seed/topsoil region used for planting decisions.
    # The root layer approximates the crop-accessible water zone used for growth stress.
    top_depth_m: float = 0.10
    root_depth_m: float = 0.4

    # Soil hydraulic thresholds in volumetric water content (VWC, m3/m3).
    # These values are representative defaults for a loam-like soil profile.
    wilting_point_vwc: float = 0.14
    field_capacity_vwc: float = 0.36
    saturation_vwc: float = 0.48

    # Simplified infiltration / redistribution parameters.
    # These replace a multi-layer infiltration model with bounded daily inflow
    # and drainage fractions.
    max_infiltration_mm_day: float = 35.0
    top_drainage_rate: float = 0.65
    root_drainage_rate: float = 0.45

    # Effective capture factors for rainfall and irrigation.
    # These represent losses from surface runoff, nonuniform application, or
    # imperfect ridge-level delivery.
    irrigation_efficiency: float = 0.92
    rainfall_capture_efficiency: float = 0.85

    # Unsaturated top-to-root redistribution.
    # Gravity drainage above field capacity is handled separately below. This
    # term represents slower redistribution when the top layer is wetter than
    # the root zone but not necessarily above field capacity. It keeps water
    # mass-conserving: mm removed from the top layer are added to the root layer.
    # Default 0 keeps historical behaviour unless a scenario profile enables it.
    top_root_redistribution_rate: float = 0.18
    top_root_redistribution_deadband_vwc: float = 0.03

    # Planting constraints use the top layer, not the root zone.
    # The 0.20-0.30 VWC interval is the preferred operational range, with
    # lower/higher bounds used for marginal or blocked conditions.
    planting_temp_min_c: float = 10.0
    planting_vwc_min: float = 0.20
    planting_vwc_max: float = 0.30
    planting_vwc_too_dry: float = 0.15
    planting_vwc_too_wet: float = 0.35

    # Growth water-stress constraints use the root zone.
    water_stress_vwc: float = 0.18
    irrigation_trigger_vwc: float = 0.17

    # Soil temperature response.
    # Soil temperature is represented as a lagged response to air temperature.
    # The top layer responds faster than the root-zone layer.
    top_temp_response: float = 0.40
    root_temp_response: float = 0.18
    rain_cooling_coeff_c_per_10mm: float = 0.25

    # Simplified ET approximation.
    # This is a reduced FAO-56-style representation: radiation and temperature
    # define atmospheric demand, canopy cover partitions demand into exposed-soil
    # evaporation and crop transpiration.
    radiation_et_coeff: float = 0.42
    min_temp_factor: float = 0.40
    max_temp_factor: float = 1.25
    bare_soil_evap_fraction: float = 0.42
    max_crop_coefficient: float = 1.05


@dataclass
class RidgeSoilState:
    """
    Dynamic soil state for one ridge.

    The engine keeps ridges independent. This avoids lateral flow modeling and is
    consistent with the current 1D ridge-indexed Farm-ARE representation.
    """
    ridge_id: int
    top_vwc: float = 0.25
    root_vwc: float = 0.25
    top_temp_c: float = 10.0
    root_temp_c: float = 9.0
    cumulative_runoff_mm: float = 0.0
    cumulative_drainage_mm: float = 0.0
    cumulative_evap_mm: float = 0.0
    cumulative_transpiration_mm: float = 0.0
    tags: list[str] = field(default_factory=list)


@dataclass
class WeatherInput:
    """
    Daily weather forcing consumed by the soil model.

    These values are expected to come from the weather generator/playback module.
    """
    day: date
    air_temp_mean_c: float
    air_temp_min_c: float
    air_temp_max_c: float
    rain_mm: float
    solar_rad_mj_m2: float
    wind_ms: float = 0.0


@dataclass
class SoilDayResult:
    """
    Ridge-level derived state after one daily update.

    These outputs are intended to be consumed by agent tools and downstream
    crop-growth modules rather than exposed as hidden ground truth by default.
    """
    day: date
    ridge_id: int
    top_vwc: float
    root_vwc: float
    top_temp_c: float
    root_temp_c: float
    water_stress: float
    irrigation_recommended: bool
    planting_ready: bool
    trafficability: str
    runoff_mm: float
    drainage_mm: float
    evap_mm: float
    transpiration_mm: float
    tags: list[str]


class SoilEngine:
    """
    Farm-ARE reduced soil engine.

    Purpose:
        Convert exogenous weather and ridge-level irrigation actions into
        soil moisture, soil temperature, trafficability, and water-stress states.

    Scope:
        This model is intended for closed-loop agent scenarios, not agronomic
        site calibration. It should produce realistic directional behavior:
          - rain and irrigation increase VWC
          - hot/sunny/windy days dry the soil
          - larger canopy cover shifts water loss from evaporation to transpiration
          - wet topsoil blocks trafficability and planting
          - low root-zone moisture causes crop water stress

    Non-scope:
        No lateral ridge-to-ridge flow, no capillary rise, no snow/freeze-thaw,
        no full Penman-Monteith ET, no multi-layer soil profile, and no
        mechanistic soil compaction model.
    """

    def __init__(
        self,
        num_ridges: int = 64,
        params: SoilParameters | None = None,
        initial_top_vwc: float = 0.25,
        initial_root_vwc: float = 0.25,
        initial_top_temp_c: float = 10.0,
        initial_root_temp_c: float = 9.0,
    ) -> None:
        self.params = params or SoilParameters()
        self.states: dict[int, RidgeSoilState] = {
            ridge_id: RidgeSoilState(
                ridge_id=ridge_id,
                top_vwc=initial_top_vwc,
                root_vwc=initial_root_vwc,
                top_temp_c=initial_top_temp_c,
                root_temp_c=initial_root_temp_c,
            )
            for ridge_id in range(num_ridges)
        }

    def update_day(
        self,
        weather: WeatherInput,
        irrigation_mm_by_ridge: Mapping[int, float] | None = None,
        canopy_cover_by_ridge: Mapping[int, float] | None = None,
    ) -> list[SoilDayResult]:
        """
        Advance all ridge soil states by one day.

        Args:
            weather:
                Daily weather forcing from the weather engine.
            irrigation_mm_by_ridge:
                Ridge-level irrigation amount in mm water equivalent.
            canopy_cover_by_ridge:
                Ridge-level canopy cover in [0, 1]. Used to partition ET into
                soil evaporation and crop transpiration.

        Returns:
            One SoilDayResult per ridge.
        """
        irrigation_mm_by_ridge = irrigation_mm_by_ridge or {}
        canopy_cover_by_ridge = canopy_cover_by_ridge or {}

        results: list[SoilDayResult] = []
        for ridge_id, state in self.states.items():
            irrigation_mm = max(0.0, float(irrigation_mm_by_ridge.get(ridge_id, 0.0)))
            canopy_cover = self._clip(float(canopy_cover_by_ridge.get(ridge_id, 0.0)), 0.0, 1.0)
            result = self._update_ridge_day(state, weather, irrigation_mm, canopy_cover)
            results.append(result)

        return results

    def get_state(self) -> dict[int, RidgeSoilState]:
        """Return a copy of the ridge soil states."""
        return {
            ridge_id: RidgeSoilState(**vars(state))
            for ridge_id, state in self.states.items()
        }

    def set_state(self, states: Mapping[int, RidgeSoilState]) -> None:
        """Replace the ridge soil state. Used for scenario initialization/replay."""
        self.states = {
            ridge_id: RidgeSoilState(**vars(state))
            for ridge_id, state in states.items()
        }

    def _update_ridge_day(
        self,
        state: RidgeSoilState,
        weather: WeatherInput,
        irrigation_mm: float,
        canopy_cover: float,
    ) -> SoilDayResult:
        p = self.params
        tags: list[str] = []

        top_depth_mm = p.top_depth_m * 1000.0
        root_depth_mm = p.root_depth_m * 1000.0

        top_storage = state.top_vwc * top_depth_mm
        root_storage = state.root_vwc * root_depth_mm

        # Inflow: rainfall and irrigation are converted to effective water
        # reaching the ridge. This is where management actions enter the soil model.
        effective_rain = max(0.0, weather.rain_mm) * p.rainfall_capture_efficiency
        effective_irrigation = irrigation_mm * p.irrigation_efficiency
        incoming = effective_rain + effective_irrigation

        # Daily infiltration cap. Excess becomes runoff.
        infiltrated = min(incoming, p.max_infiltration_mm_day)
        runoff = max(0.0, incoming - infiltrated)
        top_storage += infiltrated

        # Top-layer saturation cap. Any water above saturation moves downward first.
        top_sat = p.saturation_vwc * top_depth_mm
        top_fc = p.field_capacity_vwc * top_depth_mm
        if top_storage > top_sat:
            excess = top_storage - top_sat
            top_storage = top_sat
        else:
            excess = 0.0

        # Simplified gravity drainage from top layer to root zone.
        top_excess_above_fc = max(0.0, top_storage - top_fc)
        percolation = excess + p.top_drainage_rate * top_excess_above_fc
        top_storage -= p.top_drainage_rate * top_excess_above_fc
        root_storage += percolation

        # Slower unsaturated redistribution from wet topsoil to a drier root
        # zone. This complements the above field-capacity drainage path: rain
        # does not need to push the top layer above FC before any water can
        # replenish a dry root layer. The transfer is capped by source water
        # above wilting point and by root-zone room up to field capacity.
        redistribution = self._top_root_redistribution_mm(
            top_storage=top_storage,
            root_storage=root_storage,
            top_depth_mm=top_depth_mm,
            root_depth_mm=root_depth_mm,
        )
        if redistribution > 0.0:
            top_storage -= redistribution
            root_storage += redistribution
            tags.append("top_root_redistribution")

        # Evapotranspiration demand.
        et0 = self._estimate_et0_mm_day(weather)
        evap_demand = et0 * p.bare_soil_evap_fraction * (1.0 - canopy_cover)
        kc = 0.25 + (p.max_crop_coefficient - 0.25) * canopy_cover
        transp_demand = et0 * kc * canopy_cover

        # Soil evaporation is taken from the top layer.
        top_min = p.wilting_point_vwc * top_depth_mm
        evap = min(evap_demand, max(0.0, top_storage - top_min))
        top_storage -= evap

        # Crop transpiration is taken from the root zone and is reduced under stress.
        root_min = p.wilting_point_vwc * root_depth_mm
        root_theta_before_transp = root_storage / root_depth_mm
        water_stress = self._water_stress_factor(root_theta_before_transp)
        transp = min(transp_demand * water_stress, max(0.0, root_storage - root_min))
        root_storage -= transp

        # Root-zone saturation cap and drainage below the modeled root zone.
        root_sat = p.saturation_vwc * root_depth_mm
        root_fc = p.field_capacity_vwc * root_depth_mm

        if root_storage > root_sat:
            runoff += root_storage - root_sat
            root_storage = root_sat

        root_excess_above_fc = max(0.0, root_storage - root_fc)
        drainage = p.root_drainage_rate * root_excess_above_fc
        root_storage -= drainage

        # Convert back to VWC and clip to physical bounds.
        state.top_vwc = self._clip(top_storage / top_depth_mm, p.wilting_point_vwc, p.saturation_vwc)
        state.root_vwc = self._clip(root_storage / root_depth_mm, p.wilting_point_vwc, p.saturation_vwc)

        # Soil temperature update.
        # This is a lag model rather than a heat-transfer model.
        rain_cooling = p.rain_cooling_coeff_c_per_10mm * (weather.rain_mm / 10.0)
        target_top_temp = weather.air_temp_mean_c - rain_cooling
        target_root_temp = weather.air_temp_mean_c - 0.5 * rain_cooling

        state.top_temp_c += p.top_temp_response * (target_top_temp - state.top_temp_c)
        state.root_temp_c += p.root_temp_response * (target_root_temp - state.root_temp_c)

        # Derived operational states used by agent tools and scenario logic.
        planting_ready, planting_tags = self._planting_ready(state)
        tags.extend(planting_tags)

        trafficability = self._trafficability(state, weather)
        if trafficability != "good":
            tags.append(f"trafficability_{trafficability}")

        irrigation_recommended = state.root_vwc <= p.irrigation_trigger_vwc

        if state.root_vwc <= p.water_stress_vwc:
            tags.append("water_stress")
        if irrigation_mm > 0:
            tags.append("irrigated")
        if weather.rain_mm > 0:
            tags.append("rain_input")

        state.cumulative_runoff_mm += runoff
        state.cumulative_drainage_mm += drainage
        state.cumulative_evap_mm += evap
        state.cumulative_transpiration_mm += transp
        state.tags = tags

        return SoilDayResult(
            day=weather.day,
            ridge_id=state.ridge_id,
            top_vwc=round(state.top_vwc, 4),
            root_vwc=round(state.root_vwc, 4),
            top_temp_c=round(state.top_temp_c, 2),
            root_temp_c=round(state.root_temp_c, 2),
            water_stress=round(water_stress, 3),
            irrigation_recommended=irrigation_recommended,
            planting_ready=planting_ready,
            trafficability=trafficability,
            runoff_mm=round(runoff, 2),
            drainage_mm=round(drainage, 2),
            evap_mm=round(evap, 2),
            transpiration_mm=round(transp, 2),
            tags=tags,
        )

    def _estimate_et0_mm_day(self, weather: WeatherInput) -> float:
        """
        Reduced reference ET approximation.

        Scientific basis:
            FAO-56 estimates crop water demand through reference ET and crop
            coefficients. Full Penman-Monteith requires radiation, humidity,
            wind, and temperature inputs. This reduced version uses available
            radiation, temperature, and wind only.

        Engineering simplification:
            0.408 converts MJ/m2/day to mm/day water equivalent. The additional
            coefficient scales radiation-equivalent water to a practical forcing
            term for the simplified Farm-ARE model.
        """
        p = self.params
        temp_factor = (weather.air_temp_mean_c + 5.0) / 25.0
        temp_factor = self._clip(temp_factor, p.min_temp_factor, p.max_temp_factor)
        wind_factor = 1.0 + 0.03 * max(0.0, weather.wind_ms - 3.0)
        et0 = (
            0.408
            * max(0.0, weather.solar_rad_mj_m2)
            * p.radiation_et_coeff
            * temp_factor
            * wind_factor
        )
        return max(0.0, et0)

    def _water_stress_factor(self, root_vwc: float) -> float:
        """
        Root-zone water-stress response.

        Returns 1.0 when root-zone water is above the stress threshold and
        decreases linearly to 0.0 at wilting point.
        """
        p = self.params
        if root_vwc <= p.wilting_point_vwc:
            return 0.0
        if root_vwc >= p.water_stress_vwc:
            return 1.0
        return (root_vwc - p.wilting_point_vwc) / (p.water_stress_vwc - p.wilting_point_vwc)

    def _planting_ready(self, state: RidgeSoilState) -> tuple[bool, list[str]]:
        """
        Planting readiness from seed-zone soil temperature and top-layer VWC.

        This is an operational rule, not a biological germination model.
        """
        p = self.params
        tags: list[str] = []

        if state.top_temp_c < p.planting_temp_min_c:
            tags.append("planting_blocked_cold_soil")
        if state.top_vwc < p.planting_vwc_too_dry:
            tags.append("planting_blocked_too_dry")
        elif state.top_vwc < p.planting_vwc_min:
            tags.append("planting_marginal_dry")
        if state.top_vwc > p.planting_vwc_too_wet:
            tags.append("planting_blocked_too_wet")
        elif state.top_vwc > p.planting_vwc_max:
            tags.append("planting_marginal_wet")

        ready = (
            state.top_temp_c >= p.planting_temp_min_c
            and p.planting_vwc_min <= state.top_vwc <= p.planting_vwc_max
        )
        return ready, tags

    def _trafficability(self, state: RidgeSoilState, weather: WeatherInput) -> str:
        """
        Operational trafficability category.

        This is a simple decision-support state used to block or discourage
        tractor/ground-robot operations after heavy rain or under wet topsoil.
        """
        p = self.params
        if state.top_vwc > p.planting_vwc_too_wet or weather.rain_mm >= 15.0:
            return "blocked"
        if state.top_vwc > 0.32 or weather.rain_mm >= 5.0:
            return "limited"
        return "good"

    def _top_root_redistribution_mm(
        self,
        *,
        top_storage: float,
        root_storage: float,
        top_depth_mm: float,
        root_depth_mm: float,
    ) -> float:
        p = self.params
        rate = max(0.0, min(1.0, float(p.top_root_redistribution_rate)))
        if rate <= 0.0:
            return 0.0

        top_theta = top_storage / top_depth_mm
        root_theta = root_storage / root_depth_mm
        gradient = top_theta - root_theta
        if gradient <= max(0.0, float(p.top_root_redistribution_deadband_vwc)):
            return 0.0

        top_min = p.wilting_point_vwc * top_depth_mm
        source_available = max(0.0, top_storage - top_min)
        root_fc = p.field_capacity_vwc * root_depth_mm
        root_room_to_fc = max(0.0, root_fc - root_storage)
        gradient_limited = gradient * top_depth_mm * rate
        return min(source_available, root_room_to_fc, gradient_limited)

    @staticmethod
    def _clip(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))


if __name__ == "__main__":
    from datetime import timedelta

    engine = SoilEngine(num_ridges=4)

    start = date(2026, 5, 1)
    weather_series = [
        WeatherInput(
            day=start + timedelta(days=i),
            air_temp_mean_c=12.0 + 0.8 * i,
            air_temp_min_c=6.0 + 0.5 * i,
            air_temp_max_c=18.0 + 0.8 * i,
            rain_mm=20.0 if i == 2 else 0.0,
            solar_rad_mj_m2=16.0,
            wind_ms=3.5,
        )
        for i in range(7)
    ]

    for w in weather_series:
        irrigation = {0: 8.0, 1: 8.0} if w.day == start + timedelta(days=5) else {}
        results = engine.update_day(w, irrigation_mm_by_ridge=irrigation)
        print(w.day, results[0])
