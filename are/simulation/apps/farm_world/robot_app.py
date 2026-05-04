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
        farm_world_app.attach_weather_app(weather_app)

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
    @event_registered(operation_type=OperationType.WRITE)
    def inspect_pests(self, start_ridge: int, end_ridge: int) -> dict[str, Any]:
        """
        Walk a contiguous ridge range, returning ground-level pest detections.

        Routes through the ObservationModel — results are noisy and may include
        false positives/negatives. Coverage is capped (typically the first
        few ridges in the range due to robot speed and battery). Use after
        an aerial drone survey flagged an anomaly to confirm cause.

        Args:
            start_ridge: First ridge of the range (0-63).
            end_ridge:   Last ridge (inclusive, >= start_ridge).
        """
        return self._inspect_range(start_ridge, end_ridge, modality="pest")

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def inspect_crop_health(self, start_ridge: int, end_ridge: int) -> dict[str, Any]:
        """
        Walk a contiguous ridge range, returning ground-level pest + disease detections
        plus visible canopy health diagnostics.

        Use this when you need to discriminate among nutrient stress, drought
        stress, and biotic causes of low NDVI. The robot can return both pest
        and disease confidence scores per ridge in one pass.

        Args:
            start_ridge: First ridge of the range (0-63).
            end_ridge:   Last ridge (inclusive, >= start_ridge).
        """
        return self._inspect_range(start_ridge, end_ridge, modality="crop_health")

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def inspect_emergence(self, start_ridge: int, end_ridge: int) -> dict[str, Any]:
        """
        Walk a contiguous ridge range, returning per-ridge emergence stand
        estimates (fraction of expected plants visible).

        Used in the early-season emergence-check loop. Stand fraction below
        0.7 typically indicates a replant decision.

        Args:
            start_ridge: First ridge of the range (0-63).
            end_ridge:   Last ridge (inclusive, >= start_ridge).
        """
        return self._inspect_range(start_ridge, end_ridge, modality="emergence")

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

    def _inspect_range(
        self, start_ridge: int, end_ridge: int, modality: str
    ) -> dict[str, Any]:
        """Walk a ridge range, returning observation-model-derived detections.

        Used by inspect_pests / inspect_crop_health / inspect_emergence. The
        robot's coverage is limited (battery + speed); only the first few
        ridges are visited per pass. Falls back to legacy hidden-truth reads
        when physics is inactive.
        """
        from datetime import datetime, timezone

        self._sync_charge_state()
        if not 0 <= start_ridge <= end_ridge < self._farm_world_app.num_ridges:
            return {"error": f"Invalid ridge range [{start_ridge}, {end_ridge}]"}
        if self._battery_pct < 15.0:
            return {"error": f"Battery {self._battery_pct}% below minimum 15%"}
        if self._charging:
            return {"error": "Robot is currently charging"}

        wet_ground = self._weather_app.rainfall_mm > 0.0
        ridges = list(range(start_ridge, end_ridge + 1))
        # Coverage cap (typical ~5 ridges per pass on this scale of robot).
        max_covered = 8
        covered_ridges = ridges[:max_covered]
        battery_used = round(_BATTERY_PER_INSPECTION * len(covered_ridges) / 4.0, 1)
        if self._battery_pct - battery_used < 10.0:
            covered_ridges = covered_ridges[: max(1, int(self._battery_pct / battery_used * len(covered_ridges)))]
            battery_used = round(_BATTERY_PER_INSPECTION * len(covered_ridges) / 4.0, 1)
        self._battery_pct = round(self._battery_pct - battery_used, 1)
        duration = _inspect_duration() * len(covered_ridges)
        self.time_manager.add_offset(duration)

        # Build per-ridge observations.
        per_ridge: dict[int, dict[str, Any]] = {}
        if self._farm_world_app.physics_active:
            self._farm_world_app.advance_physics_time()
            physics = self._farm_world_app.physics
            from are.simulation.physics import HiddenRidgeTruth

            today = datetime.fromtimestamp(
                float(self.time_manager.time()), tz=timezone.utc
            ).date()
            truth_by_ridge = {}
            for rid in covered_ridges:
                soil = physics.soil.states[rid]
                canopy = physics.canopy.states[rid]
                biotic = physics.biotic.states[rid]
                management = physics.management.states[rid]
                truth_by_ridge[rid] = HiddenRidgeTruth(
                    ridge_id=rid,
                    top_vwc=soil.top_vwc,
                    root_vwc=soil.root_vwc,
                    top_temp_c=soil.top_temp_c,
                    ndvi_proxy=canopy.ndvi_proxy if canopy.initialized else 0.18,
                    lai=canopy.lai,
                    canopy_cover=canopy.canopy_cover,
                    canopy_temp_c=soil.top_temp_c + 2.0,
                    weed_pressure=biotic.weed_pressure,
                    insect_pressure=biotic.insect_pressure,
                    disease_pressure=biotic.disease_pressure,
                    nutrient_index=management.nutrient_index,
                )
            products = physics.observation_model.observe_ground_inspection(
                day=today,
                truth_by_ridge=truth_by_ridge,
                ridge_ids=covered_ridges,
                asset_id=self.name,
            )
            # First product is pest, second is disease per observation_model.observe_ground_inspection.
            pest_values = products[0].values if products else {}
            disease_values = products[1].values if len(products) > 1 else {}
            for rid in covered_ridges:
                pest = pest_values.get(rid, {})
                disease = disease_values.get(rid, {})
                stand_fraction = (
                    physics.management.states[rid].stand_fraction
                    if physics.management.states[rid].planted
                    else 0.0
                )
                per_ridge[rid] = {
                    "ridge_id": rid,
                    "pest_present": pest.get("pest_present", False),
                    "pest_confidence": pest.get("confidence", 0.0),
                    "disease_present": disease.get("disease_present", False),
                    "disease_confidence": disease.get("confidence", 0.0),
                    "stand_fraction": round(float(stand_fraction), 3),
                }
        else:
            # Legacy fallback: read hidden ridge fields directly with mock confidence.
            for rid in covered_ridges:
                ridge = self._farm_world_app.get_ridge(rid)
                per_ridge[rid] = {
                    "ridge_id": rid,
                    "pest_present": ridge.pest_pressure >= 0.2,
                    "pest_confidence": 0.8 if ridge.pest_pressure >= 0.2 else 0.4,
                    "disease_present": ridge.disease_pressure >= 0.2,
                    "disease_confidence": 0.8 if ridge.disease_pressure >= 0.2 else 0.4,
                    "stand_fraction": 1.0 if ridge.planted else 0.0,
                }

        # Trim per-modality summary returned to the agent.
        result: dict[str, Any] = {
            "status": "ok",
            "modality": modality,
            "covered_ridges": covered_ridges,
            "uncovered_ridges": ridges[len(covered_ridges):],
            "battery_remaining_pct": self._battery_pct,
            "observations": per_ridge,
        }
        if wet_ground:
            result["warning"] = "ground_wet, mobility_reduced"
        inspection_id = str(uuid.uuid4())[:8]
        self._inspection_log.append({
            "inspection_id": inspection_id,
            "robot": self.name,
            "modality": modality,
            "covered_ridges": covered_ridges,
            "duration_s": duration,
        })
        result["inspection_id"] = inspection_id
        self.is_state_modified = True
        return result
