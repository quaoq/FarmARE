"""
TractorApp - field preparation, planting, spraying, and harvest operations.

All operations are synchronous: they complete immediately, advance the
simulation clock by the real-world duration of the task, and consume
the appropriate resources (fuel, seeds, pesticide, fertilizer).
"""
from __future__ import annotations

import uuid
from typing import Any

from are.simulation.apps.app import App
from are.simulation.apps.farm_world.farm_world_app import (
    FIELD_LENGTH_M,
    FIELD_WIDTH_M,
    RIDGE_WIDTH_M,
    NUM_RIDGES,
    PESTICIDE_L_PER_RIDGE,
    PLANTS_PER_RIDGE,
    FarmWorldApp,
)
from are.simulation.apps.farm_world.models import (
    GrowthStage,
    SeedType,
)
from are.simulation.apps.farm_world.weather_app import WeatherApp
from are.simulation.tool_utils import OperationType, app_tool, data_tool
from are.simulation.types import event_registered
from are.simulation.utils.type_utils import type_check

# Tractor working speeds (m/s) by operation type
_SPEED_TILL_MS        = 3000 / 3600   # rotary tilling: 3 km/h
_SPEED_FERTILIZE_MS   = 6000 / 3600   # broadcast spreader: 6 km/h
_SPEED_RIDGE_MS       = 4000 / 3600   # ridge former: 4 km/h
_SPEED_PLANT_MS       = 5000 / 3600   # planter: 5 km/h
_SPEED_SPRAY_MS       = 6000 / 3600   # spray boom: 6 km/h
_SPEED_HARVEST_MS     = 4000 / 3600   # combine: 4 km/h

# Working widths (m) — determines number of passes needed for full-field ops
_WIDTH_TILL_M         = 2.2    # rotary tiller
_WIDTH_FERTILIZE_M    = 6.0    # broadcast spreader
_WIDTH_RIDGE_M        = 2 * RIDGE_WIDTH_M   # 2-ridge pass

# Headland turn time per pass (s)
_HEADLAND_TURN_S      = 30

_FUEL_PER_PREP_OP   = 8.0   # % per full-field prep operation
_FUEL_PER_PASS      = 2.0   # % per 4-ridge or 10-ridge pass

_FIELD_PREP_SEQUENCE = ["level", "base_fertilize", "form_ridges"]


def _full_field_duration(working_width_m: float, speed_ms: float) -> int:
    """Duration in seconds for a full-field operation."""
    passes = FIELD_WIDTH_M / working_width_m
    dist_per_pass = FIELD_LENGTH_M + _HEADLAND_TURN_S * speed_ms  # effective distance
    total_dist = passes * FIELD_LENGTH_M
    turn_time = passes * _HEADLAND_TURN_S
    return int(total_dist / speed_ms + turn_time)


def _pass_duration(speed_ms: float) -> int:
    """Duration in seconds for a single ridge-length pass plus headland turn."""
    return int(FIELD_LENGTH_M / speed_ms) + _HEADLAND_TURN_S


