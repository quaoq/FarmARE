"""
SensorApp - fixed sensor infrastructure for Farm-World.

Manages 6 soil probes (VWC + temperature) and 6 canopy sensors (NDVI proxy),
located at ridges 5/15/25/38/48/58. [PDF-p3]

Each sensor covers a contiguous range of ridges:
  S1/C1: ridge 0-10   (installed at ridge 5)
  S2/C2: ridge 11-21  (installed at ridge 15)
  S3/C3: ridge 22-32  (installed at ridge 25)
  S4/C4: ridge 33-43  (installed at ridge 38)
  S5/C5: ridge 44-53  (installed at ridge 48)
  S6/C6: ridge 54-63  (installed at ridge 58)

Agent-facing tools add small random noise to mimic sensor uncertainty. [设计]
Scenario events update cached readings via @env_tool methods.

Weather station data is NOT managed here — use WeatherApp.get_current_weather().
"""
from __future__ import annotations

from typing import Any

from are.simulation.apps.app import App
from are.simulation.apps.farm_world.farm_world_app import FarmWorldApp
from are.simulation.tool_utils import OperationType, app_tool, data_tool, env_tool
from are.simulation.types import EventType, event_registered
from are.simulation.utils.type_utils import type_check

# (sensor_id, installed_ridge, ridge_start, ridge_end)
_SENSOR_ZONES: list[tuple[str, int, int, int]] = [
    ("1", 5,  0,  10),
    ("2", 15, 11, 21),
    ("3", 25, 22, 32),
    ("4", 38, 33, 43),
    ("5", 48, 44, 53),
    ("6", 58, 54, 63),
]


