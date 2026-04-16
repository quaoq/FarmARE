"""
FarmWorldApp — central ridge-state store.

All other apps write ridge data via @env_tool methods.
The agent reads via @app_tool methods.

Farm layout: 268 m × 71 m, 64 ridges (ID 0-63), ridge width 1.1 m. [PDF-p1]
"""
from __future__ import annotations

import logging
from typing import Any

from are.simulation.apps.app import App
from are.simulation.apps.farm_world.models import (
    GrowthStage,
    InventoryState,
    RidgeState,
    SeasonPhase,
)
from are.simulation.tool_utils import OperationType, app_tool, data_tool, env_tool
from are.simulation.types import EventType, event_registered
from are.simulation.utils.type_utils import type_check

logger = logging.getLogger(__name__)

# Number of ridges in the farm [PDF-p1]
DEFAULT_NUM_RIDGES = 64

# Farm physical dimensions [PDF-p1]
FIELD_LENGTH_M = 268.0   # ridge length (y-axis)
FIELD_WIDTH_M  = 71.0    # total field width (x-axis)
DEFAULT_RIDGE_WIDTH_M  = 1.1     # single ridge width
ROWS_PER_RIDGE = 2       # two soybean rows per ridge [PDF-p6]
ROW_SPACING_M  = 0.4     # spacing between the two planted rows within a ridge [PDF-p1]

# Grain yield per ridge (kg) — midpoint of 200-290 kg range [PDF-p11]
GRAIN_KG_PER_RIDGE = 245.0

# Pesticide consumed per ridge per spray pass (L) [设计, based on 300-800L / 5-10 ridges]
PESTICIDE_L_PER_RIDGE = 8.0

# Growth stage progression: days_since_planted thresholds [设计, based on PDF-p6 stage descriptions]
# Maps (seed_type, days) → growth_stage
_STAGE_THRESHOLDS: list[tuple[int, str]] = [
    (0,   GrowthStage.VE.value),
    (10,  GrowthStage.V1.value),
    (20,  GrowthStage.V2.value),
    (30,  GrowthStage.V3.value),
    (40,  GrowthStage.V4.value),
    (50,  GrowthStage.V5.value),
    (60,  GrowthStage.V6.value),
    (70,  GrowthStage.R1.value),
    (80,  GrowthStage.R2.value),
    (90,  GrowthStage.R3.value),
    (100, GrowthStage.R4.value),
    (105, GrowthStage.R5.value),
    (110, GrowthStage.R6.value),
    (115, GrowthStage.R7.value),
    (120, GrowthStage.R8.value),
]


def _stage_for_days(days: int) -> str:
    """Return the growth stage string for a given days_since_planted value."""
    stage = GrowthStage.VE.value
    for threshold, s in _STAGE_THRESHOLDS:
        if days >= threshold:
            stage = s
    return stage


def _grain_moisture_for_stage(stage: str, days_in_r6_plus: int) -> float:
    """
    Estimate grain moisture % based on growth stage.
    Grain moisture starts ~35% at R6 and drops ~1.5%/day toward 13-15%. [PDF-p10]
    """
    pre_r6 = {
        GrowthStage.BARE.value, GrowthStage.VE.value,
        GrowthStage.V1.value, GrowthStage.V2.value, GrowthStage.V3.value,
        GrowthStage.V4.value, GrowthStage.V5.value, GrowthStage.V6.value,
        GrowthStage.R1.value, GrowthStage.R2.value, GrowthStage.R3.value,
        GrowthStage.R4.value, GrowthStage.R5.value,
    }
    if stage in pre_r6:
        return 0.0
    # R6 onward: start at 35%, drop ~1.5%/day, floor at 13%
    moisture = max(13.0, 35.0 - days_in_r6_plus * 1.5)
    return round(moisture, 2)


