"""
FieldOpsApp - ridge-level irrigation and manual spot spray controls.

All operations are synchronous: they complete immediately and advance the
simulation clock by the real-world duration of the task.
"""
from __future__ import annotations

from typing import Any

from are.simulation.apps.app import App
from are.simulation.apps.farm_world.farm_world_app import (
    FIELD_LENGTH_M,
    NUM_RIDGES,
    FarmWorldApp,
)
from are.simulation.apps.farm_world.weather_app import WeatherApp
from are.simulation.tool_utils import OperationType, app_tool, data_tool
from are.simulation.types import event_registered
from are.simulation.utils.type_utils import type_check

# Irrigation setup time per ridge (s) — valve open/close, hose connection
_IRRIGATION_SETUP_S    = 300
_IRRIGATION_S_PER_HOUR = 3600

# Manual backpack sprayer speed (m/s) — operator walking pace
_MANUAL_SPRAY_SPEED_MS = 0.8
_MANUAL_SPRAY_SETUP_S  = 15    # fill/prepare sprayer


def _manual_spray_duration() -> int:
    """Time in seconds to walk one ridge with a backpack sprayer."""
    return int(FIELD_LENGTH_M / _MANUAL_SPRAY_SPEED_MS) + _MANUAL_SPRAY_SETUP_S


class FieldOpsApp(App):
    """Ridge-level irrigation and manual spot spraying."""

    def __init__(self, farm_world_app: FarmWorldApp, weather_app: WeatherApp) -> None:
        super().__init__(name="FieldOpsApp")
        self._farm_world_app = farm_world_app
        self._weather_app = weather_app
        self._irrigation_log: list[dict[str, Any]] = []
        self._manual_spray_log: list[dict[str, Any]] = []

    def get_state(self) -> dict[str, Any]:
        return {
            "app_name": self.name,
            "irrigation_log": list(self._irrigation_log),
            "manual_spray_log": list(self._manual_spray_log),
        }

    def load_state(self, state_dict: dict[str, Any]) -> None:
        self._irrigation_log = [dict(item) for item in state_dict.get("irrigation_log", [])]
        self._manual_spray_log = [dict(item) for item in state_dict.get("manual_spray_log", [])]

    def reset(self) -> None:
        super().reset()
        self._irrigation_log = []
        self._manual_spray_log = []

    # ------------------------------------------------------------------
    # Agent tools
    # ------------------------------------------------------------------

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def irrigate_ridge(self, ridge_id: int, duration_hours: float) -> dict[str, Any]:
        """
        Irrigate a single ridge.

        Args:
            ridge_id:       Ridge to irrigate (0-63).
            duration_hours: Irrigation run time in hours.
        """
        if not 0 <= ridge_id < NUM_RIDGES:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        if float(duration_hours) <= 0:
            return {"error": "duration_hours must be positive"}
        ridge = self._farm_world_app.get_ridge(ridge_id)
        if ridge.soil_vwc >= 0.30:
            return {"error": f"Ridge {ridge_id} soil VWC {ridge.soil_vwc:.3f} already >= 0.30"}

        duration_s = _IRRIGATION_SETUP_S + int(float(duration_hours) * _IRRIGATION_S_PER_HOUR)
        self.time_manager.add_offset(duration_s)
        self._farm_world_app.set_irrigation_pending(ridge_id)

        self._irrigation_log.append({
            "ridge_id": ridge_id,
            "duration_hours": float(duration_hours),
            "duration_s": duration_s,
            "date": self._farm_world_app.get_state()["sim_date"],
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "ridge_id": ridge_id,
            "duration_hours": float(duration_hours),
            "duration_minutes": round(duration_s / 60, 1),
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def irrigate_range(self, start: int, end: int, duration_hours: float) -> dict[str, Any]:
        """
        Irrigate a contiguous range of ridges.

        Args:
            start:          First ridge to irrigate (0-63).
            end:            Last ridge to irrigate (0-63, >= start).
            duration_hours: Irrigation run time per ridge in hours.
        """
        if not 0 <= start <= end < NUM_RIDGES:
            return {"error": f"Invalid ridge range [{start}, {end}]"}
        if float(duration_hours) <= 0:
            return {"error": "duration_hours must be positive"}
        for ridge_id in range(start, end + 1):
            ridge = self._farm_world_app.get_ridge(ridge_id)
            if ridge.soil_vwc >= 0.30:
                return {"error": f"Ridge {ridge_id} soil VWC {ridge.soil_vwc:.3f} already >= 0.30"}

        ridge_count = end - start + 1
        duration_s = ridge_count * (_IRRIGATION_SETUP_S + int(float(duration_hours) * _IRRIGATION_S_PER_HOUR))
        self.time_manager.add_offset(duration_s)

        irrigated = []
        for ridge_id in range(start, end + 1):
            self._farm_world_app.set_irrigation_pending(ridge_id)
            self._irrigation_log.append({
                "ridge_id": ridge_id,
                "duration_hours": float(duration_hours),
                "duration_s": _IRRIGATION_SETUP_S + int(float(duration_hours) * _IRRIGATION_S_PER_HOUR),
                "date": self._farm_world_app.get_state()["sim_date"],
            })
            irrigated.append(ridge_id)

        self.is_state_modified = True
        return {
            "status": "ok",
            "irrigated_ridges": irrigated,
            "duration_hours_per_ridge": float(duration_hours),
            "total_duration_minutes": round(duration_s / 60, 1),
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def apply_pesticide_manual(self, ridge_id: int) -> dict[str, Any]:
        """
        Apply pesticide to a single ridge using the handheld backpack sprayer.
        Suitable for small, localised problems. For larger outbreaks use the
        tractor spray boom instead.

        Args:
            ridge_id: Ridge to spray (0-63).
        """
        if not 0 <= ridge_id < NUM_RIDGES:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        if not self._weather_app.is_sprayable:
            return {"error": "Weather conditions do not allow spraying (rain or wind >= 5 m/s)"}

        self.time_manager.add_offset(_manual_spray_duration())
        self._farm_world_app.update_ridge_pesticide(ridge_id)

        self._manual_spray_log.append({
            "ridge_id": ridge_id,
            "date": self._farm_world_app.get_state()["sim_date"],
            "method": "manual_backpack",
            "duration_s": _manual_spray_duration(),
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "ridge_id": ridge_id,
        }

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_irrigation_log(self) -> dict[str, Any]:
        """
        Return the irrigation history for all ridges.
        """
        return {"irrigation_log": list(self._irrigation_log)}
