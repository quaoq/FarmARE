"""
RobotApp - ground inspection robots for Farm-World.

All inspections are synchronous: they complete immediately, advance the
simulation clock by the real-world round-trip duration, and consume battery.
"""
from __future__ import annotations

import uuid
from typing import Any

from are.simulation.apps.app import App
from are.simulation.apps.farm_world.farm_world_app import (
    FIELD_LENGTH_M,
    NUM_RIDGES,
    FarmWorldApp,
)
from are.simulation.apps.farm_world.weather_app import WeatherApp
from are.simulation.tool_utils import OperationType, app_tool, data_tool, env_tool
from are.simulation.types import EventType, event_registered
from are.simulation.utils.type_utils import type_check

# Zhiyuan D1 Max walking speed (m/s)
_ROBOT_SPEED_MS         = 0.8
_ROBOT_SETUP_S          = 30    # fixed setup time per inspection
_BATTERY_PER_INSPECTION = 20.0  # % per ridge inspection


def _inspect_duration() -> int:
    """Round-trip walk time for one ridge inspection (out and back)."""
    return int(FIELD_LENGTH_M * 2 / _ROBOT_SPEED_MS) + _ROBOT_SETUP_S


class RobotApp(App):
    """Manage two Zhiyuan D1 Max robot dog inspection platforms."""

    def __init__(self, farm_world_app: FarmWorldApp, weather_app: WeatherApp) -> None:
        super().__init__(name="RobotApp")
        self._farm_world_app = farm_world_app
        self._weather_app = weather_app
        self._robots = self._default_robots()
        self._inspection_log: list[dict[str, Any]] = []

    def get_state(self) -> dict[str, Any]:
        return {
            "app_name": self.name,
            "robots": {
                robot_id: self._robot_state(robot)
                for robot_id, robot in self._robots.items()
            },
            "inspection_log": list(self._inspection_log),
        }

    def load_state(self, state_dict: dict[str, Any]) -> None:
        self._robots = {
            robot_id: dict(robot)
            for robot_id, robot in state_dict["robots"].items()
        }
        self._inspection_log = [dict(item) for item in state_dict.get("inspection_log", [])]

    def reset(self) -> None:
        super().reset()
        self._robots = self._default_robots()
        self._inspection_log = []

    # ------------------------------------------------------------------
    # Agent tools
    # ------------------------------------------------------------------

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def inspect_ridge(self, robot_id: str, ridge_id: int) -> dict[str, Any]:
        """
        Send a robot dog to walk the full length of a ridge and back, reporting
        pest and disease presence at ground level.

        Use this to ground-truth anomalies flagged by drone surveys.

        Args:
            robot_id:  "robot_0" or "robot_1".
            ridge_id:  Ridge to inspect (0-63).
        """
        robot = self._robots.get(robot_id)
        if robot is None:
            return {"error": f"Unknown robot_id '{robot_id}'. Valid: robot_0, robot_1"}
        if not 0 <= ridge_id < NUM_RIDGES:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        if robot["battery_pct"] < 15.0:
            return {"error": f"Battery {robot['battery_pct']}% below minimum 15%"}

        wet_ground = self._weather_app.rainfall_mm > 0.0

        # Execute
        robot["battery_pct"] = round(robot["battery_pct"] - _BATTERY_PER_INSPECTION, 1)
        duration = _inspect_duration()
        self.time_manager.add_offset(duration)
        ridge = self._farm_world_app.get_ridge(ridge_id)
        result = {
            "ridge_id": ridge_id,
            "pest_detected": ridge.pest_pressure >= 0.2,
            "pest_type": "aphid" if ridge.pest_pressure >= 0.2 else None,
            "pest_count_estimate": self._pressure_band(ridge.pest_pressure),
            "disease_detected": ridge.disease_pressure >= 0.2,
            "disease_type": "leaf_spot" if ridge.disease_pressure >= 0.2 else None,
            "confidence": "high",
        }
        if wet_ground:
            result["warning"] = "ground_wet, mobility_reduced"

        inspection_id = str(uuid.uuid4())[:8]
        self._inspection_log.append({
            "inspection_id": inspection_id,
            "robot_id": robot_id,
            "ridge_id": ridge_id,
            "duration_s": duration,
            "result": result,
        })
        self.is_state_modified = True

        response: dict[str, Any] = {
            "status": "ok",
            "inspection_id": inspection_id,
            "result": result,
            "battery_remaining_pct": robot["battery_pct"],
        }
        if wet_ground:
            response["warning"] = "ground_wet"
        return response

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def check_robot_status(self) -> dict[str, Any]:
        """Return battery level and availability for both robot dogs."""
        return {
            "robots": {
                robot_id: self._robot_state(robot)
                for robot_id, robot in self._robots.items()
            }
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def charge_robot(self, robot_id: str) -> dict[str, Any]:
        """
        Send a robot dog back to its charging station.

        Args:
            robot_id: "robot_0" or "robot_1".
        """
        robot = self._robots.get(robot_id)
        if robot is None:
            return {"error": f"Unknown robot_id '{robot_id}'. Valid: robot_0, robot_1"}
        robot["charging"] = True
        self.is_state_modified = True
        return {"status": "charging_started", "robot_id": robot_id}

    # ------------------------------------------------------------------
    # Environment tools
    # ------------------------------------------------------------------

    @type_check
    @env_tool()
    @event_registered(operation_type=OperationType.WRITE, event_type=EventType.ENV)
    def advance_day(self) -> dict[str, Any]:
        """Complete queued robot charging cycles."""
        charged = []
        for robot_id, robot in self._robots.items():
            if robot["charging"]:
                robot["battery_pct"] = 100.0
                robot["charging"] = False
                charged.append(robot_id)
        if charged:
            self.is_state_modified = True
        return {"status": "ok", "charged_robots": charged}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _default_robots(self) -> dict[str, dict[str, Any]]:
        return {
            "robot_0": {"battery_pct": 100.0, "charging": False},
            "robot_1": {"battery_pct": 100.0, "charging": False},
        }

    def _robot_state(self, robot: dict[str, Any]) -> dict[str, Any]:
        return {
            "battery_pct": round(robot["battery_pct"], 1),
            "charging": robot["charging"],
        }

    def _pressure_band(self, pressure: float) -> str:
        if pressure >= 0.7:
            return "high"
        if pressure >= 0.35:
            return "medium"
        return "low"
