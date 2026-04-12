"""
DroneApp - multispectral and thermal drone operations for Farm-World.

All missions are synchronous: they complete immediately, advance the
simulation clock by the real-world flight duration, and consume battery.
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
    FarmWorldApp,
)
from are.simulation.apps.farm_world.models import GrowthStage
from are.simulation.apps.farm_world.weather_app import WeatherApp
from are.simulation.tool_utils import OperationType, app_tool, data_tool, env_tool
from are.simulation.types import EventType, event_registered
from are.simulation.utils.type_utils import type_check

# Drone flight speeds (m/s)
_MAVIC3M_SPEED_MS   = 8.0   # Mavic 3M survey speed
_MATRICE4T_SPEED_MS = 4.0   # Matrice 4T thermal inspection speed

# Flight line spacing: one pass per ridge width
_SURVEY_LINES       = FIELD_WIDTH_M / RIDGE_WIDTH_M   # ~64 lines for full field
_SURVEY_TAKEOFF_S   = 30    # fixed takeoff + landing overhead
_THERMAL_TAKEOFF_S  = 20    # fixed takeoff + landing overhead per dispatch

# Battery consumption
_SURVEY_BATTERY_FULL_FIELD  = 60.0  # % for full 64-ridge survey
_THERMAL_BATTERY_PER_RIDGE  = 3.0   # % per ridge, min 8%


def _survey_duration(ridge_count: int) -> int:
    """Flight time in seconds for a multispectral survey over ridge_count ridges."""
    lines = max(1, round(_SURVEY_LINES * ridge_count / NUM_RIDGES))
    return int(lines * FIELD_LENGTH_M / _MAVIC3M_SPEED_MS) + _SURVEY_TAKEOFF_S


def _thermal_duration(ridge_count: int) -> int:
    """Flight time in seconds for a thermal inspection over ridge_count ridges."""
    return int(ridge_count * FIELD_LENGTH_M / _MATRICE4T_SPEED_MS) + _THERMAL_TAKEOFF_S


class DroneApp(App):
    """Manage Mavic 3M multispectral and Matrice 4T thermal drones."""

    def __init__(self, farm_world_app: FarmWorldApp, weather_app: WeatherApp) -> None:
        super().__init__(name="DroneApp")
        self._farm_world_app = farm_world_app
        self._weather_app = weather_app
        self._drones = self._default_drones()
        self._mission_log: list[dict[str, Any]] = []

    def get_state(self) -> dict[str, Any]:
        return {
            "app_name": self.name,
            "drones": {
                drone_id: self._drone_state(drone)
                for drone_id, drone in self._drones.items()
            },
            "mission_log": list(self._mission_log),
        }

    def load_state(self, state_dict: dict[str, Any]) -> None:
        self._drones = {
            drone_id: dict(drone)
            for drone_id, drone in state_dict["drones"].items()
        }
        self._mission_log = [dict(item) for item in state_dict.get("mission_log", [])]

    def reset(self) -> None:
        super().reset()
        self._drones = self._default_drones()
        self._mission_log = []

    # ------------------------------------------------------------------
    # Agent tools
    # ------------------------------------------------------------------

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def fly_multispectral_survey(self, start_ridge: int, end_ridge: int) -> dict[str, Any]:
        """
        Fly the Mavic 3M multispectral drone over a contiguous ridge range and
        update NDVI readings for all surveyed ridges.

        Args:
            start_ridge: First ridge of the survey range (0-63).
            end_ridge:   Last ridge of the survey range (0-63, >= start_ridge).
        """
        if not 0 <= start_ridge <= end_ridge < NUM_RIDGES:
            return {"error": f"Invalid ridge range [{start_ridge}, {end_ridge}]"}
        err = self._preflight_check("mavic3m")
        if err:
            return {"error": err}

        ridges = list(range(start_ridge, end_ridge + 1))
        duration = _survey_duration(len(ridges))
        battery_use = round(min(60.0, _SURVEY_BATTERY_FULL_FIELD * len(ridges) / NUM_RIDGES), 1)

        # Execute
        self._drones["mavic3m"]["battery_pct"] = round(self._drones["mavic3m"]["battery_pct"] - battery_use, 1)
        self.time_manager.add_offset(duration)
        observations = []
        for ridge_id in ridges:
            ndvi = self._estimate_ndvi(ridge_id)
            self._farm_world_app.update_ridge_ndvi(ridge_id, ndvi)
            observations.append({"ridge_id": ridge_id, "ndvi": ndvi})

        mission_id = str(uuid.uuid4())[:8]
        self._mission_log.append({
            "mission_id": mission_id,
            "drone_id": "mavic3m",
            "mission_type": "survey",
            "target_ridges": ridges,
            "battery_used_pct": battery_use,
            "duration_s": duration,
            "observations": observations,
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "mission_id": mission_id,
            "surveyed_ridges": ridges,
            "observations": observations,
            "duration_minutes": round(duration / 60, 1),
            "battery_remaining_pct": self._drones["mavic3m"]["battery_pct"],
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def fly_thermal_inspection(self, ridge_ids: list[int]) -> dict[str, Any]:
        """
        Fly the Matrice 4T thermal drone over selected ridges and update canopy
        temperature readings.

        Use this to detect water stress or disease hotspots flagged by NDVI anomalies.

        Args:
            ridge_ids: List of ridge IDs to inspect (0-63, duplicates ignored).
        """
        if not ridge_ids:
            return {"error": "ridge_ids cannot be empty"}
        if any(r < 0 or r >= NUM_RIDGES for r in ridge_ids):
            return {"error": "All ridge_ids must be within 0-63"}
        err = self._preflight_check("matrice4t")
        if err:
            return {"error": err}

        ridges = sorted(set(ridge_ids))
        duration = _thermal_duration(len(ridges))
        battery_use = round(min(80.0, max(8.0, len(ridges) * _THERMAL_BATTERY_PER_RIDGE)), 1)

        # Execute
        self._drones["matrice4t"]["battery_pct"] = round(self._drones["matrice4t"]["battery_pct"] - battery_use, 1)
        self.time_manager.add_offset(duration)
        observations = []
        for ridge_id in ridges:
            canopy_temp = self._estimate_canopy_temp(ridge_id)
            self._farm_world_app.update_ridge_canopy_temp(ridge_id, canopy_temp)
            observations.append({"ridge_id": ridge_id, "canopy_temp_c": canopy_temp})

        mission_id = str(uuid.uuid4())[:8]
        self._mission_log.append({
            "mission_id": mission_id,
            "drone_id": "matrice4t",
            "mission_type": "thermal",
            "target_ridges": ridges,
            "battery_used_pct": battery_use,
            "duration_s": duration,
            "observations": observations,
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "mission_id": mission_id,
            "inspected_ridges": ridges,
            "observations": observations,
            "duration_minutes": round(duration / 60, 1),
            "battery_remaining_pct": self._drones["matrice4t"]["battery_pct"],
        }

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def check_drone_status(self) -> dict[str, Any]:
        """Return battery level and availability for both drones."""
        return {
            "drones": {
                drone_id: self._drone_state(drone)
                for drone_id, drone in self._drones.items()
            }
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def charge_drone(self, drone_id: str) -> dict[str, Any]:
        """
        Send a drone back to its charging station.

        Args:
            drone_id: "mavic3m" or "matrice4t".
        """
        drone = self._drones.get(drone_id)
        if drone is None:
            return {"error": f"Unknown drone_id '{drone_id}'. Valid: mavic3m, matrice4t"}
        drone["charging"] = True
        self.is_state_modified = True
        return {"status": "charging_started", "drone_id": drone_id}

    # ------------------------------------------------------------------
    # Environment tools
    # ------------------------------------------------------------------

    @type_check
    @env_tool()
    @event_registered(operation_type=OperationType.WRITE, event_type=EventType.ENV)
    def advance_day(self) -> dict[str, Any]:
        """Complete queued drone charging cycles."""
        charged = []
        for drone_id, drone in self._drones.items():
            if drone["charging"]:
                drone["battery_pct"] = 100.0
                drone["charging"] = False
                charged.append(drone_id)
        if charged:
            self.is_state_modified = True
        return {"status": "ok", "charged_drones": charged}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _default_drones(self) -> dict[str, dict[str, Any]]:
        return {
            "mavic3m":   {"battery_pct": 100.0, "charging": False},
            "matrice4t": {"battery_pct": 100.0, "charging": False},
        }

    def _drone_state(self, drone: dict[str, Any]) -> dict[str, Any]:
        return {
            "battery_pct": round(drone["battery_pct"], 1),
            "charging": drone["charging"],
        }

    def _preflight_check(self, drone_id: str) -> str | None:
        drone = self._drones[drone_id]
        if drone["battery_pct"] < 20.0:
            return f"Battery {drone['battery_pct']}% below minimum 20%"
        if drone["charging"]:
            return "Drone is currently charging"
        if not self._weather_app.is_flyable:
            return "Weather conditions do not allow flight (rain or wind >= 12 m/s)"
        return None

    def _estimate_ndvi(self, ridge_id: int) -> float:
        ridge = self._farm_world_app.get_ridge(ridge_id)
        if not ridge.planted or ridge.growth_stage == GrowthStage.BARE.value:
            return -1.0
        base_by_stage = {
            GrowthStage.VE.value: 0.20, GrowthStage.V1.value: 0.35,
            GrowthStage.V2.value: 0.45, GrowthStage.V3.value: 0.55,
            GrowthStage.V4.value: 0.65, GrowthStage.V5.value: 0.72,
            GrowthStage.V6.value: 0.78, GrowthStage.R1.value: 0.80,
            GrowthStage.R2.value: 0.82, GrowthStage.R3.value: 0.84,
            GrowthStage.R4.value: 0.80, GrowthStage.R5.value: 0.75,
            GrowthStage.R6.value: 0.68, GrowthStage.R7.value: 0.55,
            GrowthStage.R8.value: 0.35,
        }
        base = base_by_stage.get(ridge.growth_stage, 0.5)
        water_stress = max(0.0, 0.18 - ridge.soil_vwc) * 1.2
        ndvi = base - ridge.pest_pressure * 0.35 - ridge.disease_pressure * 0.25 - water_stress
        return round(max(0.05, min(0.95, ndvi)), 3)

    def _estimate_canopy_temp(self, ridge_id: int) -> float:
        ridge = self._farm_world_app.get_ridge(ridge_id)
        weather = self._weather_app.get_current_weather()
        base = float(weather["temp_c"]) + 2.0
        water_stress = max(0.0, 0.20 - ridge.soil_vwc) * 40.0
        return round(base + water_stress + ridge.pest_pressure * 4.0 + ridge.disease_pressure * 3.0, 2)