class SensorApp(App):
    """Six soil probes and six canopy sensors, each covering a ridge zone."""

    def __init__(self, farm_world_app: FarmWorldApp) -> None:
        super().__init__(name="SensorApp")
        self._farm_world_app = farm_world_app
        self._soil_sensors = self._default_soil_sensors()
        self._canopy_sensors = self._default_canopy_sensors()

    # ------------------------------------------------------------------
    # App interface
    # ------------------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        return {
            "app_name": self.name,
            "soil_sensors": [dict(s) for s in self._soil_sensors],
            "canopy_sensors": [dict(s) for s in self._canopy_sensors],
        }

    def load_state(self, state_dict: dict[str, Any]) -> None:
        self._soil_sensors = [dict(s) for s in state_dict["soil_sensors"]]
        self._canopy_sensors = [dict(s) for s in state_dict["canopy_sensors"]]

    def reset(self) -> None:
        super().reset()
        self._soil_sensors = self._default_soil_sensors()
        self._canopy_sensors = self._default_canopy_sensors()

    # ------------------------------------------------------------------
    # Agent tools
    # ------------------------------------------------------------------

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def read_soil_sensors(self) -> dict[str, Any]:
        """
        Return the latest readings from the six fixed soil probes.

        Each probe reports volumetric water content (vwc) and soil temperature
        (temp_c) at 5 cm depth. The reading represents the average condition
        of the ridges within the sensor's coverage zone.

        Sensor zones:
          S1: ridges 0-10,  S2: ridges 11-21, S3: ridges 22-32,
          S4: ridges 33-43, S5: ridges 44-53, S6: ridges 54-63.
        """
        self._sync_sensors()
        readings = []
        for sensor in self._soil_sensors:
            reading = dict(sensor)
            reading["vwc"] = round(
                max(0.0, min(1.0, sensor["vwc"] + self.rng.uniform(-0.01, 0.01))),
                4,
            )
            reading["temp_c"] = round(
                sensor["temp_c"] + self.rng.uniform(-0.3, 0.3), 2
            )
            readings.append(reading)
        return {"soil_sensors": readings}

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def read_canopy_sensors(self) -> dict[str, Any]:
        """
        Return the latest readings from the six fixed canopy sensors.

        Each sensor reports an ndvi_proxy value (0.0-1.0). A value of -1
        means no valid reading yet (pre-emergence). The reading represents
        the average canopy condition within the sensor's coverage zone.

        Sensor zones:
          C1: ridges 0-10,  C2: ridges 11-21, C3: ridges 22-32,
          C4: ridges 33-43, C5: ridges 44-53, C6: ridges 54-63.
        """
        self._sync_sensors()
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
    def read_soil_sensor(self, sensor_id: str) -> dict[str, Any]:
        """
        Return the latest reading from a single soil probe.

        Args:
            sensor_id: "S1" through "S6".
        """
        self._sync_sensors()
        sensor = self._find_soil(sensor_id)
        if sensor is None:
            return {"error": f"Unknown sensor_id '{sensor_id}'"}
        reading = dict(sensor)
        reading["vwc"] = round(
            max(0.0, min(1.0, sensor["vwc"] + self.rng.uniform(-0.01, 0.01))), 4
        )
        reading["temp_c"] = round(sensor["temp_c"] + self.rng.uniform(-0.3, 0.3), 2)
        return reading

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def read_canopy_sensor(self, sensor_id: str) -> dict[str, Any]:
        """
        Return the latest reading from a single canopy sensor.

        Args:
            sensor_id: "C1" through "C6".
        """
        self._sync_sensors()
        sensor = self._find_canopy(sensor_id)
        if sensor is None:
            return {"error": f"Unknown sensor_id '{sensor_id}'"}
        reading = dict(sensor)
        if sensor["ndvi_proxy"] >= 0.0:
            noisy = sensor["ndvi_proxy"] + self.rng.uniform(-0.02, 0.02)
            reading["ndvi_proxy"] = round(max(0.0, min(1.0, noisy)), 3)
        return reading

    # ------------------------------------------------------------------
    # Environment tools — called by scenario to sync sensor caches
    # ------------------------------------------------------------------

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def update_soil_sensor(
        self, sensor_id: str, vwc: float, temp_c: float
    ) -> dict[str, Any]:
        """
        Update a soil sensor's cached reading.

        Args:
            sensor_id: "S1" through "S6".
            vwc:       Volumetric water content (0.0-1.0).
            temp_c:    Soil temperature at 5 cm depth (°C).
        """
        sensor = self._find_soil(sensor_id)
        if sensor is None:
            return {"error": f"Unknown sensor_id '{sensor_id}'"}
        sensor["vwc"] = float(vwc)
        sensor["temp_c"] = float(temp_c)
        self.is_state_modified = True
        return {"status": "ok", "sensor_id": sensor_id}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def update_canopy_sensor(
        self, sensor_id: str, ndvi_proxy: float
    ) -> dict[str, Any]:
        """
        Update a canopy sensor's cached reading.

        Args:
            sensor_id:  "C1" through "C6".
            ndvi_proxy: NDVI proxy value (0.0-1.0, or -1 for no reading).
        """
        sensor = self._find_canopy(sensor_id)
        if sensor is None:
            return {"error": f"Unknown sensor_id '{sensor_id}'"}
        sensor["ndvi_proxy"] = float(ndvi_proxy)
        self.is_state_modified = True
        return {"status": "ok", "sensor_id": sensor_id}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_soil(self, sensor_id: str) -> dict[str, Any] | None:
        for s in self._soil_sensors:
            if s["sensor_id"] == sensor_id:
                return s
        return None

    def _find_canopy(self, sensor_id: str) -> dict[str, Any] | None:
        for s in self._canopy_sensors:
            if s["sensor_id"] == sensor_id:
                return s
        return None

    def _default_soil_sensors(self) -> list[dict[str, Any]]:
        return [
            {
                "sensor_id": f"S{sid}",
                "ridge_id": ridge,
                "ridge_start": start,
                "ridge_end": end,
                "vwc": 0.22,
                "temp_c": 10.0,
            }
            for sid, ridge, start, end in _SENSOR_ZONES
        ]

    def _sync_sensors(self) -> None:
        """Refresh sensor caches from current world state.

        Physics-active mode (preferred): pulls top_vwc / top_temp_c from the
        soil engine's truth, and canopy NDVI from the canopy/biomass engine's
        ndvi_proxy. The orchestrator's compatibility-sync already mirrors
        these onto RidgeState, but reading the engine values directly avoids
        any drift between sync points. Calling advance_physics_time first
        guarantees the agent sees the latest state.

        Legacy mode: averages RidgeState fields by zone, exactly as before.
        """
        if self._farm_world_app.physics_active:
            self._farm_world_app.advance_physics_time()
            physics = self._farm_world_app.physics

            for s in self._soil_sensors:
                sid, rs, re = s["sensor_id"], s["ridge_start"], s["ridge_end"]
                soil_states = [physics.soil.states[r] for r in range(rs, re + 1)]
                avg_vwc = sum(st.top_vwc for st in soil_states) / len(soil_states)
                avg_temp = sum(st.top_temp_c for st in soil_states) / len(soil_states)
                self.update_soil_sensor(sid, avg_vwc, avg_temp)

            for s in self._canopy_sensors:
                sid, rs, re = s["sensor_id"], s["ridge_start"], s["ridge_end"]
                canopy_states = [physics.canopy.states[r] for r in range(rs, re + 1)]
                # Pre-emergence canopy state has initialized=False; report -1
                # to match the legacy "no valid reading yet" semantic.
                if any(not st.initialized for st in canopy_states):
                    self.update_canopy_sensor(sid, -1.0)
                else:
                    avg_ndvi = sum(st.ndvi_proxy for st in canopy_states) / len(canopy_states)
                    self.update_canopy_sensor(sid, avg_ndvi)
            return

        # Legacy path
        for s in self._soil_sensors:
            sid, rs, re = s["sensor_id"], s["ridge_start"], s["ridge_end"]
            ridges = [self._farm_world_app.get_ridge(r) for r in range(rs, re + 1)]
            avg_vwc = sum(r.soil_vwc for r in ridges) / len(ridges)
            avg_temp = sum(r.soil_temp_c for r in ridges) / len(ridges)
            self.update_soil_sensor(sid, avg_vwc, avg_temp)

        for s in self._canopy_sensors:
            sid, rs, re = s["sensor_id"], s["ridge_start"], s["ridge_end"]
            ridges = [self._farm_world_app.get_ridge(r) for r in range(rs, re + 1)]
            avg_ndvi = sum(r.ndvi for r in ridges) / len(ridges)
            self.update_canopy_sensor(sid, avg_ndvi)

    def _default_canopy_sensors(self) -> list[dict[str, Any]]:
        return [
            {
                "sensor_id": f"C{sid}",
                "ridge_id": ridge,
                "ridge_start": start,
                "ridge_end": end,
                "ndvi_proxy": -1.0,
            }
            for sid, ridge, start, end in _SENSOR_ZONES
        ]
