"""
WeatherApp — current weather state and 7-day forecast.

Data source: on-farm weather station.
"""
from __future__ import annotations

import logging
from typing import Any

from are.simulation.apps.app import App
from are.simulation.apps.farm_world.models import WeatherState
from are.simulation.tool_utils import OperationType, app_tool, data_tool, env_tool
from are.simulation.types import EventType, event_registered
from are.simulation.utils.type_utils import type_check

logger = logging.getLogger(__name__)

# Operation condition thresholds
_MAX_WIND_FLY_MS   = 12.0   # max wind speed for drone flight (m/s) [PDF-p7]
_MAX_WIND_SPRAY_MS =  5.0   # max wind speed for pesticide spray (m/s) [PDF-p9]
_MAX_VWC_TRAFFIC   =  0.35  # max avg soil VWC for tractor trafficability [PDF-p9]


class WeatherApp(App):
    """
    Maintains current weather and 7-day forecast.

    avg_soil_vwc is pushed in by the scenario via set_weather() or a dedicated
    env_tool, so WeatherApp does not hold a reference to FarmWorldApp. [设计]
    """

    def __init__(self) -> None:
        super().__init__(name="WeatherApp")
        self._weather: WeatherState = WeatherState.default()
        self._avg_soil_vwc: float = 0.22  # updated by scenario after advance_day

    # ------------------------------------------------------------------
    # App interface
    # ------------------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        return {
            "app_name": self.name,
            "current": self._current_weather_dict(),
            "forecast": list(self._weather.forecast),
            "avg_soil_vwc": round(self._avg_soil_vwc, 4),
        }

    def load_state(self, state_dict: dict[str, Any]) -> None:
        current = dict(state_dict["current"])
        current["forecast"] = state_dict.get("forecast", current.get("forecast", []))
        self._weather = WeatherState.from_dict(current)
        self._avg_soil_vwc = state_dict.get("avg_soil_vwc", 0.22)

    def reset(self) -> None:
        super().reset()
        self._weather = WeatherState.default()
        self._avg_soil_vwc = 0.22

    # ------------------------------------------------------------------
    # Agent tools
    # ------------------------------------------------------------------

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_current_weather(self) -> dict[str, Any]:
        """Return today's weather readings from the on-farm weather station."""
        return self._current_weather_dict()

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_forecast(self, days: int = 7) -> dict[str, Any]:
        """
        Return weather forecast for the next N days (max 7).

        Args:
            days: Number of forecast days to return (1-7).
        """
        days = max(1, min(7, int(days)))
        return {"forecast": self._weather.forecast[:days]}

    # ------------------------------------------------------------------
    # Environment tools
    # ------------------------------------------------------------------

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def set_weather(
        self,
        date: str,
        temp_c: float,
        humidity_pct: float,
        wind_speed_ms: float,
        rainfall_mm: float,
        solar_radiation: float,
        forecast: list[dict] | None = None,
        avg_soil_vwc: float | None = None,
    ) -> dict[str, Any]:
        """
        Update current weather. Called by scenario events before each day. [PDF-p3]

        Args:
            date:            ISO date string, e.g. "2026-05-12".
            temp_c:          Air temperature (°C).
            humidity_pct:    Relative humidity (%).
            wind_speed_ms:   Wind speed (m/s).
            rainfall_mm:     Daily precipitation (mm).
            solar_radiation: Solar radiation (W/m²).
            forecast:        Optional list of next-day dicts (same keys).
            avg_soil_vwc:    Optional average soil VWC from FarmWorldApp.
        """
        self._weather = WeatherState(
            date=date,
            temp_c=float(temp_c),
            humidity_pct=float(humidity_pct),
            wind_speed_ms=float(wind_speed_ms),
            rainfall_mm=float(rainfall_mm),
            solar_radiation=float(solar_radiation),
            forecast=forecast or [],
        )
        if avg_soil_vwc is not None:
            self._avg_soil_vwc = float(avg_soil_vwc)
        self.is_state_modified = True
        return {"status": "ok", "date": date}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def set_avg_soil_vwc(self, avg_vwc: float) -> dict[str, Any]:
        """
        Update the average soil VWC used for trafficability check.
        Called by scenario after advance_day to sync with FarmWorldApp. [设计]

        Args:
            avg_vwc: Average volumetric water content across all ridges.
        """
        self._avg_soil_vwc = float(avg_vwc)
        self.is_state_modified = True
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_flyable(self) -> bool:
        """Drone flight allowed: no rain AND wind < 12 m/s. [PDF-p7]"""
        return (
            self._weather.rainfall_mm == 0.0
            and self._weather.wind_speed_ms < _MAX_WIND_FLY_MS
        )

    def _is_sprayable(self) -> bool:
        """Pesticide spray allowed: no rain AND wind < 5 m/s. [PDF-p9]"""
        return (
            self._weather.rainfall_mm == 0.0
            and self._weather.wind_speed_ms < _MAX_WIND_SPRAY_MS
        )

    def _is_trafficable(self) -> bool:
        """Tractor field work allowed: avg soil VWC < 0.35. [PDF-p9]"""
        return self._avg_soil_vwc < _MAX_VWC_TRAFFIC

    def _current_weather_dict(self) -> dict[str, Any]:
        return {
            "date": self._weather.date,
            "temp_c": round(self._weather.temp_c, 2),
            "humidity_pct": round(self._weather.humidity_pct, 1),
            "wind_speed_ms": round(self._weather.wind_speed_ms, 2),
            "rainfall_mm": round(self._weather.rainfall_mm, 2),
            "solar_radiation": round(self._weather.solar_radiation, 1),
        }

    # Expose for device apps that hold a reference [设计]
    @property
    def is_flyable(self) -> bool:
        return self._is_flyable()

    @property
    def is_sprayable(self) -> bool:
        return self._is_sprayable()

    @property
    def is_trafficable(self) -> bool:
        return self._is_trafficable()

    @property
    def rainfall_mm(self) -> float:
        return self._weather.rainfall_mm