class TractorApp(App):
    """Tractor operations: field preparation, planting, spraying, and harvest."""

    def __init__(self, farm_world_app: FarmWorldApp, weather_app: WeatherApp) -> None:
        super().__init__(name="TractorApp")
        self._farm_world_app = farm_world_app
        self._weather_app = weather_app
        self._operation_log: list[dict[str, Any]] = []
        self._completed_prep_ops: list[str] = []

    def get_state(self) -> dict[str, Any]:
        inventory = self._farm_world_app.get_inventory()
        return {
            "app_name": self.name,
            "fuel_pct": inventory["tractor_fuel_pct"],
            "pesticide_tank_liters": inventory["pesticide_liters"],
            "completed_prep_ops": list(self._completed_prep_ops),
            "operation_log": list(self._operation_log),
        }

    def load_state(self, state_dict: dict[str, Any]) -> None:
        self._operation_log = [dict(item) for item in state_dict.get("operation_log", [])]
        self._completed_prep_ops = list(state_dict.get("completed_prep_ops", []))

    def reset(self) -> None:
        super().reset()
        self._operation_log = []
        self._completed_prep_ops = []

    # ------------------------------------------------------------------
    # Agent tools
    # ------------------------------------------------------------------

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def prepare_field(self, operation: str) -> dict[str, Any]:
        """
        Execute one full-field preparation step.

        Three steps must be completed in order before planting can begin:
          "level"           — rotary till and level the soil surface
          "base_fertilize"  — apply base fertilizer across all ridges
          "form_ridges"     — form the 64 ridge rows

        Args:
            operation: One of "level", "base_fertilize", "form_ridges".
        """
        if operation not in _FIELD_PREP_SEQUENCE:
            return {"error": f"Unknown operation '{operation}'. Valid: {_FIELD_PREP_SEQUENCE}"}
        if self._farm_world_app.get_avg_vwc() > 0.35:
            return {"error": "Soil too wet for tractor field preparation (avg VWC > 0.35)"}
        expected = _FIELD_PREP_SEQUENCE[len(self._completed_prep_ops)] if len(self._completed_prep_ops) < len(_FIELD_PREP_SEQUENCE) else None
        if operation != expected:
            return {"error": f"Preparation order violation: expected '{expected}', got '{operation}'"}

        if operation == "base_fertilize":
            if not self._farm_world_app.consume_fertilizer(200.0):
                return {"error": "Insufficient fertilizer stock"}
        if not self._farm_world_app.consume_fuel(_FUEL_PER_PREP_OP):
            return {"error": "Insufficient fuel"}

        duration = {
            "level":          _full_field_duration(_WIDTH_TILL_M,       _SPEED_TILL_MS),
            "base_fertilize": _full_field_duration(_WIDTH_FERTILIZE_M,  _SPEED_FERTILIZE_MS),
            "form_ridges":    _full_field_duration(_WIDTH_RIDGE_M,       _SPEED_RIDGE_MS),
        }[operation]
        self.time_manager.add_offset(duration)
        self._completed_prep_ops.append(operation)

        op_id = str(uuid.uuid4())[:8]
        self._operation_log.append({
            "op_id": op_id,
            "operation": operation,
            "duration_s": duration,
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "operation": operation,
            "duration_minutes": round(duration / 60, 1),
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def plant_seeds(
        self, start_ridge: int, end_ridge: int, seed_type: str, depth_cm: float
    ) -> dict[str, Any]:
        """
        Sow seeds across a contiguous block of ridges (up to 4 per pass).

        Seed types:
          STANDARD        — standard soybean variety
          EARLY_COLD      — cold-tolerant variety
          HIGH_DENSITY    — compact high-density variety
          STRESS_TOLERANT — tolerates adverse conditions

        Args:
            start_ridge: First ridge to plant (0-63).
            end_ridge:   Last ridge to plant (0-63, max 4-ridge span).
            seed_type:   One of STANDARD, EARLY_COLD, HIGH_DENSITY, STRESS_TOLERANT.
            depth_cm:    Sowing depth in centimetres.
        """
        err = self._validate_ridge_window(start_ridge, end_ridge, max_width=4)
        if err:
            return {"error": err}
        if not 3.0 <= float(depth_cm) <= 5.0:
            return {"error": "depth_cm must be within 3–5 cm"}
        if seed_type not in {m.value for m in SeedType}:
            return {"error": f"Unknown seed_type '{seed_type}'"}

        ridges = [self._farm_world_app.get_ridge(r) for r in range(start_ridge, end_ridge + 1)]
        if any(r.planted for r in ridges):
            return {"error": "One or more ridges are already planted"}

        avg_vwc  = sum(r.soil_vwc   for r in ridges) / len(ridges)
        avg_temp = sum(r.soil_temp_c for r in ridges) / len(ridges)
        if not 0.20 <= avg_vwc <= 0.30:
            return {"error": f"Soil VWC {avg_vwc:.3f} must be within 0.20–0.30 for planting"}
        if seed_type == SeedType.EARLY_COLD.value:
            if avg_temp < 8.0:
                return {"error": f"Soil temperature {avg_temp:.1f}°C too low for EARLY_COLD (min 8°C)"}
        elif avg_temp <= 10.0:
            return {"error": f"Soil temperature {avg_temp:.1f}°C must exceed 10°C for planting"}

        seed_count = len(ridges) * PLANTS_PER_RIDGE

        # Execute
        if not self._farm_world_app.consume_seeds(seed_type, seed_count):
            return {"error": "Insufficient seed stock"}
        if not self._farm_world_app.consume_fuel(_FUEL_PER_PASS):
            return {"error": "Insufficient fuel"}
        duration = _pass_duration(_SPEED_PLANT_MS)
        self.time_manager.add_offset(duration)
        for r in ridges:
            self._farm_world_app.set_ridge_planted(r.ridge_id, seed_type)

        op_id = str(uuid.uuid4())[:8]
        self._operation_log.append({
            "op_id": op_id,
            "operation": "plant_seeds",
            "ridge_ids": list(range(start_ridge, end_ridge + 1)),
            "seed_type": seed_type,
            "depth_cm": float(depth_cm),
            "duration_s": duration,
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "planted_ridges": list(range(start_ridge, end_ridge + 1)),
            "seed_type": seed_type,
            "depth_cm": float(depth_cm),
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def apply_pesticide(self, start_ridge: int, end_ridge: int) -> dict[str, Any]:
        """
        Apply pesticide across a contiguous block of ridges using the tractor
        spray boom (up to 10 ridges per pass).

        Args:
            start_ridge: First ridge to spray (0-63).
            end_ridge:   Last ridge to spray (0-63, max 10-ridge span).
        """
        err = self._validate_ridge_window(start_ridge, end_ridge, max_width=10)
        if err:
            return {"error": err}
        if not self._weather_app.is_sprayable:
            return {"error": "Weather conditions do not allow spraying (rain or wind >= 5 m/s)"}
        if not self._weather_app.is_trafficable:
            return {"error": "Soil too wet for tractor spraying (avg VWC > 0.35)"}

        ridge_count = end_ridge - start_ridge + 1
        required_liters = ridge_count * PESTICIDE_L_PER_RIDGE

        # Execute
        if not self._farm_world_app.consume_pesticide(required_liters):
            return {"error": f"Insufficient pesticide: need {required_liters:.1f} L"}
        if not self._farm_world_app.consume_fuel(_FUEL_PER_PASS):
            return {"error": "Insufficient fuel"}
        duration = _pass_duration(_SPEED_SPRAY_MS)
        self.time_manager.add_offset(duration)
        for ridge_id in range(start_ridge, end_ridge + 1):
            self._farm_world_app.update_ridge_pesticide(ridge_id)

        op_id = str(uuid.uuid4())[:8]
        self._operation_log.append({
            "op_id": op_id,
            "operation": "apply_pesticide",
            "ridge_ids": list(range(start_ridge, end_ridge + 1)),
            "pesticide_used_liters": required_liters,
            "duration_s": duration,
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "sprayed_ridges": list(range(start_ridge, end_ridge + 1)),
            "pesticide_used_liters": required_liters,
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def harvest(self, start_ridge: int, end_ridge: int) -> dict[str, Any]:
        """
        Harvest a contiguous block of ridges (up to 4 per pass).

        Args:
            start_ridge: First ridge to harvest (0-63).
            end_ridge:   Last ridge to harvest (0-63, max 4-ridge span).
        """
        err = self._validate_ridge_window(start_ridge, end_ridge, max_width=4)
        if err:
            return {"error": err}
        if self._weather_app.rainfall_mm > 0.0:
            return {"error": "Cannot harvest in rainy conditions"}
        if not self._weather_app.is_trafficable:
            return {"error": "Soil too wet for harvest (avg VWC > 0.35)"}

        ridges = [self._farm_world_app.get_ridge(r) for r in range(start_ridge, end_ridge + 1)]
        if any(not r.planted for r in ridges):
            return {"error": "All ridges must be planted before harvest"}
        if any(r.growth_stage in {GrowthStage.BARE.value, GrowthStage.VE.value} for r in ridges):
            return {"error": "Ridges are not mature enough for harvest"}
        bad_moisture = [r.ridge_id for r in ridges if not 13.0 <= r.grain_moisture_pct <= 18.0]
        if bad_moisture:
            return {"error": f"Grain moisture out of 13–18% window on ridges {bad_moisture}"}

        # Execute
        if not self._farm_world_app.consume_fuel(_FUEL_PER_PASS):
            return {"error": "Insufficient fuel"}
        duration = _pass_duration(_SPEED_HARVEST_MS)
        self.time_manager.add_offset(duration)
        before = self._farm_world_app.get_inventory()["harvest_grain_kg"]
        for r in ridges:
            self._farm_world_app.set_ridge_harvested(r.ridge_id)
        after = self._farm_world_app.get_inventory()["harvest_grain_kg"]
        grain_added = round(after - before, 2)

        op_id = str(uuid.uuid4())[:8]
        self._operation_log.append({
            "op_id": op_id,
            "operation": "harvest",
            "ridge_ids": list(range(start_ridge, end_ridge + 1)),
            "grain_kg_added": grain_added,
            "duration_s": duration,
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "harvested_ridges": list(range(start_ridge, end_ridge + 1)),
            "grain_kg_added": grain_added,
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def refuel(self) -> dict[str, Any]:
        """
        Refuel the tractor to 100%. Drive to the fuel station at the field edge.
        """
        self._farm_world_app.refuel()
        self.is_state_modified = True
        return {"status": "ok", "fuel_pct": 100.0}

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def refill_pesticide_tank(self, liters: float) -> dict[str, Any]:
        """
        Add pesticide to the tractor tank from the farm storage. The tank
        holds up to 800 L; excess is silently capped.

        Args:
            liters: Amount to add (must be positive).
        """
        if float(liters) <= 0:
            return {"error": "liters must be positive"}
        self._farm_world_app.refill_pesticide(float(liters))
        self.is_state_modified = True
        return {
            "status": "ok",
            "pesticide_tank_liters": self._farm_world_app.get_inventory()["pesticide_liters"],
        }

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def check_tractor_status(self) -> dict[str, Any]:
        """
        Return current fuel level and pesticide tank level.
        """
        inventory = self._farm_world_app.get_inventory()
        return {
            "fuel_pct": inventory["tractor_fuel_pct"],
            "pesticide_tank_liters": inventory["pesticide_liters"],
            "completed_prep_ops": list(self._completed_prep_ops),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_ridge_window(
        self, start_ridge: int, end_ridge: int, max_width: int
    ) -> str | None:
        if not 0 <= start_ridge <= end_ridge < NUM_RIDGES:
            return f"Invalid ridge range [{start_ridge}, {end_ridge}]"
        if end_ridge - start_ridge + 1 > max_width:
            return f"Range cannot exceed {max_width} ridges per pass"
        return None
