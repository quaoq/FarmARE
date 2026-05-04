"""
FieldOpsApp - ridge-level irrigation and manual spot spray controls.

Manual operations advance the simulation clock immediately. Irrigation work
finishes right away, but the soil moisture response is only visible after a
follow-up delay, similar to the async charging notification flow.
"""
from __future__ import annotations

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

# Irrigation setup time per ridge (s) — valve open/close, hose connection
_IRRIGATION_SETUP_S    = 300
_IRRIGATION_S_PER_HOUR = 3600
_IRRIGATION_VWC_PER_HOUR = 0.05
_IRRIGATION_EFFECT_DELAY_S = 2 * 60 * 60

# Manual backpack sprayer speed (m/s) — operator walking pace
_MANUAL_SPRAY_SPEED_MS = 0.8
_MANUAL_SPRAY_SETUP_S  = 15    # fill/prepare sprayer
# Pesticide drawn from warehouse per manual ridge spray (L) — backpack
# is more thorough/targeted than the tractor boom (8 L/ridge).
_MANUAL_PESTICIDE_L_PER_RIDGE = 3.0


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
        # Forward the weather reference to FarmWorldApp so the physics
        # orchestrator can pull daily weather without depending on env discovery.
        farm_world_app.attach_weather_app(weather_app)

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
        if not 0 <= ridge_id < self._farm_world_app.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        if float(duration_hours) <= 0:
            return {"error": "duration_hours must be positive"}
        ridge = self._farm_world_app.get_ridge(ridge_id)
        if ridge.soil_vwc >= 0.30:
            return {"error": f"Ridge {ridge_id} soil VWC {ridge.soil_vwc:.3f} already >= 0.30"}
        return self._start_irrigation([ridge_id], float(duration_hours))

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.WRITE)
    def irrigate(self, start: int, end: int, hours: float) -> dict[str, Any]:
        """
        Irrigate a contiguous range of ridges.

        Round-3 / round-4 alias for ``irrigate_range`` with a shorter
        signature (``hours`` instead of ``duration_hours``). Behaves
        identically. Provided to match the scenario scaffolds that use
        the more concise name.

        Args:
            start: First ridge to irrigate (0-63).
            end:   Last ridge to irrigate (0-63, >= start).
            hours: Irrigation run time per ridge in hours.
        """
        if not 0 <= start <= end < self._farm_world_app.num_ridges:
            return {"error": f"Invalid ridge range [{start}, {end}]"}
        if float(hours) <= 0:
            return {"error": "hours must be positive"}
        for ridge_id in range(start, end + 1):
            ridge = self._farm_world_app.get_ridge(ridge_id)
            if ridge.soil_vwc >= 0.30:
                return {"error": f"Ridge {ridge_id} soil VWC {ridge.soil_vwc:.3f} already >= 0.30"}
        return self._start_irrigation(list(range(start, end + 1)), float(hours))

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
        if not 0 <= start <= end < self._farm_world_app.num_ridges:
            return {"error": f"Invalid ridge range [{start}, {end}]"}
        if float(duration_hours) <= 0:
            return {"error": "duration_hours must be positive"}
        for ridge_id in range(start, end + 1):
            ridge = self._farm_world_app.get_ridge(ridge_id)
            if ridge.soil_vwc >= 0.30:
                return {"error": f"Ridge {ridge_id} soil VWC {ridge.soil_vwc:.3f} already >= 0.30"}
        return self._start_irrigation(list(range(start, end + 1)), float(duration_hours))

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
        if not 0 <= ridge_id < self._farm_world_app.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        if not self._weather_app.is_sprayable:
            return {"error": "Weather conditions do not allow spraying (rain or wind >= 5 m/s)"}
        if not self._farm_world_app.consume_pesticide(_MANUAL_PESTICIDE_L_PER_RIDGE):
            return {
                "error": (
                    f"Insufficient pesticide in warehouse: "
                    f"need {_MANUAL_PESTICIDE_L_PER_RIDGE:.1f} L"
                )
            }

        self.time_manager.add_offset(_manual_spray_duration())
        self._farm_world_app.update_ridge_pesticide(ridge_id)

        self._register_manual_spray_with_physics(ridge_id=ridge_id)

        self._manual_spray_log.append({
            "ridge_id": ridge_id,
            "date": self._farm_world_app.get_state()["sim_date"],
            "method": "manual_backpack",
            "pesticide_used_liters": _MANUAL_PESTICIDE_L_PER_RIDGE,
            "duration_s": _manual_spray_duration(),
        })
        self.is_state_modified = True
        return {
            "status": "ok",
            "ridge_id": ridge_id,
            "pesticide_used_liters": _MANUAL_PESTICIDE_L_PER_RIDGE,
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

    def _start_irrigation(
        self, ridge_ids: list[int], duration_hours: float
    ) -> dict[str, Any]:
        pending = self._farm_world_app.get_pending_irrigation_for_ridges(ridge_ids)
        if pending:
            earliest = pending[0]
            return {
                "status": "irrigation_pending",
                "irrigated_ridges": [item["ridge_id"] for item in pending],
                "effect_ready_at": earliest["effect_ready_at"],
                "message": (
                    "Irrigation effect is still pending for part of this range. "
                    "Wait for the follow-up notification before rechecking soil moisture."
                ),
            }

        duration_s = _IRRIGATION_SETUP_S + int(duration_hours * _IRRIGATION_S_PER_HOUR)
        add_vwc = duration_hours * _IRRIGATION_VWC_PER_HOUR
        self._advance_linked_time(duration_s)
        effect_ready_at = float(self.time_manager.time()) + _IRRIGATION_EFFECT_DELAY_S

        if self._farm_world_app.physics_active:
            # Physics path: queue ManagementAction(IRRIGATION) so the soil
            # engine receives water on the next advance_physics_time call,
            # and record a FarmActionRecord. The legacy add_vwc bump is
            # bypassed (set_irrigation_pending not called).
            self._register_irrigation_with_physics(
                ridge_ids=ridge_ids,
                duration_hours=duration_hours,
                duration_s=duration_s,
                effect_ready_at=effect_ready_at,
            )
        else:
            for ridge_id in ridge_ids:
                self._farm_world_app.set_irrigation_pending(
                    ridge_id, add_vwc, effect_ready_at=effect_ready_at
                )
                self._irrigation_log.append(
                    {
                        "ridge_id": ridge_id,
                        "duration_hours": duration_hours,
                        "duration_s": duration_s,
                        "date": self._farm_world_app.get_state()["sim_date"],
                        "effect_ready_at": effect_ready_at,
                    }
                )

        self.is_state_modified = True
        response = {
            "status": "irrigation_started",
            "irrigated_ridges": list(ridge_ids),
            "duration_hours_per_ridge": duration_hours,
            "total_duration_minutes": round(duration_s / 60, 1),
            "effect_ready_at": effect_ready_at,
            "eta_minutes_to_confirmation": round(_IRRIGATION_EFFECT_DELAY_S / 60.0, 1),
            "message": (
                "Irrigation run finished. Recheck soil moisture after the "
                "2-hour follow-up notification."
            ),
        }
        if len(ridge_ids) == 1:
            response["ridge_id"] = ridge_ids[0]
            response["duration_hours"] = duration_hours
            response["duration_minutes"] = round(duration_s / 60, 1)
        return response

    def _advance_linked_time(self, offset_seconds: float) -> None:
        """
        Advance time for this app and directly linked farm/weather apps.

        In tests these apps often share references without being registered into
        an Environment, so they do not automatically share the same TimeManager.
        """
        self.time_manager.add_offset(offset_seconds)
        for app in (self._farm_world_app, self._weather_app):
            if app.time_manager is self.time_manager:
                continue
            app.time_manager.add_offset(offset_seconds)

    # ------------------------------------------------------------------
    # Physics integration helpers
    # ------------------------------------------------------------------

    def _register_irrigation_with_physics(
        self,
        ridge_ids: list[int],
        duration_hours: float,
        duration_s: int,
        effect_ready_at: float,
    ) -> None:
        import uuid

        from are.simulation.apps.farm_world.farm_action_record import FarmActionRecord
        from are.simulation.physics import ManagementAction, ManagementActionType

        # Approximate water delivered: legacy +0.05 VWC per hour matched a
        # ~5 mm/hour delivery in our top-zone soil engine. Keep that mapping
        # so scenarios calibrated to the legacy behaviour still produce a
        # comparable VWC bump after the soil engine processes the action.
        mm_per_hour = 5.0
        mm_per_ridge = float(duration_hours) * mm_per_hour

        physics = self._farm_world_app.physics
        for ridge_id in ridge_ids:
            physics.queue_management_action(
                ridge_id,
                ManagementAction(
                    action_type=ManagementActionType.IRRIGATION,
                    amount=mm_per_ridge,
                    quality=1.0,
                    metadata={"duration_hours": float(duration_hours)},
                ),
            )
            self._irrigation_log.append(
                {
                    "ridge_id": ridge_id,
                    "duration_hours": float(duration_hours),
                    "duration_s": duration_s,
                    "date": self._farm_world_app.get_state()["sim_date"],
                    "effect_ready_at": effect_ready_at,
                    "estimated_water_mm": mm_per_ridge,
                }
            )

        self._farm_world_app.record_action(
            FarmActionRecord(
                action_id=str(uuid.uuid4())[:8],
                timestamp=float(self.time_manager.time()),
                actor_app=self.name,
                action_type="irrigation",
                ridge_ids=list(ridge_ids),
                parameters={
                    "duration_hours": float(duration_hours),
                    "estimated_water_mm": mm_per_ridge,
                },
                direct_effect_summary={
                    "soil_water_input_registered": True,
                    "effect_ready_at": effect_ready_at,
                },
            )
        )

        # Apply the action immediately to soil via the orchestrator's
        # sub-daily path. The next time advance crosses a date boundary,
        # the daily cycle will pick up cumulative effects.
        self._farm_world_app.advance_physics_time()

    def _register_manual_spray_with_physics(self, ridge_id: int) -> None:
        if not self._farm_world_app.physics_active:
            return

        import uuid

        from are.simulation.apps.farm_world.farm_action_record import FarmActionRecord
        from are.simulation.physics import (
            ManagementAction,
            ManagementActionType,
            TreatmentApplication,
            TreatmentType,
        )

        physics = self._farm_world_app.physics
        physics.queue_treatment(
            ridge_id,
            TreatmentApplication(
                treatment_type=TreatmentType.INSECTICIDE,
                efficacy_multiplier=1.0,
            ),
        )
        physics.queue_management_action(
            ridge_id,
            ManagementAction(
                action_type=ManagementActionType.INSECTICIDE,
                amount=1.0,
                quality=1.0,
                metadata={"method": "manual_backpack"},
            ),
        )
        self._farm_world_app.record_action(
            FarmActionRecord(
                action_id=str(uuid.uuid4())[:8],
                timestamp=float(self.time_manager.time()),
                actor_app=self.name,
                action_type="insecticide",
                ridge_ids=[ridge_id],
                parameters={"method": "manual_backpack"},
                direct_effect_summary={
                    "treatment_type": "INSECTICIDE",
                    "residual_window_opened": True,
                },
            )
        )
        self._farm_world_app.advance_physics_time()
