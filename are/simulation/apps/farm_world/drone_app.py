"""
DroneApp - aerial drone operations for Farm-World.

Each DroneApp instance represents one physical drone (e.g. Mavic 3M, Matrice 4T).
Instantiate with drone-specific parameters (speed, height, effective_ridges_per_pass,
battery consumption, etc.).

All missions are synchronous: they complete immediately, advance the
simulation clock by the real-world flight duration, and consume battery.
Charging is asynchronous and completes after about 30 minutes of
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
from are.simulation.apps.farm_world.models import GrowthStage
from are.simulation.apps.farm_world.weather_app import WeatherApp
from are.simulation.tool_utils import OperationType, app_tool, data_tool
from are.simulation.types import event_registered
from are.simulation.utils.type_utils import type_check

_CHARGE_DURATION_S = 30 * 60


class DroneApp(App):
    """
    Aerial drone platform. Each instance represents one physical drone.

    Instantiate with drone-specific parameters::

        mavic = DroneApp(
            farm_world_app=farm,
            weather_app=weather,
            name="Mavic3M",
            speed_ms=5.0,
            height_m=30.0,
            effective_ridges_per_pass=7,
            takeoff_overhead_s=30,
            min_battery_pct=20.0,
            battery_pct_per_ridge=1.0,
        )
    """

    def __init__(
        self,
        farm_world_app: FarmWorldApp,
        weather_app: WeatherApp,
        *,
        name: str = "DroneApp",
        description: str = "",
        speed_ms: float = 5.0,
        effective_ridges_per_pass: int = 7,
        takeoff_overhead_s: int = 30,
        min_battery_pct: float = 20.0,
        battery_pct_per_ridge: float = 1.0,
    ) -> None:
        super().__init__(name=name)
        self._farm_world_app = farm_world_app
        self._weather_app = weather_app

        # Drone configuration (set at instantiation, read-only during sim)
        self.description = description
        self.speed_ms = float(speed_ms)
        self.effective_ridges_per_pass = int(effective_ridges_per_pass)
        self.takeoff_overhead_s = int(takeoff_overhead_s)
        self.min_battery_pct = float(min_battery_pct)
        self.battery_pct_per_ridge = float(battery_pct_per_ridge)

        # Runtime state
        self._battery_pct: float = 100.0
        self._charging: bool = False
        self._charge_started_at: float | None = None
        self._charge_complete_at: float | None = None
        self._charge_start_battery_pct: float | None = None
        self._mission_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # App interface
    # ------------------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        self._sync_charge_state()
        return {
            "app_name": self.name,
            "description": self.description,
            "battery_pct": round(self._battery_pct, 1),
            "charging": self._charging,
            "charge_session": self._serialize_charge_session(),
            "config": {
                "speed_ms": self.speed_ms,
                "effective_ridges_per_pass": self.effective_ridges_per_pass,
                "takeoff_overhead_s": self.takeoff_overhead_s,
                "min_battery_pct": self.min_battery_pct,
                "battery_pct_per_ridge": self.battery_pct_per_ridge,
            },
            "mission_log": list(self._mission_log),
        }

    def load_state(self, state_dict: dict[str, Any]) -> None:
        self._battery_pct = state_dict.get("battery_pct", 100.0)
        self._charging = state_dict.get("charging", False)
        charge_session = state_dict.get("charge_session") or {}
        self._charge_started_at = charge_session.get("started_at")
        self._charge_complete_at = charge_session.get("complete_at")
        self._charge_start_battery_pct = charge_session.get("start_battery_pct")
        self._mission_log = [dict(item) for item in state_dict.get("mission_log", [])]

    def reset(self) -> None:
        super().reset()
        self._battery_pct = 100.0
        self._charging = False
        self._charge_started_at = None
        self._charge_complete_at = None
        self._charge_start_battery_pct = None
        self._mission_log = []

    # ------------------------------------------------------------------
    # Agent tools
    # ------------------------------------------------------------------

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def fly_survey(self, start_ridge: int, end_ridge: int) -> dict[str, Any]:
        """
        Fly an aerial survey over a contiguous ridge range, collecting
        multispectral (NDVI) and thermal (canopy temperature) observations
        for every surveyed ridge.

        The drone covers ridges in passes; each pass covers a number of
        ridges determined by the drone's effective_ridges_per_pass setting.
        If battery runs low mid-flight the drone returns automatically and
        reports the ridges it managed to cover.

        Args:
            start_ridge: First ridge of the survey range (0-based).
            end_ridge:   Last ridge of the survey range (inclusive, >= start_ridge).
        """
        self._sync_charge_state()
        num_ridges = self._farm_world_app.num_ridges
        if not 0 <= start_ridge <= end_ridge < num_ridges:
            return {"error": f"Invalid ridge range [{start_ridge}, {end_ridge}]"}
        err = self._preflight_check()
        if err:
            return {"error": err}

        all_ridge_ids = list(range(start_ridge, end_ridge + 1))

        # Split into passes
        passes: list[list[int]] = []
        for i in range(0, len(all_ridge_ids), self.effective_ridges_per_pass):
            passes.append(all_ridge_ids[i : i + self.effective_ridges_per_pass])

        observations: list[dict[str, Any]] = []
        surveyed_ridges: list[int] = []
        total_battery_used = 0.0
        total_duration = self.takeoff_overhead_s
        aborted = False

        for pass_ridges in passes:
            pass_battery = round(len(pass_ridges) * self.battery_pct_per_ridge, 1)
            # Check if we have enough battery for this pass + safe return
            if self._battery_pct - pass_battery < self.min_battery_pct:
                aborted = True
                break

            # Fly this pass
            pass_duration = int(FIELD_LENGTH_M / self.speed_ms)
            self._battery_pct = round(self._battery_pct - pass_battery, 1)
            total_battery_used = round(total_battery_used + pass_battery, 1)
            total_duration += pass_duration

            for ridge_id in pass_ridges:
                ndvi = self._estimate_ndvi(ridge_id)
                canopy_temp = self._estimate_canopy_temp(ridge_id)
                self._farm_world_app.update_ridge_ndvi(ridge_id, ndvi)
                self._farm_world_app.update_ridge_canopy_temp(ridge_id, canopy_temp)
                observations.append({
                    "ridge_id": ridge_id,
                    "ndvi": ndvi,
                    "canopy_temp_c": canopy_temp,
                })
                surveyed_ridges.append(ridge_id)

        self.time_manager.add_offset(total_duration)

        mission_id = str(uuid.uuid4())[:8]
        self._mission_log.append({
            "mission_id": mission_id,
            "drone": self.name,
            "target_ridges": all_ridge_ids,
            "surveyed_ridges": surveyed_ridges,
            "battery_used_pct": total_battery_used,
            "duration_s": total_duration,
            "observations": observations,
            "aborted": aborted,
        })
        self.is_state_modified = True

        missed = [r for r in all_ridge_ids if r not in surveyed_ridges]
        result: dict[str, Any] = {
            "status": "partial" if aborted else "ok",
            "mission_id": mission_id,
            "surveyed_ridges": surveyed_ridges,
            "observations": observations,
            "duration_minutes": round(total_duration / 60, 1),
            "battery_remaining_pct": self._battery_pct,
        }
        if aborted:
            result["warning"] = (
                f"Battery low ({self._battery_pct}%), returned early. "
                f"Ridges not surveyed: {missed}"
            )
        return result

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def check_status(self) -> dict[str, Any]:
        """Return battery level, charging state, and drone configuration."""
        self._sync_charge_state()
        return {
            "drone": self.name,
            "description": self.description,
            "battery_pct": round(self._battery_pct, 1),
            "charging": self._charging,
            "eta_minutes_to_full": self._remaining_charge_minutes(),
            "speed_ms": self.speed_ms,
            "effective_ridges_per_pass": self.effective_ridges_per_pass,
        }

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def charge(self) -> dict[str, Any]:
        """
        Send this drone to its charging station.

        Charging completes asynchronously after about 30 minutes of
        environment time. The battery level increases gradually while charging.
        """
        self._sync_charge_state()
        if self._charging:
            return {
                "status": "charging",
                "drone": self.name,
                "battery_pct": round(self._battery_pct, 1),
                "eta_minutes_to_full": self._remaining_charge_minutes(),
                "message": "Already charging.",
            }
        if self._battery_pct >= 100.0:
            return {
                "status": "already_full",
                "drone": self.name,
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
            "drone": self.name,
            "battery_pct": round(self._battery_pct, 1),
            "eta_minutes_to_full": 30.0,
            "charge_complete_at": self._charge_complete_at,
            "message": "Charging started. Estimated full charge in about 30 minutes.",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _preflight_check(self) -> str | None:
        self._sync_charge_state()
        if self._battery_pct < self.min_battery_pct:
            return f"Battery {self._battery_pct}% below minimum {self.min_battery_pct}%"
        if self._charging:
            return "Drone is currently charging"
        if not self._weather_app.is_flyable:
            return "Weather conditions do not allow flight (rain or wind >= 12 m/s)"
        return None

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
        if (
            self._charge_started_at is None
            or self._charge_complete_at is None
            or self._charge_start_battery_pct is None
        ):
            return None
        return {
            "started_at": self._charge_started_at,
            "complete_at": self._charge_complete_at,
            "start_battery_pct": self._charge_start_battery_pct,
        }

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
