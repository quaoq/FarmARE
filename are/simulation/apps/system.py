# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.


from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from are.simulation.apps.app import App
from are.simulation.tool_utils import OperationType, app_tool
from are.simulation.types import event_registered
from are.simulation.utils import type_check


@dataclass
class WaitForNotificationTimeout:
    time_created: float
    timeout: int = 0
    timeout_timestamp: float = field(init=False)

    def __str__(self):
        return f"Wait for even timeout after {self.timeout} seconds"

    def __post_init__(self):
        self.timeout_timestamp = self.time_created + self.timeout


class SystemApp(App):
    def __init__(
        self,
        name: str | None = None,
    ):
        super().__init__(name)
        self.wait_for_notification_timeout = None
        self.wait_for_next_notification: Callable = lambda: None  # type: ignore # Will be set by Environment during registration
        # Optional reference to the FarmWorldApp so advance_time can trigger
        # the physics orchestrator after a time jump. Wired by FarmWorldApp's
        # attach_system_app(...) helper, called from scenario init.
        self._farm_world_app = None

    # Not @app_tool() because the agent should not be able to modify the time.
    # If the agent calls SystemApp__wait(), we should increment env.tick_count accordingly. Right now, when calling wait(3), env.tick_count is incremented only once, not 3 times.
    @type_check
    @event_registered(operation_type=OperationType.READ)
    def wait(self, time: int = 0) -> None:
        """
        Wait a given amount of time, only to be used when you have absolutely nothing to do.
        :param time: Amount of time to wait in seconds
        """
        assert time >= 0, "Time must be non-negative"
        self.time_manager.add_offset(time)

    @app_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_current_time(self) -> dict:
        """
        Get the current time, date and weekday, returned in a dict, timestamp as float (epoch), datetime as string (YYYY-MM-DD HH:MM:SS), weekday as string (Monday, Tuesday, etc.).
        :returns: a dictionary with the keys "current_timestamp" (current time as timestamp), "current_datetime" (current time as datetime), "current_weekday" (current weekday)
        """
        timestamp = self.time_manager.time()
        date = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return {
            "current_timestamp": timestamp,
            "current_datetime": date.strftime("%Y-%m-%d %H:%M:%S"),
            "current_weekday": date.strftime("%A"),
        }

    def get_wait_for_notification_timeout(self) -> WaitForNotificationTimeout | None:
        """
        Return the current wait for event timeout, if any.
        """
        if (
            self.wait_for_notification_timeout
            and self.time_manager.time()
            >= self.wait_for_notification_timeout.timeout_timestamp
        ):
            return self.wait_for_notification_timeout
        return None

    def reset_wait_for_notification_timeout(self):
        self.wait_for_notification_timeout = None

    def attach_farm_world_app(self, farm_world_app) -> None:
        """Forward a FarmWorldApp reference so advance_time can trigger physics."""
        self._farm_world_app = farm_world_app

    @type_check
    @app_tool()
    @event_registered(operation_type=OperationType.READ)
    def advance_time(
        self,
        seconds: int = 0,
        minutes: int = 0,
        hours: int = 0,
        days: int = 0,
    ) -> dict:
        """
        Advance the simulation clock by a specified amount of time.

        This is the canonical "fast-forward" tool. Use it when waiting for
        biological or physical processes (crop growth, soil drying, treatment
        residual decay, post-harvest drying) to play out. After the time
        advances, any subsequent state read will reflect the evolved world.

        Args:
            seconds: Seconds to advance.
            minutes: Minutes to advance.
            hours:   Hours to advance.
            days:    Days to advance.

        Returns:
            A dict containing the new current time.
        """
        total_seconds = (
            int(seconds)
            + int(minutes) * 60
            + int(hours) * 3600
            + int(days) * 86400
        )
        if total_seconds <= 0:
            return {"error": "advance_time amount must be > 0"}
        farm_world_app = self._farm_world_app
        if farm_world_app is not None:
            try:
                prepare = getattr(farm_world_app, "prepare_for_time_advance", None)
                if callable(prepare):
                    prepare(float(self.time_manager.time()))
            except Exception:  # pragma: no cover — defensive
                pass
        self.time_manager.add_offset(total_seconds)
        # Propagate to attached FarmWorldApp + WeatherApp time managers so
        # apps that aren't sharing the env's time_manager (e.g., in unit
        # tests) still see the same clock; mirror FieldOpsApp's existing
        # _advance_linked_time pattern.
        if farm_world_app is not None:
            try:
                fw_tm = getattr(farm_world_app, "time_manager", None)
                if fw_tm is not None and fw_tm is not self.time_manager:
                    fw_tm.add_offset(total_seconds)
                weather_app = getattr(farm_world_app, "_weather_app", None)
                if weather_app is not None:
                    w_tm = getattr(weather_app, "time_manager", None)
                    if w_tm is not None and w_tm is not self.time_manager and w_tm is not fw_tm:
                        w_tm.add_offset(total_seconds)
                # Trigger physics orchestrator (idempotent, no-ops when inactive).
                farm_world_app.advance_physics_time()
            except Exception:  # pragma: no cover — defensive
                pass
        timestamp = self.time_manager.time()
        return {
            "status": "ok",
            "advanced_seconds": total_seconds,
            "current_timestamp": timestamp,
            "current_datetime": datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }

    @app_tool()
    @event_registered(operation_type=OperationType.READ)
    def wait_for_notification(
        self,
        timeout: int = 0,
    ) -> None:
        """
        Wait for a specified amount of time or until the next notification or user message is received, whichever comes first.
        This method should only be used when there are no other tasks to perform.
        :param timeout: The maximum amount of time to wait in seconds. If a notification is received before this time elapses, the wait will end early.
        """
        # This method efficiently jumps from event to event without advancing time incrementally, making it more efficient than wait_for_notification.
        timeout = int(timeout)
        assert timeout >= 0, "Timeout must be non-negative"
        # Create a new timeout object that will be used by the notification system.
        # It will create a notification after the timeout is reached if no other notification is received in between.
        self.wait_for_notification_timeout = WaitForNotificationTimeout(
            timeout=int(timeout), time_created=self.time_manager.time()
        )

        # Signal the environment to enter wait for notification mode
        if self.wait_for_next_notification is not None:
            self.wait_for_next_notification()
