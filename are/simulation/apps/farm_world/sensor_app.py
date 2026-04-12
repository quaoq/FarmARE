"""
SensorApp - fixed sensor infrastructure for Farm-World.

The app stores cached soil, canopy, and weather-station readings. Agent-facing
tools add small deterministic noise to mimic sensor uncertainty. [设计]
"""
from __future__ import annotations

from typing import Any

from are.simulation.apps.app import App
from are.simulation.apps.farm_world.farm_world_app import NUM_RIDGES
from are.simulation.tool_utils import OperationType, app_tool, data_tool, env_tool
from are.simulation.types import EventType, event_registered
from are.simulation.utils.type_utils import type_check

_SOIL_SENSOR_RIDGES = [5, 15, 25, 38, 48, 58]
_CANOPY_SENSOR_RIDGES = [5, 15, 25, 38, 48, 58]


class SensorApp(App):
    """Six soil probes, six canopy sensors, and one weather station."""

    def __init__(self) -> None:
        super().__init__(name="SensorApp")
        self._soil_sensors = self._default_soil_sensors()
        self._canopy_sensors = self._default_canopy_sensors()
        self._weather_station = self._default_weather_station()

    def get_state(self) -> dict[str, Any]:
        return {
            "app_name": self.name,
            "soil_sensors": [dict(sensor) for sensor in self._soil_sensors],
            "canopy_sensors": [dict(sensor) for sensor in self._canopy_sensors],
            "weather_station": dict(self._weather_station),
        }

    def load_state(self, state_dict: dict[str, Any]) -> None:
        self._soil_sensors = [dict(sensor) for sensor in state_dict["soil_sensors"]]
        self._canopy_sensors = [dict(sensor) for sensor in state_dict["canopy_sensors"]]
        self._weather_station = dict(state_dict["weather_station"])

    def reset(self) -> None:
        super().reset()
        self._soil_sensors = self._default_soil_sensors()
        self._canopy_sensors = self._default_canopy_sensors()
        self._weather_station = self._default_weather_station()

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def read_soil_sensors(self) -> dict[str, Any]:
        """
        Return the latest readings from the six fixed soil probes across the field.

        Each probe reports volumetric water content (vwc) and soil temperature (temp_c)
        at 5 cm depth. Probes are located at ridges 5, 15, 25, 38, 48, and 58.
        """
        readings = []
        for sensor in self._soil_sensors:
            reading = dict(sensor)
            reading["vwc"] = round(
                max(0.0, min(1.0, sensor["vwc"] + self.rng.uniform(-0.01, 0.01))), 4
            )
            reading["temp_c"] = round(sensor["temp_c"] + self.rng.uniform(-0.3, 0.3), 2)
            readings.append(reading)
        return {"soil_sensors": readings}

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def read_canopy_sensors(self) -> dict[str, Any]:
        """
        Return the latest readings from the six fixed canopy sensors across the field.

        Each sensor reports an ndvi_proxy value (0.0–1.0). A value of -1 means
        no valid reading yet. Sensors are co-located with the soil probes at
        ridges 5, 15, 25, 38, 48, and 58.
        """
        readings = []
        for sensor in self._canopy_sensors:
            reading = dict(sensor)
            if sensor["ndvi_proxy"] >= 0.0:
                noisy = sensor["ndvi_proxy"] + self.rng.uniform(-0.02, 0.02)
                reading["ndvi_proxy"] = round(max(0.0, min(1.0, noisy)), 3)
            readings.append(reading)
        return {"canopy_sensors": readings}

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def read_weather_station(self) -> dict[str, Any]:
        """Return the latest reading from the on-farm weather station."""
        return dict(self._weather_station)

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def update_soil_readings(self, ridge_id: int, vwc: float, temp_c: float) -> dict[str, Any]:
        """Update the cached soil sensor for the given ridge if one exists."""
        if not 0 <= ridge_id < NUM_RIDGES:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        sensor = self._find_sensor(self._soil_sensors, ridge_id)
        if sensor is None:
            return {"status": "ignored", "reason": "no_sensor_on_ridge"}
        sensor["vwc"] = float(vwc)
        sensor["temp_c"] = float(temp_c)
        sensor["last_updated"] = self._weather_station["date"]
        self.is_state_modified = True
        return {"status": "ok", "sensor_id": sensor["sensor_id"]}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def update_canopy_readings(self, ridge_id: int, ndvi: float) -> dict[str, Any]:
        """Update the cached canopy sensor for the given ridge if one exists."""
        if not 0 <= ridge_id < NUM_RIDGES:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        sensor = self._find_sensor(self._canopy_sensors, ridge_id)
        if sensor is None:
            return {"status": "ignored", "reason": "no_sensor_on_ridge"}
        sensor["ndvi_proxy"] = float(ndvi)
        sensor["last_updated"] = self._weather_station["date"]
        self.is_state_modified = True
        return {"status": "ok", "sensor_id": sensor["sensor_id"]}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def update_weather_readings(self, weather: dict[str, Any]) -> dict[str, Any]:
        """Update the cached weather-station reading from WeatherApp data."""
        self._weather_station = {
            "date": weather.get("date", self._weather_station["date"]),
            "temp_c": float(weather.get("temp_c", self._weather_station["temp_c"])),
            "humidity_pct": float(
                weather.get("humidity_pct", self._weather_station["humidity_pct"])
            ),
            "wind_speed_ms": float(
                weather.get("wind_speed_ms", self._weather_station["wind_speed_ms"])
            ),
            "rainfall_mm": float(
                weather.get("rainfall_mm", self._weather_station["rainfall_mm"])
            ),
            "solar_radiation": float(
                weather.get(
                    "solar_radiation", self._weather_station["solar_radiation"]
                )
            ),
        }
        self.is_state_modified = True
        return {"status": "ok"}

    def _default_soil_sensors(self) -> list[dict[str, Any]]:
        return [
            {
                "sensor_id": f"S{i + 1}",
                "ridge_id": ridge_id,
                "vwc": 0.22,
                "temp_c": 10.0,
                "last_updated": "2026-04-25",
            }
            for i, ridge_id in enumerate(_SOIL_SENSOR_RIDGES)
        ]

    def _default_canopy_sensors(self) -> list[dict[str, Any]]:
        return [
            {
                "sensor_id": f"C{i + 1}",
                "ridge_id": ridge_id,
                "ndvi_proxy": -1.0,
                "last_updated": "2026-04-25",
            }
            for i, ridge_id in enumerate(_CANOPY_SENSOR_RIDGES)
        ]

    def _default_weather_station(self) -> dict[str, Any]:
        return {
            "date": "2026-04-25",
            "temp_c": 15.0,
            "humidity_pct": 55.0,
            "wind_speed_ms": 2.0,
            "rainfall_mm": 0.0,
            "solar_radiation": 380.0,
        }

    def _find_sensor(
        self, sensors: list[dict[str, Any]], ridge_id: int
    ) -> dict[str, Any] | None:
        for sensor in sensors:
            if sensor["ridge_id"] == ridge_id:
                return sensor
        return None