def plants_per_ridge_from_spacing(seed_spacing_cm: float) -> int:
    """
    Convert in-row seed spacing to the realized plant count for one ridge.

    Each ridge contains two soybean rows across the fixed 268 m ridge length. [PDF-p6]
    """
    return int(round(FIELD_LENGTH_M * ROWS_PER_RIDGE * 100.0 / float(seed_spacing_cm)))


class FarmWorldApp(App):
    """
    Central ridge-state store for the Farm-World simulation.

    Maintains the state of all 64 ridges and the farm inventory.
    Other device apps write ridge data via env_tool calls.
    The agent reads via app_tool calls.
    """

    def __init__(self) -> None:
        super().__init__(name="FarmWorldApp")
        self.ridge_width_m = DEFAULT_RIDGE_WIDTH_M
        self.num_ridges = DEFAULT_NUM_RIDGES
        self._ridges: list[RidgeState] = [RidgeState.default(i) for i in range(self.num_ridges)]
        self._inventory: InventoryState = InventoryState.default()
        self._sim_date: str = "2026-04-25"
        self._season_phase: str = SeasonPhase.PREP.value
        # Track days in R6+ per ridge for grain moisture calculation [设计]
        self._days_in_r6_plus: list[int] = [0] * self.num_ridges

    # ------------------------------------------------------------------
    # App interface
    # ------------------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        return {
            "app_name": self.name,
            "sim_date": self._sim_date,
            "season_phase": self._season_phase,
            "ridges": [r.to_dict() for r in self._ridges],
            "inventory": self._inventory.to_dict(),
            "_days_in_r6_plus": list(self._days_in_r6_plus),
        }

    def load_state(self, state_dict: dict[str, Any]) -> None:
        self._sim_date = state_dict["sim_date"]
        self._season_phase = state_dict["season_phase"]
        self._ridges = [RidgeState.from_dict(d) for d in state_dict["ridges"]]
        self._inventory = InventoryState.from_dict(state_dict["inventory"])
        self._days_in_r6_plus = state_dict.get(
            "_days_in_r6_plus", [0] * self.num_ridges
        )

    def reset(self) -> None:
        super().reset()
        self._ridges = [RidgeState.default(i) for i in range(self.num_ridges)]
        self._inventory = InventoryState.default()
        self._sim_date = "2026-04-25"
        self._season_phase = SeasonPhase.PREP.value
        self._days_in_r6_plus = [0] * self.num_ridges

    # ------------------------------------------------------------------
    # Agent tools (@app_tool) — read-only
    # ------------------------------------------------------------------

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_farm_overview(self) -> dict[str, Any]:
        """
        Return a concise farm summary: current date, season phase, inventory,
        and a per-ridge overview (key fields only, not full detail).

        season_phase indicates the current stage of the agricultural calendar:
          "prep"     — pre-planting field preparation
          "planting" — active sowing window
          "growing"  — crop vegetative and reproductive growth
          "harvest"  — grain maturity and harvesting

        Use get_ridge_state() or get_ridge_range_state() for full ridge detail.
        """
        overview = []
        for r in self._ridges:
            overview.append({
                "ridge_id": r.ridge_id,
                "planted": r.planted,
                "growth_stage": r.growth_stage,
                "grain_moisture_pct": round(r.grain_moisture_pct, 1),
            })
        return {
            "sim_date": self._sim_date,
            "season_phase": self._season_phase,
            "inventory": self._inventory.to_dict(),
            "ridges_overview": overview,
        }

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_ridge_state(self, ridge_id: int) -> dict[str, Any]:
        """
        Return the full state of a single ridge.

        Args:
            ridge_id: Ridge identifier, 0-63.
        """
        if not 0 <= ridge_id < self.num_ridges:
            return {"error": f"ridge_id must be 0-{self.num_ridges - 1}, got {ridge_id}"}
        return self._agent_ridge_dict(self._ridges[ridge_id])

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_ridge_range_state(self, start: int, end: int) -> dict[str, Any]:
        """
        Return the full state of ridges from start to end (inclusive).

        Args:
            start: First ridge ID
            end:   Last ridge ID , must be >= start.

        Use this instead of repeated get_ridge_state() calls when you need
        to inspect a contiguous block of ridges at once.
        """
        if not 0 <= start <= end < self.num_ridges:
            return {"error": f"Invalid range [{start}, {end}]. Must be within 0-{self.num_ridges - 1}."}
        return {
            "ridges": [self._agent_ridge_dict(self._ridges[i]) for i in range(start, end + 1)]
        }

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_inventory(self) -> dict[str, Any]:
        """Return the current farm inventory (seeds, pesticide, fertilizer, fuel, grain)."""
        return self._inventory.to_dict()

    # ------------------------------------------------------------------
    # Environment tools (@env_tool) — called by scenario events / device apps
    # ------------------------------------------------------------------

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def advance_day(self, weather: dict[str, Any]) -> dict[str, Any]:
        """
        Advance simulation by one day. Updates all ridges:
        - soil VWC (evapotranspiration + rainfall + pending irrigation) [PDF-p6]
        - growth stage (days_since_planted + 1) [PDF-p6]
        - grain moisture % (R6+ stage) [PDF-p10]
        - pesticide_applied_days_ago counter [PDF-p9]

        Args:
            weather: dict with keys: date, temp_c, rainfall_mm, solar_radiation.
        """
        rainfall_mm = float(weather.get("rainfall_mm", 0.0))
        temp_c = float(weather.get("temp_c", 15.0))
        self._sim_date = weather.get("date", self._sim_date)

        # Evapotranspiration rate: simplified model [设计]
        et_rate = 0.002 if rainfall_mm > 0 else 0.005

        for i, r in enumerate(self._ridges):
            # --- VWC update ---
            new_vwc = r.soil_vwc - et_rate + rainfall_mm / 100.0
            if r.irrigation_pending:
                new_vwc += 0.08  # irrigation effect [设计]
                r.irrigation_pending = False
            r.soil_vwc = max(0.05, min(0.45, new_vwc))

            # --- Soil temperature: nudge toward air temp [设计] ---
            r.soil_temp_c = round(r.soil_temp_c * 0.8 + temp_c * 0.2, 2)

            # --- Growth stage ---
            if r.planted:
                previous_stage = r.growth_stage
                r.days_since_planted += 1
                r.growth_stage = _stage_for_days(r.days_since_planted)

                # Track days in R6+ for grain moisture [PDF-p10]
                r6_stages = {
                    GrowthStage.R6.value, GrowthStage.R7.value, GrowthStage.R8.value
                }
                if r.growth_stage in r6_stages:
                    if previous_stage in r6_stages:
                        self._days_in_r6_plus[i] += 1
                    else:
                        self._days_in_r6_plus[i] = 0
                else:
                    self._days_in_r6_plus[i] = 0
                r.grain_moisture_pct = _grain_moisture_for_stage(
                    r.growth_stage, self._days_in_r6_plus[i]
                )

            # --- Pesticide counter ---
            if r.pesticide_applied_days_ago >= 0:
                r.pesticide_applied_days_ago += 1
                # Pest pressure decays after treatment [PDF-p10]
                if r.pesticide_applied_days_ago >= 3:
                    r.pest_pressure = max(0.0, r.pest_pressure - 0.15)

        self.is_state_modified = True
        return {"status": "ok", "sim_date": self._sim_date}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def trigger_pest_outbreak(
        self, ridge_ids: list[int], severity: float
    ) -> dict[str, Any]:
        """
        Trigger a localised pest outbreak on the specified ridges. [PDF-p7]

        Args:
            ridge_ids: List of ridge IDs affected.
            severity:  Pest pressure level to set (0.0-1.0).
        """
        for rid in ridge_ids:
            if 0 <= rid < self.num_ridges:
                self._ridges[rid].pest_pressure = max(
                    self._ridges[rid].pest_pressure, float(severity)
                )
        self.is_state_modified = True
        return {"status": "ok", "affected_ridges": ridge_ids}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def trigger_rainfall(self, mm: float) -> dict[str, Any]:
        """
        Apply an immediate rainfall event, updating soil VWC for all ridges. [PDF-p6]

        Args:
            mm: Rainfall amount in millimetres.
        """
        for r in self._ridges:
            r.soil_vwc = min(0.45, r.soil_vwc + mm / 100.0)
        self.is_state_modified = True
        return {"status": "ok", "rainfall_mm": mm}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def set_ridge_planted(
        self,
        ridge_id: int,
        seed_type: str,
        seed_spacing_cm: float | None = None,
        seeds_planted: int | None = None,
    ) -> dict[str, Any]:
        """
        Mark a ridge as planted. Called by TractorApp after plant_seeds completes.

        Args:
            ridge_id:         Ridge ID (0-63).
            seed_type:        SeedType value string.
            seed_spacing_cm:  In-row spacing in centimetres.
            seeds_planted:    Realized plant count for the ridge.
        """
        if not 0 <= ridge_id < self.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        r = self._ridges[ridge_id]
        r.planted = True
        r.seed_type = seed_type
        r.seed_spacing_cm = seed_spacing_cm
        r.seeds_planted = seeds_planted or 0
        r.days_since_planted = 0
        r.growth_stage = GrowthStage.VE.value
        self._days_in_r6_plus[ridge_id] = 0
        self.is_state_modified = True
        return {
            "status": "ok",
            "ridge_id": ridge_id,
            "seed_type": seed_type,
            "seed_spacing_cm": seed_spacing_cm,
            "seeds_planted": r.seeds_planted,
        }

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def set_ridge_harvested(self, ridge_id: int) -> dict[str, Any]:
        """
        Mark a ridge as harvested. Called by TractorApp after harvest completes.
        Adds GRAIN_KG_PER_RIDGE to inventory. [PDF-p11]

        Args:
            ridge_id: Ridge ID (0-63).
        """
        if not 0 <= ridge_id < self.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        r = self._ridges[ridge_id]
        # Yield scales with yield_potential [设计]
        grain = round(GRAIN_KG_PER_RIDGE * r.yield_potential, 2)
        self._inventory.harvest_grain_kg += grain
        r.planted = False
        r.seed_type = None
        r.seed_spacing_cm = None
        r.seeds_planted = 0
        r.growth_stage = GrowthStage.BARE.value
        r.days_since_planted = 0
        r.grain_moisture_pct = 0.0
        r.ndvi = -1.0
        r.canopy_temp_c = -1.0
        self._days_in_r6_plus[ridge_id] = 0
        self.is_state_modified = True
        return {"status": "ok", "ridge_id": ridge_id, "grain_kg_added": grain}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def update_ridge_ndvi(self, ridge_id: int, ndvi: float) -> dict[str, Any]:
        """
        Update NDVI observation for a ridge. Called by DroneApp after survey. [PDF-p7]

        Args:
            ridge_id: Ridge ID (0-63).
            ndvi:     NDVI value (0.0-1.0).
        """
        if not 0 <= ridge_id < self.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        self._ridges[ridge_id].ndvi = round(float(ndvi), 4)
        self.is_state_modified = True
        return {"status": "ok"}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def update_ridge_canopy_temp(self, ridge_id: int, temp_c: float) -> dict[str, Any]:
        """
        Update canopy temperature observation for a ridge.
        Called by DroneApp after thermal inspection. [PDF-p7]

        Args:
            ridge_id: Ridge ID (0-63).
            temp_c:   Canopy temperature in °C.
        """
        if not 0 <= ridge_id < self.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        self._ridges[ridge_id].canopy_temp_c = round(float(temp_c), 2)
        self.is_state_modified = True
        return {"status": "ok"}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def update_ridge_pesticide(self, ridge_id: int) -> dict[str, Any]:
        """
        Record that pesticide was applied to a ridge today.
        Called by TractorApp / FieldOpsApp after spray completes. [PDF-p9]

        Args:
            ridge_id: Ridge ID (0-63).
        """
        if not 0 <= ridge_id < self.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        self._ridges[ridge_id].pesticide_applied_days_ago = 0
        self.is_state_modified = True
        return {"status": "ok"}


    def set_irrigation_pending(self, ridge_id: int,add_vwc:float) -> dict[str, Any]:
        """
        Mark a ridge for irrigation. Effect applied on next advance_day. [PDF-p10]

        Args:
            ridge_id: Ridge ID (0-63).
        """
        if not 0 <= ridge_id < self.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}

        self._ridges[ridge_id].soil_vwc+= add_vwc

        self.is_state_modified = True
        return {"status": "ok"}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def set_season_phase(self, phase: str) -> dict[str, Any]:
        """
        Update the high-level season phase label. [设计]

        Args:
            phase: SeasonPhase value string ("prep"/"planting"/"growing"/"harvest").
        """
        self._season_phase = phase
        self.is_state_modified = True
        return {"status": "ok", "season_phase": phase}

    # Fields hidden from agent — observable only via SensorApp / DroneApp
    _OBSERVATION_FIELDS = {
        "soil_vwc", "soil_temp_c", "ndvi", "canopy_temp_c",
        "pest_pressure", "disease_pressure",
    }

    def _agent_ridge_dict(self, ridge: RidgeState) -> dict[str, Any]:
        """Return ridge dict with observation fields stripped for agent tools."""
        return {
            k: v for k, v in ridge.to_dict().items()
            if k not in self._OBSERVATION_FIELDS
        }

    # ------------------------------------------------------------------
    # Internal helpers (used by device apps directly, not via tool system)
    # ------------------------------------------------------------------

    def get_ridge(self, ridge_id: int) -> RidgeState:
        """Direct access for device apps that hold a reference to this app."""
        return self._ridges[ridge_id]

    def set_ridges(self, ridges: list[RidgeState]) -> None:
        """Replace the ridge list (called by TractorApp.form_ridges)."""
        self._ridges = ridges
        self._days_in_r6_plus = [0] * len(ridges)
        self.is_state_modified = True

    def get_avg_vwc(self) -> float:
        """Return average soil VWC across all ridges (used by WeatherApp)."""
        return round(sum(r.soil_vwc for r in self._ridges) / self.num_ridges, 4)

    def consume_seeds(self, seed_type: str, count: int) -> bool:
        """Deduct seeds from warehouse. Returns False if insufficient."""
        current = self._inventory.seed_stock.get(seed_type, 0)
        if current < count:
            return False
        self._inventory.seed_stock[seed_type] = current - count
        self.is_state_modified = True
        return True

    def consume_pesticide(self, liters: float) -> bool:
        """Deduct pesticide from warehouse. Returns False if insufficient."""
        if self._inventory.pesticide_liters < liters:
            return False
        self._inventory.pesticide_liters = round(self._inventory.pesticide_liters - liters, 2)
        self.is_state_modified = True
        return True

    def consume_fertilizer(self, kg: float) -> bool:
        """Deduct fertilizer from warehouse. Returns False if insufficient."""
        if self._inventory.fertilizer_kg < kg:
            return False
        self._inventory.fertilizer_kg = round(self._inventory.fertilizer_kg - kg, 2)
        self.is_state_modified = True
        return True

    def consume_fuel(self, liters: float) -> bool:
        """Deduct fuel from warehouse. Returns False if insufficient."""
        if self._inventory.fuel_liters < liters:
            return False
        self._inventory.fuel_liters = round(self._inventory.fuel_liters - liters, 2)
        self.is_state_modified = True
        return True
