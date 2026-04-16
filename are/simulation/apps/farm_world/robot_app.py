"""
RobotApp - ground inspection robots for Farm-World.

Each RobotApp instance represents one physical robot dog (e.g. Zhiyuan D1 Max).
Instantiate with robot-specific parameters.

All inspections are synchronous: they complete immediately, advance the
simulation clock by the real-world round-trip duration, and consume battery.
Charging is asynchronous and completes after about 60 minutes of
environment time.
"""
from __future__ import annotations

import uuid
from typing import Any

from are.simulation.apps.app import App
from are.simulation.apps.farm_world.farm_world_app import (
    FIELD_LENGTH_M,
    FarmWorldApp,
)
from are.simulation.apps.farm_world.weather_app import WeatherApp
from are.simulation.tool_utils import OperationType, app_tool, data_tool
from are.simulation.types import event_registered
from are.simulation.utils.type_utils import type_check

# Zhiyuan D1 Max walking speed (m/s)
_ROBOT_SPEED_MS         = 0.8
_ROBOT_SETUP_S          = 30    # fixed setup time per inspection
_BATTERY_PER_INSPECTION = 20.0  # % per ridge inspection
_CHARGE_DURATION_S      = 60 * 60  # 60 minutes to full charge


def _inspect_duration() -> int:
    """Round-trip walk time for one ridge inspection (out and back)."""
    return int(FIELD_LENGTH_M * 2 / _ROBOT_SPEED_MS) + _ROBOT_SETUP_S


