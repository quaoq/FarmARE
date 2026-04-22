"""
TractorApp - field preparation, planting, spraying, fertilizing, and harvest.

All operations are synchronous: they complete immediately, advance the
simulation clock by the real-world duration of the task, and consume
resources from the tractor's own onboard tanks/hoppers.

Onboard capacity:
  Fuel tank:           100 L (full = 100%)
  Pesticide tank:      800 L max
  Fertilizer spreader: 500 kg max
  Seed hopper:          max 300000 plants

Resources must be loaded from the farm warehouse before use:
  load_seeds(), load_fertilizer(), refill_pesticide_tank(), refuel()
"""
from __future__ import annotations

import uuid
from typing import Any

from are.simulation.apps.app import App
from are.simulation.apps.farm_world.farm_world_app import (
    FIELD_LENGTH_M,
    FIELD_WIDTH_M,
    GRAIN_KG_PER_RIDGE,
    PESTICIDE_L_PER_RIDGE,
    FarmWorldApp,
    plants_per_ridge_from_spacing,
)
from are.simulation.apps.farm_world.models import (
    GrowthStage,
    RidgeState,
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

# Working widths (m) for full-field ops
_WIDTH_FERTILIZE_M    = 6.0           # broadcast spreader

# Levelling attachment working widths (m) [PDF-p4]
_ATTACH_WIDTH_M = {
    "grader":    3.0,   # 平地机
    "furrower":   1.1,   # 开沟机
    "harvester":  4.4,   # 收割机
    "sprayer":   11.0,   # 喷药机
}
ATTACHMENTS = list(_ATTACH_WIDTH_M.keys())

# Headland turn time per pass (s)
_HEADLAND_TURN_S      = 30

# Fuel consumption (L) per full-field prep operation
_FUEL_PER_PREP_OP   = 8.0
# Fuel consumption (L) per single ridge-pass (planting / spraying / fertilizing)
_FUEL_PER_PASS      = 2.0

# Fertilizer consumed for base_fertilize (kg) [PDF-p4]
_BASE_FERTILIZE_KG  = 200.0

# Onboard tank/hopper capacities
_FUEL_TANK_MAX_L            = 100.0
_PESTICIDE_TANK_MAX_L       = 800.0
_FERTILIZER_SPREADER_MAX_KG = 500.0
_SEED_HOPPER_MAX_PLANTS     = 300000
# Combine harvester grain bin capacity (kg). [PDF-p11, 1.2–1.5 t range]
# Holds roughly 2 four-ridge passes before it must be unloaded.
_GRAIN_BIN_MAX_KG           = 2000.0
# Time to transfer full grain bin to trailer/warehouse (s). [PDF-p11, 5–10 min]
_GRAIN_UNLOAD_DURATION_S    = 480

# Prep steps that must be completed before planting
_FIELD_PREP_SEQUENCE = ["level", "base_fertilize", "form_ridges"]


def _full_field_duration(working_width_m: float, speed_ms: float) -> int:
    """Duration in seconds for a full-field operation."""
    passes = FIELD_WIDTH_M / working_width_m
    total_dist = passes * FIELD_LENGTH_M
    turn_time = passes * _HEADLAND_TURN_S
    return int(total_dist / speed_ms + turn_time)


def _pass_duration(speed_ms: float) -> int:
    """Duration in seconds for a single ridge-length pass plus headland turn."""
    return int(FIELD_LENGTH_M / speed_ms) + _HEADLAND_TURN_S
class TractorApp(App):
    """Tractor operations: field preparation, planting, spraying, fertilizing, and harvest."""

    def __init__(self, farm_world_app: FarmWorldApp, weather_app: WeatherApp, name: str | None = None) -> None:
        super().__init__(name=name)
        self.seed_type = None
        self._farm_world_app = farm_world_app
        self._weather_app = weather_app
        # Onboard device state
        self._fuel_tank_l: float = _FUEL_TANK_MAX_L
        self._pesticide_tank_l: float = 0.0
        self._fertilizer_spreader_kg: float = 0.0
        self._seed_hopper: int = 0
        self._grain_bin_kg: float = 0.0
        self._operation_log: list[dict[str, Any]] = []
        self._completed_prep_ops: list[str] = []
        self._attached_implement: str | None = None  # currently mounted attachment

    def get_state(self) -> dict[str, Any]:
        return {
            "app_name": self.name,
            "fuel_tank_l": round(self._fuel_tank_l, 1),
            "pesticide_tank_l": round(self._pesticide_tank_l, 1),
            "fertilizer_spreader_kg": round(self._fertilizer_spreader_kg, 1),
            "seed_hopper": self._seed_hopper,
            "grain_bin_kg": round(self._grain_bin_kg, 1),
            "grain_bin_max_kg": _GRAIN_BIN_MAX_KG,
            "available_implements": ATTACHMENTS,
            "attached_implement": self._attached_implement,
            "operation_log": list(self._operation_log),
        }

    def load_state(self, state_dict: dict[str, Any]) -> None:
        self._fuel_tank_l = state_dict.get("fuel_tank_l", _FUEL_TANK_MAX_L)
        self._pesticide_tank_l = state_dict.get("pesticide_tank_l", 0.0)
        self._fertilizer_spreader_kg = state_dict.get("fertilizer_spreader_kg", 0.0)
        self._seed_hopper = state_dict.get("seed_hopper", 0)
        self._grain_bin_kg = state_dict.get("grain_bin_kg", 0.0)
        self._operation_log = [dict(item) for item in state_dict.get("operation_log", [])]
        self._completed_prep_ops = list(state_dict.get("completed_prep_ops", []))
        self._attached_implement = state_dict.get("attached_implement", None)

    def reset(self) -> None:
        super().reset()
        self._fuel_tank_l = _FUEL_TANK_MAX_L
        self._pesticide_tank_l = 0.0
        self._fertilizer_spreader_kg = 0.0
        self._seed_hopper = 0
        self._grain_bin_kg = 0.0
        self._operation_log = []
        self._completed_prep_ops = []
        self._attached_implement = None

    # ------------------------------------------------------------------
    # Loading tools — transfer from warehouse to onboard tanks/hoppers
    # ------------------------------------------------------------------

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def attach_implement(self, implement: str) -> dict[str, Any]:
        """
        Mount an implement onto the tractor.

        Args:
            implement: Name of the implement to attach.
        """
        if implement not in _ATTACH_WIDTH_M:
            return {"error": f"Unknown implement '{implement}'"}
        if self._attached_implement is not None and self._attached_implement != implement:
            return {"error": f"Already have '{self._attached_implement}' attached."}
        self._attached_implement = implement
        self.is_state_modified = True
        return {"status": "ok", "attached_implement": implement}

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def detach_implement(self) -> dict[str, Any]:
        """
        Remove the currently mounted implement from the tractor.
        """
        if self._attached_implement is None:
            return {"error": "No implement is currently attached"}
        removed = self._attached_implement
        self._attached_implement = None
        self.is_state_modified = True
        return {"status": "ok", "detached": removed}

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def refuel(self, liters: float) -> dict[str, Any]:
        """
        Transfer fuel from the farm warehouse to the tractor fuel tank.

        Args:
            liters: Amount to transfer .
        """
        liters = float(liters)
        if liters <= 0:
            return {"error": "liters must be positive"}
        space = round(_FUEL_TANK_MAX_L - self._fuel_tank_l, 2)
        if space <= 0:
            return {"msg": "Fuel tank is already full"}
        to_transfer = min(liters, space)
        if not self._farm_world_app.consume_fuel(to_transfer):
            return {"error": f"Insufficient fuel in warehouse"}
        self._fuel_tank_l = round(self._fuel_tank_l + to_transfer, 2)
        self.is_state_modified = True
        return {"status": "ok", "fuel_tank_l": round(self._fuel_tank_l, 1)}

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def refill_pesticide_tank(self, liters: float) -> dict[str, Any]:
        """
        Transfer pesticide from the farm warehouse to the tractor spray tank.

        Args:
            liters: Amount to transfer
        """
        liters = float(liters)
        if liters <= 0:
            return {"error": "liters must be positive"}
        space = round(_PESTICIDE_TANK_MAX_L - self._pesticide_tank_l, 2)
        if space <= 0:
            return {"msg": "Pesticide tank is already full"}
        to_transfer = min(liters, space)
        if not self._farm_world_app.consume_pesticide(to_transfer):
            return {"error": f"Insufficient pesticide in warehouse"}
        self._pesticide_tank_l = round(self._pesticide_tank_l + to_transfer, 2)
        self.is_state_modified = True
        return {"status": "ok", "pesticide_tank_l": round(self._pesticide_tank_l, 1)}

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def load_fertilizer(self, kg: float) -> dict[str, Any]:
        """
        Transfer fertilizer from the farm warehouse to the tractor spreader.
        Args:
            kg: Amount to transfer.
        """
        kg = float(kg)
        if kg <= 0:
            return {"error": "kg must be positive"}
        space = round(_FERTILIZER_SPREADER_MAX_KG - self._fertilizer_spreader_kg, 2)
        if space <= 0:
            return {"msg": "Fertilizer spreader is already full"}
        to_transfer = min(kg, space)
        if not self._farm_world_app.consume_fertilizer(to_transfer):
            return {"error": f"Insufficient fertilizer in warehouse"}
        self._fertilizer_spreader_kg = round(self._fertilizer_spreader_kg + to_transfer, 2)
        self.is_state_modified = True
        return {"status": "ok", "fertilizer_spreader_kg": round(self._fertilizer_spreader_kg, 1)}

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def load_seeds(self, seed_type: str, count: int) -> dict[str, Any]:
        """
        Transfer seeds from the farm warehouse to the tractor seed hopper.
        Hopper holds up to 300000 plants per seed type; excess is rejected.

        Args:
            seed_type: One of STANDARD, EARLY_COLD, HIGH_DENSITY, STRESS_TOLERANT.
            count:     Number of plants to load (must be positive).
        """
        if seed_type not in {m.value for m in SeedType}:
            return {"error": f"Unknown seed_type '{seed_type}'"}
        self.seed_type = seed_type
        count = int(count)
        if count <= 0:
            return {"error": "count must be positive"}
        current = self._seed_hopper
        space = _SEED_HOPPER_MAX_PLANTS - current
        if space <= 0:
            return {"msg": f"Seed hopper already full for {seed_type}"}
        to_transfer = min(count, space)
        if not self._farm_world_app.consume_seeds(seed_type, to_transfer):
            return {"error": f"Insufficient {seed_type} seeds in warehouse: need {to_transfer}"}
        self._seed_hopper = current + to_transfer
        self.is_state_modified = True
        return {"status": "ok", "seed_hopper": self._seed_hopper, "seed_type": seed_type}

    # ------------------------------------------------------------------
    # Field preparation tools
    # ------------------------------------------------------------------

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_status(self) -> dict[str, Any]:
        """
        """
        return {
            "fuel_tank_l": round(self._fuel_tank_l, 1),
            "fertilizer_spreader_kg": round(self._fertilizer_spreader_kg, 1),
            "pesticide_tank_l": round(self._pesticide_tank_l, 1),
            "seed_type": self.seed_type,
            "seed_hopper": self._seed_hopper,
            "grain_bin_kg": round(self._grain_bin_kg, 1),
            "grain_bin_max_kg": _GRAIN_BIN_MAX_KG,
            "available_implements": ATTACHMENTS,
            "attached_implement": self._attached_implement
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def level(self) -> dict[str, Any]:
        """
        Level and till the full field surface using the attached implement.
        """
        if self._attached_implement is None:
            return {"error": "No implement attached."}
        if self._attached_implement != "grader":
            return {"error": f"Attached implement '{self._attached_implement}' is not suitable for levelling"}
        if self._farm_world_app.get_avg_vwc() > 0.35:
            return {"error": "Soil too wet for tractor operation (avg VWC > 0.35)"}
        if self._fuel_tank_l < _FUEL_PER_PREP_OP:
            return {"error": f"Insufficient fuel: need {_FUEL_PER_PREP_OP} L"}

        implement = self._attached_implement
        width_m = _ATTACH_WIDTH_M[implement]
        duration = _full_field_duration(width_m, _SPEED_TILL_MS)
        self._fuel_tank_l = round(self._fuel_tank_l - _FUEL_PER_PREP_OP, 2)
        self.time_manager.add_offset(duration)
        self._completed_prep_ops.append("level")
        self._operation_log.append({
            "op_id": str(uuid.uuid4())[:8],
            "operation": "level",
            "implement": implement,
            "working_width_m": width_m,
            "duration_s": duration,
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "implement": implement,
            "working_width_m": width_m,
            "duration_minutes": round(duration / 60, 1),
            "fuel_tank_l": round(self._fuel_tank_l, 1),
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def base_fertilize(self) -> dict[str, Any]:
        """
        Apply base fertilizer across the full field using the tractor spreader.
        """

        if self._fertilizer_spreader_kg < _BASE_FERTILIZE_KG:
            return {"error": f"Insufficient fertilizer in spreader: need {_BASE_FERTILIZE_KG} kg, have {self._fertilizer_spreader_kg:.1f} kg"}
        if self._fuel_tank_l < _FUEL_PER_PREP_OP:
            return {"error": f"Insufficient fuel: need {_FUEL_PER_PREP_OP} L, have {self._fuel_tank_l:.1f} L"}
        if self._farm_world_app.get_avg_vwc() > 0.35:
            return {"error": "Soil too wet for tractor operation (avg VWC > 0.35)"}

        self._fertilizer_spreader_kg = round(self._fertilizer_spreader_kg - _BASE_FERTILIZE_KG, 2)
        self._fuel_tank_l = round(self._fuel_tank_l - _FUEL_PER_PREP_OP, 2)
        duration = _full_field_duration(_WIDTH_FERTILIZE_M, _SPEED_FERTILIZE_MS)
        self.time_manager.add_offset(duration)
        self._completed_prep_ops.append("base_fertilize")
        self._operation_log.append({
            "op_id": str(uuid.uuid4())[:8],
            "operation": "base_fertilize",
            "fertilizer_used_kg": _BASE_FERTILIZE_KG,
            "duration_s": duration,
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "fertilizer_used_kg": _BASE_FERTILIZE_KG,
            "duration_minutes": round(duration / 60, 1),
            "fuel_tank_l": round(self._fuel_tank_l, 1),
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def form_ridges(self, ridge_width_m: float) -> dict[str, Any]:
        """
        Form ridge rows across the full field using a tractor-mounted ridge former.

        Args:
            ridge_width_m: Width of each ridge in metres.
        """
        if self._fuel_tank_l < _FUEL_PER_PREP_OP:
            return {"error": f"Insufficient fuel: need {_FUEL_PER_PREP_OP} L, have {self._fuel_tank_l:.1f} L"}
        if self._farm_world_app.get_avg_vwc() > 0.35:
            return {"error": "Soil too wet for tractor operation (avg VWC > 0.35)"}

        ridge_width_m = float(ridge_width_m)
        # Each pass forms 4 ridges side by side
        ridges_per_pass = 4
        working_width_m = ridges_per_pass * ridge_width_m
        num_ridges = int(FIELD_WIDTH_M / ridge_width_m)

        duration = _full_field_duration(working_width_m, _SPEED_RIDGE_MS)
        self._fuel_tank_l = round(self._fuel_tank_l - _FUEL_PER_PREP_OP, 2)
        self.time_manager.add_offset(duration)

        # Preserve pre-conditioned soil state (VWC, temp, pressure baselines).
        # Only resize the ridge list if ridge-width changed the count, and
        # only clear planting/observation fields on carried-over ridges.
        existing = [
            self._farm_world_app.get_ridge(i)
            for i in range(self._farm_world_app.num_ridges)
        ]
        new_ridges: list[RidgeState] = []
        for i in range(num_ridges):
            if i < len(existing):
                r = existing[i]
                r.ridge_id = i
                r.planted = False
                r.seed_type = None
                r.seed_spacing_cm = None
                r.seeds_planted = 0
                r.growth_stage = GrowthStage.BARE.value
                r.days_since_planted = 0
                r.ndvi = -1.0
                r.canopy_temp_c = -1.0
                r.grain_moisture_pct = 0.0
                r.planted_at_sim_time = None
                r.pest_pressure = r.pest_pressure_base
                r.disease_pressure = r.disease_pressure_base
                new_ridges.append(r)
            else:
                new_ridges.append(RidgeState.default(i))
        self._farm_world_app.set_ridges(new_ridges)

        self._completed_prep_ops.append("form_ridges")
        self._operation_log.append({
            "op_id": str(uuid.uuid4())[:8],
            "operation": "form_ridges",
            "ridge_width_m": ridge_width_m,
            "num_ridges": num_ridges,
            "duration_s": duration,
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "ridge_width_m": ridge_width_m,
            "num_ridges": num_ridges,
            "duration_minutes": round(duration / 60, 1),
            "fuel_tank_l": round(self._fuel_tank_l, 1),
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def apply_fertilizer(self, start_ridge: int, end_ridge: int, kg_per_ridge: float) -> dict[str, Any]:
        """
        Apply fertilizer across a contiguous block of ridges using the tractor spreader
        (up to 10 ridges per pass).

        Args:
            start_ridge:   First ridge to fertilize.
            end_ridge:     Last ridge to fertilize (max 10-ridge span).
            kg_per_ridge:  Fertilizer amount per ridge in kilograms (must be positive).
        """
        err = self._validate_ridge_window(start_ridge, end_ridge, max_width=10)
        if err:
            return {"error": err}
        if float(kg_per_ridge) <= 0:
            return {"error": "kg_per_ridge must be positive"}
        if not self._weather_app.is_trafficable:
            return {"error": "Soil too wet for tractor fertilizing (avg VWC > 0.35)"}

        ridge_count = end_ridge - start_ridge + 1
        required_kg = ridge_count * float(kg_per_ridge)
        if self._fertilizer_spreader_kg < required_kg:
            return {"error": f"Insufficient fertilizer in spreader: need {required_kg:.1f} kg, have {self._fertilizer_spreader_kg:.1f} kg"}
        if self._fuel_tank_l < _FUEL_PER_PASS:
            return {"error": f"Insufficient fuel: need {_FUEL_PER_PASS} L, have {self._fuel_tank_l:.1f} L"}

        self._fertilizer_spreader_kg = round(self._fertilizer_spreader_kg - required_kg, 2)
        self._fuel_tank_l = round(self._fuel_tank_l - _FUEL_PER_PASS, 2)
        duration = _pass_duration(_SPEED_FERTILIZE_MS)
        self.time_manager.add_offset(duration)

        for ridge_id in range(start_ridge, end_ridge + 1):
            ridge = self._farm_world_app.get_ridge(ridge_id)
            ridge.yield_potential = min(1.0, round(ridge.yield_potential + min(0.15, float(kg_per_ridge) * 0.005), 3))
        self._farm_world_app.is_state_modified = True

        op_id = str(uuid.uuid4())[:8]
        self._operation_log.append({
            "op_id": op_id,
            "operation": "apply_fertilizer",
            "ridge_ids": list(range(start_ridge, end_ridge + 1)),
            "fertilizer_used_kg": required_kg,
            "duration_s": duration,
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "fertilized_ridges": list(range(start_ridge, end_ridge + 1)),
            "fertilizer_used_kg": required_kg,
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def plant_seeds(
        self,
        start_ridge: int,
        end_ridge: int,
        depth_cm: float,
        seed_spacing_cm: float,
    ) -> dict[str, Any]:
        """
        Sow seeds across a contiguous block of ridges (up to 4 per pass).

        Seed types:
          STANDARD
          EARLY_COLD
          HIGH_DENSITY
          STRESS_TOLERANT

        Args:
            start_ridge: First ridge to plant (0-63).
            end_ridge:   Last ridge to plant (0-63, max 4-ridge span).
            depth_cm:    Sowing depth in centimetres.
            seed_spacing_cm:
                In-row seed spacing in centimetres.
        """
        err = self._validate_ridge_window(start_ridge, end_ridge, max_width=4)
        if err:
            return {"error": err}
        if not 3.0 <= float(depth_cm) <= 5.0:
            return {"error": "depth_cm must be within 3–5 cm"}
        if float(seed_spacing_cm) <= 0:
            return {"error": "seed_spacing_cm must be positive"}

        if self._completed_prep_ops != _FIELD_PREP_SEQUENCE:
            return {
                "error": (
                    "Field preparation incomplete: must finish "
                    "level -> base_fertilize -> form_ridges before planting"
                )
            }

        ridges = [self._farm_world_app.get_ridge(r) for r in range(start_ridge, end_ridge + 1)]
        if any(r.planted for r in ridges):
            return {"error": "One or more ridges are already planted"}

        avg_vwc  = sum(r.soil_vwc   for r in ridges) / len(ridges)
        avg_temp = sum(r.soil_temp_c for r in ridges) / len(ridges)
        if not 0.20 <= avg_vwc <= 0.30:
            return {"error": f"Soil VWC {avg_vwc:.3f} must be within 0.20–0.30 for planting"}
        if self.seed_type == SeedType.EARLY_COLD.value:
            if avg_temp < 8.0:
                return {"error": f"Soil temperature {avg_temp:.1f}°C too low for EARLY_COLD (min 8°C)"}
        elif avg_temp <= 10.0:
            return {"error": f"Soil temperature {avg_temp:.1f}°C must exceed 10°C for planting"}

        seeds_per_ridge = plants_per_ridge_from_spacing(seed_spacing_cm)
        seed_count = len(ridges) * seeds_per_ridge
        if self._seed_hopper < seed_count:
            return {"error": f"Insufficient {self.seed_type} seeds in hopper: need {seed_count}, have {self._seed_hopper}"}
        if self._fuel_tank_l < _FUEL_PER_PASS:
            return {"error": f"Insufficient fuel: need {_FUEL_PER_PASS} L, have {self._fuel_tank_l:.1f} L"}

        self._seed_hopper = self._seed_hopper - seed_count
        self._fuel_tank_l = round(self._fuel_tank_l - _FUEL_PER_PASS, 2)
        duration = _pass_duration(_SPEED_PLANT_MS)
        self.time_manager.add_offset(duration)
        for r in ridges:
            self._farm_world_app.set_ridge_planted(
                r.ridge_id,
                self.seed_type,
                seed_spacing_cm=float(seed_spacing_cm),
                seeds_planted=seeds_per_ridge,
            )

        op_id = str(uuid.uuid4())[:8]
        self._operation_log.append({
            "op_id": op_id,
            "operation": "plant_seeds",
            "ridge_ids": list(range(start_ridge, end_ridge + 1)),
            "depth_cm": float(depth_cm),
            "seed_spacing_cm": float(seed_spacing_cm),
            "seeds_per_ridge": seeds_per_ridge,
            "seeds_used": seed_count,
            "duration_s": duration,
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "planted_ridges": list(range(start_ridge, end_ridge + 1)),
            "depth_cm": float(depth_cm),
            "seed_spacing_cm": float(seed_spacing_cm),
            "seeds_per_ridge": seeds_per_ridge,
            "seeds_used": seed_count,
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
        if self._pesticide_tank_l < required_liters:
            return {"error": f"Insufficient pesticide in tank: need {required_liters:.1f} L, have {self._pesticide_tank_l:.1f} L"}
        if self._fuel_tank_l < _FUEL_PER_PASS:
            return {"error": f"Insufficient fuel: need {_FUEL_PER_PASS} L, have {self._fuel_tank_l:.1f} L"}

        self._pesticide_tank_l = round(self._pesticide_tank_l - required_liters, 2)
        self._fuel_tank_l = round(self._fuel_tank_l - _FUEL_PER_PASS, 2)
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
        if self._fuel_tank_l < _FUEL_PER_PASS:
            return {"error": f"Insufficient fuel: need {_FUEL_PER_PASS} L, have {self._fuel_tank_l:.1f} L"}

        # Check that grain bin can hold this pass worst-case (yield_potential ≤ 1.0).
        ridge_count = end_ridge - start_ridge + 1
        worst_case_grain = ridge_count * GRAIN_KG_PER_RIDGE
        if self._grain_bin_kg + worst_case_grain > _GRAIN_BIN_MAX_KG:
            return {
                "error": (
                    f"Grain bin would overflow: {self._grain_bin_kg:.0f} + "
                    f"{worst_case_grain:.0f} kg > {_GRAIN_BIN_MAX_KG:.0f} kg. "
                    f"Call unload_grain first."
                )
            }

        self._fuel_tank_l = round(self._fuel_tank_l - _FUEL_PER_PASS, 2)
        duration = _pass_duration(_SPEED_HARVEST_MS)
        self.time_manager.add_offset(duration)
        grain_added = 0.0
        for r in ridges:
            result = self._farm_world_app.set_ridge_harvested(r.ridge_id)
            grain_added += float(result.get("grain_kg_added", 0.0))
        grain_added = round(grain_added, 2)
        self._grain_bin_kg = round(self._grain_bin_kg + grain_added, 2)

        op_id = str(uuid.uuid4())[:8]
        self._operation_log.append({
            "op_id": op_id,
            "operation": "harvest",
            "ridge_ids": list(range(start_ridge, end_ridge + 1)),
            "grain_kg_added": grain_added,
            "grain_bin_kg": round(self._grain_bin_kg, 1),
            "duration_s": duration,
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "harvested_ridges": list(range(start_ridge, end_ridge + 1)),
            "grain_kg_added": grain_added,
            "grain_bin_kg": round(self._grain_bin_kg, 1),
            "grain_bin_max_kg": _GRAIN_BIN_MAX_KG,
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def unload_grain(self) -> dict[str, Any]:
        """
        Unload the combine grain bin into the farm warehouse (trailer transfer).
        Takes about 8 minutes. Call this when the bin is full or after the
        final harvest pass to deposit grain into the warehouse inventory.
        """
        if self._grain_bin_kg <= 0.0:
            return {"error": "Grain bin is already empty"}
        unloaded = round(self._grain_bin_kg, 2)
        self.time_manager.add_offset(_GRAIN_UNLOAD_DURATION_S)
        self._farm_world_app.add_grain_to_inventory(unloaded)
        self._grain_bin_kg = 0.0

        op_id = str(uuid.uuid4())[:8]
        self._operation_log.append({
            "op_id": op_id,
            "operation": "unload_grain",
            "grain_kg_unloaded": unloaded,
            "duration_s": _GRAIN_UNLOAD_DURATION_S,
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "grain_kg_unloaded": unloaded,
            "grain_bin_kg": 0.0,
            "duration_minutes": round(_GRAIN_UNLOAD_DURATION_S / 60, 1),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_ridge_window(self, start_ridge: int, end_ridge: int, max_width: int) -> str | None:
        if not 0 <= start_ridge <= end_ridge < self._farm_world_app.num_ridges:
            return f"Invalid ridge range [{start_ridge}, {end_ridge}]"
        if end_ridge - start_ridge + 1 > max_width:
            return f"Range cannot exceed {max_width} ridges per pass"
        return None