class RobotApp(App):
    """
    Ground inspection robot platform. Each instance represents one physical robot dog.

    Instantiate with robot-specific parameters::

        robot_0 = RobotApp(
            farm_world_app=farm,
            weather_app=weather,
            name="Robot0",
            description="Zhiyuan D1 Max #1 — ground-level pest/disease inspection robot",
        )
    """

    def __init__(
        self,
        farm_world_app: FarmWorldApp,
        weather_app: WeatherApp,
        *,
        name: str = "RobotApp",
        description: str = "",
    ) -> None:
        super().__init__(name=name)
        self._farm_world_app = farm_world_app
        self._weather_app = weather_app

        # Robot configuration
        self.description = description

        # Runtime state
        self._battery_pct: float = 100.0
        self._charging: bool = False
        self._charge_started_at: float | None = None
        self._charge_complete_at: float | None = None
        self._charge_start_battery_pct: float | None = None
        self._inspection_log: list[dict[str, Any]] = []

    def get_state(self) -> dict[str, Any]:
        self._sync_charge_state()
        return {
            "app_name": self.name,
            "description": self.description,
            "battery_pct": round(self._battery_pct, 1),
            "charging": self._charging,
            "charge_session": self._serialize_charge_session(),
            "inspection_log": list(self._inspection_log),
        }

    def load_state(self, state_dict: dict[str, Any]) -> None:
        self._battery_pct = state_dict.get("battery_pct", 100.0)
        self._charging = state_dict.get("charging", False)
        charge_session = state_dict.get("charge_session") or {}
        self._charge_started_at = charge_session.get("started_at")
        self._charge_complete_at = charge_session.get("complete_at")
        self._charge_start_battery_pct = charge_session.get("start_battery_pct")
        self._inspection_log = [dict(item) for item in state_dict.get("inspection_log", [])]

    def reset(self) -> None:
        super().reset()
        self._battery_pct = 100.0
        self._charging = False
        self._charge_started_at = None
        self._charge_complete_at = None
        self._charge_start_battery_pct = None
        self._inspection_log = []

    # ------------------------------------------------------------------
    # Agent tools
    # ------------------------------------------------------------------

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def inspect_ridge(self, ridge_id: int) -> dict[str, Any]:
        """
        Send this robot dog to walk the full length of a ridge and back, reporting
        pest and disease presence at ground level.

        Use this to ground-truth anomalies flagged by drone surveys.

        Args:
            ridge_id:  Ridge to inspect (0-63).
        """
        self._sync_charge_state()
        if not 0 <= ridge_id < self._farm_world_app.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        if self._battery_pct < 15.0:
            return {"error": f"Battery {self._battery_pct}% below minimum 15%"}
        if self._charging:
            return {"error": "Robot is currently charging"}

        wet_ground = self._weather_app.rainfall_mm > 0.0

        # Execute
        self._battery_pct = round(self._battery_pct - _BATTERY_PER_INSPECTION, 1)
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
            "robot": self.name,
            "ridge_id": ridge_id,
            "duration_s": duration,
            "result": result,
        })
        self.is_state_modified = True

        response: dict[str, Any] = {
            "status": "ok",
            "inspection_id": inspection_id,
            "result": result,
            "battery_remaining_pct": self._battery_pct,
        }
        if wet_ground:
            response["warning"] = "ground_wet"
        return response

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def check_status(self) -> dict[str, Any]:
        """Return battery level, charging state, and robot configuration."""
        self._sync_charge_state()
        return {
            "robot": self.name,
            "description": self.description,
            "battery_pct": round(self._battery_pct, 1),
            "charging": self._charging,
            "eta_minutes_to_full": self._remaining_charge_minutes(),
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def charge(self) -> dict[str, Any]:
        """
        Send this robot dog back to its charging station.

        Charging completes asynchronously after about 60 minutes of
        environment time. The battery level increases gradually while charging.
        """
        self._sync_charge_state()
        if self._charging:
            return {
                "status": "charging",
                "robot": self.name,
                "battery_pct": round(self._battery_pct, 1),
                "eta_minutes_to_full": self._remaining_charge_minutes(),
                "message": "Already charging.",
            }
        if self._battery_pct >= 100.0:
            return {
                "status": "already_full",
                "robot": self.name,
                "battery_pct": 100.0,
                "message": "Battery is already full.",
            }

        now = self.time_manager.time()
        self._charging = True
        self._charge_started_at = now
        self._charge_complete_at = now + _CHARGE_DURATION_S
        self._charge_start_battery_pct = self._battery_pct
        self.is_state_modified = True
        return {
            "status": "charging_started",
            "robot": self.name,
            "battery_pct": round(self._battery_pct, 1),
            "eta_minutes_to_full": 60.0,
            "charge_complete_at": self._charge_complete_at,
            "message": "Charging started. Estimated full charge in about 60 minutes.",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sync_charge_state(self) -> None:
        if not self._charging:
            return
        if (
            self._charge_started_at is None
            or self._charge_complete_at is None
            or self._charge_start_battery_pct is None
        ):
            return

        now = self.time_manager.time()
        total_duration = max(self._charge_complete_at - self._charge_started_at, 1.0)
        progress = min(
            max((now - self._charge_started_at) / total_duration, 0.0),
            1.0,
        )
        self._battery_pct = round(
            self._charge_start_battery_pct
            + (100.0 - self._charge_start_battery_pct) * progress,
            1,
        )
        if now >= self._charge_complete_at:
            self._battery_pct = 100.0
            self._charging = False
            self._charge_started_at = None
            self._charge_complete_at = None
            self._charge_start_battery_pct = None
        self.is_state_modified = True

    def _remaining_charge_minutes(self) -> float | None:
        if not self._charging or self._charge_complete_at is None:
            return None
        remaining_seconds = max(0.0, self._charge_complete_at - self.time_manager.time())
        return round(remaining_seconds / 60.0, 1)

    def _serialize_charge_session(self) -> dict[str, Any] | None:
        if not self._charging:
            return None
        return {
            "started_at": self._charge_started_at,
            "complete_at": self._charge_complete_at,
            "start_battery_pct": self._charge_start_battery_pct,
        }

    def _pressure_band(self, pressure: float) -> str:
        if pressure >= 0.7:
            return "high"
        if pressure >= 0.35:
            return "medium"
        return "low"
