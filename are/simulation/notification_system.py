# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.


import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any

from are.simulation.agents.multimodal import Attachment
from are.simulation.apps.agent_user_interface import AUIMessage, Sender
from are.simulation.apps.reminder import Reminder, ReminderApp
from are.simulation.apps.system import SystemApp, WaitForNotificationTimeout
from are.simulation.priority_queue import PriorityQueue
from are.simulation.time_manager import TimeManager
from are.simulation.types import AbstractEvent, Action, CompletedEvent, EventType
from are.simulation.validation.constants import APP_ALIAS


class MessageType(Enum):
    USER_MESSAGE = "USER_MESSAGE"
    ENVIRONMENT_NOTIFICATION = "ENVIRONMENT_NOTIFICATION"
    ENVIRONMENT_STOP = "ENVIRONMENT_STOP"


class VerbosityLevel(IntEnum):
    """Defines the verbosity levels for the notification system.
    LOW: Only user messages and system notifications (due reminders, wait for notification timeouts) are notified.
    MEDIUM: ENV events that are possible consequences of Agent actions are notified.
    HIGH: Most ENV events are notified, even independent ENV events that are not caused by Agent actions.
    """

    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass
class Message:
    message_type: MessageType
    message: str
    timestamp: datetime
    attachments: list[Attachment] = field(default_factory=list)


class AbstractMessageQueue:
    @abc.abstractmethod
    def put(self, message: Message) -> None:
        pass

    @abc.abstractmethod
    def get_by_timestamp(self, timestamp: datetime) -> list[Message]:
        pass


class MessageQueue(AbstractMessageQueue):
    def __init__(self):
        self.messages = PriorityQueue[Message](fields=["timestamp"])

    def put(self, message: Message) -> None:
        self.messages.put(message)

    def get_by_timestamp(self, timestamp: datetime) -> list[Message]:
        extracted_messages = []
        remaining_messages = []

        while not self.messages.empty():
            event = self.messages.get()
            if event.timestamp <= timestamp:
                extracted_messages.append(event)
            else:
                remaining_messages.append(event)
                break  # since events are ordered, no need to check further

        # Reinsert the remaining items back into the queue
        for item in remaining_messages:
            self.messages.put(item)

        return extracted_messages

    def has_environment_stop_message(self) -> bool:
        return any(
            message.message_type == MessageType.ENVIRONMENT_STOP
            for message in self.list_view()
        )

    def list_view(self) -> list[Message]:
        return list(self.messages)

    def has_new_messages(self, timestamp: datetime) -> bool:
        return any(message.timestamp <= timestamp for message in self.list_view())


@dataclass
class NotificationSystemConfig:
    notified_tools: dict[str, list[str]] = field(default_factory=dict)


def get_notification_tools(
    verbosity_level: VerbosityLevel = VerbosityLevel.LOW,
) -> dict[str, list[str]]:
    """
    Returns the tools to be notified based on the specified verbosity level.

    Args:
        level: The verbosity level to determine which tools to notify

    Returns:
        A dictionary mapping app names to lists of function names to be notified
    """
    base_notified_tools = {
        "DroneApp": ["charge"],
        "RobotApp": ["charge"],
        "FieldOpsApp": ["irrigate_ridge", "irrigate_range"],
    }

    if verbosity_level == VerbosityLevel.LOW:
        return _add_app_aliases(base_notified_tools)
    elif verbosity_level == VerbosityLevel.MEDIUM:
        notified_tools = {
            "EmailClientApp": [
                "create_and_add_email",
                "send_email_to_user_only",
                "reply_to_email_from_user",
            ],
            "MessagingApp": ["create_and_add_message"],
            "MessagingAppV2": ["create_and_add_message"],
            "ShoppingApp": [
                "cancel_order",
                "update_order_status",
            ],
            "CabApp": [
                "cancel_ride",
                "user_cancel_ride",
                "end_ride",
            ],
            "CalendarApp": [
                "add_calendar_event_by_attendee",
                "delete_calendar_event_by_attendee",
            ],
        }
        notified_tools.update(base_notified_tools)
        return _add_app_aliases(notified_tools)
    elif verbosity_level == VerbosityLevel.HIGH:
        notified_tools = {
            "EmailClientApp": [
                "create_and_add_email",
                "send_email_to_user_only",
                "reply_to_email_from_user",
            ],
            "MessagingApp": ["create_and_add_message"],
            "MessagingAppV2": ["create_and_add_message"],
            "ShoppingApp": [
                "add_product",
                "add_item_to_product",
                "cancel_order",
                "update_order_status",
                "add_discount_code",
            ],
            "ApartmentListingApp": ["add_new_apartment"],
            "CabApp": [
                "cancel_ride",
                "user_cancel_ride",
                "update_ride_status",
                "end_ride",
            ],
            "CalendarApp": [
                "add_calendar_event_by_attendee",
                "delete_calendar_event_by_attendee",
            ],
        }
        notified_tools.update(base_notified_tools)
        return _add_app_aliases(notified_tools)
    else:
        raise ValueError(f"Invalid verbosity level: {verbosity_level}")


def _add_app_aliases(notified_tools: dict[str, list[str]]) -> dict[str, list[str]]:
    """Helper function to add app aliases to the notified tools dictionary."""
    aliases_to_add = {}
    for app_name in notified_tools:
        if app_name in APP_ALIAS:
            if isinstance(APP_ALIAS[app_name], list):
                for alias in APP_ALIAS[app_name]:
                    aliases_to_add[alias] = notified_tools[app_name]
            elif isinstance(APP_ALIAS[app_name], str):
                aliases_to_add[APP_ALIAS[app_name]] = notified_tools[app_name]
    notified_tools.update(aliases_to_add)
    return notified_tools


class BaseNotificationSystem:
    """Base class for all notification systems."""

    def __init__(
        self, config: NotificationSystemConfig = NotificationSystemConfig()
    ) -> None:
        self.message_queue = MessageQueue()
        self.time_manager = None
        self.reminder_app = None
        self.system_app = None
        self._initialized = False
        self.config = config

    def initialize(self, time_manager: TimeManager) -> None:
        self.time_manager = time_manager
        self._initialized = True

    def setup_reminder_app(self, reminder_app: ReminderApp) -> None:
        self.reminder_app = reminder_app

    def setup_system_app(self, system_app: SystemApp) -> None:
        self.system_app = system_app

    def clear(self) -> None:
        self.message_queue = MessageQueue()

    def get_current_time(self) -> float:
        if not self.time_manager:
            raise ValueError("Time manager is not set.")
        return self.time_manager.time()

    def get_next_notification_time(self) -> float | None:
        next_message = self.message_queue.messages.peek()
        if next_message is None:
            return None
        return datetime.fromtimestamp(
            next_message.timestamp.timestamp(), tz=timezone.utc
        ).timestamp()

    def handle_time_based_notifications(self) -> None:
        if self.reminder_app:
            due_reminders = self.reminder_app.get_due_reminders()
            new_due_reminders = [
                reminder for reminder in due_reminders if not reminder.already_notified
            ]
            if new_due_reminders:
                message = self.convert_reminders_to_message(new_due_reminders)
                if message is not None:
                    self.message_queue.put(message)

    def handle_timeout_after_events(self) -> None:
        """
        Handle timeout notifications after all events in the current tick have been processed.
        This ensures that timeout notifications are only triggered if no other notifications
        were generated during the same tick.
        """
        current_timestamp = datetime.fromtimestamp(
            self.get_current_time(), tz=timezone.utc
        )
        if self.system_app:
            if not self.message_queue.has_new_messages(timestamp=current_timestamp):
                wait_for_notification_timeout = (
                    self.system_app.get_wait_for_notification_timeout()
                )
                if wait_for_notification_timeout is not None:
                    # Insert a wait for notification timeout message
                    message = self.convert_wait_for_notification_timeout_to_message(
                        wait_for_notification_timeout
                    )
                    if message is not None:
                        self.message_queue.put(message)
                    self.system_app.reset_wait_for_notification_timeout()
            else:
                # If there are new messages, reset the wait for notification timeout
                self.system_app.reset_wait_for_notification_timeout()

    def convert_reminders_to_message(
        self, due_reminders: list[Reminder]
    ) -> Message | None:
        """Convert due reminders to a notification message."""
        if self.reminder_app:
            timestamp = self.get_current_time()
            for reminder in due_reminders:
                reminder.time_notified = timestamp
            message = f"{self.reminder_app.name}: New due reminders:\n"
            message += "\n".join(str(reminder) for reminder in due_reminders)
            return Message(
                message_type=MessageType.ENVIRONMENT_NOTIFICATION,
                message=message,
                timestamp=datetime.fromtimestamp(timestamp, tz=timezone.utc),
            )
        return None

    def convert_wait_for_notification_timeout_to_message(
        self, wait_for_notification_timeout: WaitForNotificationTimeout
    ) -> Message | None:
        if self.system_app:
            timestamp = self.get_current_time()
            message = f"{self.system_app.name}: Wait for notification timeout reached after {wait_for_notification_timeout.timeout} seconds"
            return Message(
                message_type=MessageType.ENVIRONMENT_NOTIFICATION,
                message=message,
                timestamp=datetime.fromtimestamp(timestamp, tz=timezone.utc),
            )
        return None

    def handle_event(self, event: AbstractEvent) -> None:
        if not self._initialized:
            raise ValueError("Notification system is not initialized.")
        message = self.convert_to_message(event)
        if message is not None:
            self.message_queue.put(message)

    def convert_to_message(self, event: AbstractEvent) -> Message | None:
        if not isinstance(event, CompletedEvent) or not isinstance(
            event.action, Action
        ):
            return None

        timestamp = self.get_current_time()

        # Handle AgentUserInterface send_message_to_agent events
        if (
            hasattr(event.action, "app")
            and event.app_class_name() == "AgentUserInterface"
            and event.function_name() == "send_message_to_agent"
        ):
            args: dict[str, Any] = get_args(event)
            message = str(
                AUIMessage(
                    sender=Sender.USER,
                    content=args["content"],
                    timestamp=timestamp,
                    time_read=timestamp,
                )
            )
            dumped_attachments: list[dict[str, Any]] = (
                args.get("base64_utf8_encoded_attachment_contents") or []
            )
            attachments: list[Attachment] = [
                Attachment.model_validate(attachment)
                for attachment in dumped_attachments
            ]
            return Message(
                message_type=MessageType.USER_MESSAGE,
                message=message,
                timestamp=datetime.fromtimestamp(timestamp, tz=timezone.utc),
                attachments=attachments,
            )

        # Handle environment notification events
        message = get_content_for_message(event)
        notification_timestamp = get_notification_timestamp(event, timestamp)
        app_class_name = event.app_class_name()
        if (
            app_class_name is not None
            and app_class_name in self.config.notified_tools
            and event.function_name() in self.config.notified_tools[app_class_name]
            and message is not None
        ):
            return Message(
                message_type=MessageType.ENVIRONMENT_NOTIFICATION,
                message=message,
                timestamp=datetime.fromtimestamp(
                    notification_timestamp, tz=timezone.utc
                ),
            )

        return None


def get_args(event: CompletedEvent) -> dict[str, Any]:
    assert type(event.action) is Action
    if event.action.resolved_args:
        return event.action.resolved_args
    return event.action.args


def get_notification_timestamp(
    event: CompletedEvent, default_timestamp: float
) -> float:
    if (
        hasattr(event.action, "app")
        and event.app_class_name() in ("DroneApp", "RobotApp")
        and event.function_name() == "charge"
    ):
        return_value = event.metadata.return_value
        if (
            isinstance(return_value, dict)
            and return_value.get("status") == "charging_started"
            and isinstance(return_value.get("charge_complete_at"), (int, float))
        ):
            return float(return_value["charge_complete_at"])
    if (
        hasattr(event.action, "app")
        and event.app_class_name() == "FieldOpsApp"
        and event.function_name() in ("irrigate_ridge", "irrigate_range")
    ):
        return_value = event.metadata.return_value
        if (
            isinstance(return_value, dict)
            and return_value.get("status") == "irrigation_started"
            and isinstance(return_value.get("effect_ready_at"), (int, float))
        ):
            return float(return_value["effect_ready_at"])
    return default_timestamp


class VerboseNotificationSystem(BaseNotificationSystem):
    def __init__(self, verbosity_level: VerbosityLevel = VerbosityLevel.MEDIUM):
        self.verbosity_level = verbosity_level
        config = NotificationSystemConfig(
            notified_tools=get_notification_tools(self.verbosity_level)
        )
        super().__init__(config)

    def __call__(self) -> BaseNotificationSystem:
        config = NotificationSystemConfig(
            notified_tools=get_notification_tools(self.verbosity_level)
        )
        return BaseNotificationSystem(config)


NotificationSystem = VerboseNotificationSystem(verbosity_level=VerbosityLevel.LOW)


def get_content_for_message(event: AbstractEvent) -> str | None:
    if type(event) is CompletedEvent and type(event.action) is Action:
        if (
            hasattr(event.action, "app")
            and event.app_class_name() == "DroneApp"
            and event.function_name() == "charge"
        ):
            return_value = event.metadata.return_value
            if (
                isinstance(return_value, dict)
                and return_value.get("status") == "charging_started"
            ):
                return f"{event.app_name()}: charge complete"
            return None
        elif (
            hasattr(event.action, "app")
            and event.app_class_name() == "FieldOpsApp"
            and event.function_name() in ("irrigate_ridge", "irrigate_range")
        ):
            return_value = event.metadata.return_value
            if (
                isinstance(return_value, dict)
                and return_value.get("status") == "irrigation_started"
            ):
                ridges = return_value.get("irrigated_ridges")
                if isinstance(ridges, list) and len(ridges) == 1:
                    return f"{event.app_name()}: irrigation effect ready for ridge {ridges[0]}"
                if isinstance(ridges, list) and len(ridges) > 1:
                    return (
                        f"{event.app_name()}: irrigation effect ready for ridges "
                        f"{ridges[0]}-{ridges[-1]}"
                    )
                return f"{event.app_name()}: irrigation effect ready"
            return None
        elif (
            hasattr(event.action, "app")
            and (
                event.app_class_name() == "EmailClientApp"
                or event.app_class_name() in APP_ALIAS["EmailClientApp"]
            )
            and event.function_name()
            in ["create_and_add_email", "send_email_to_user_only"]
        ):
            return f"{event.app_class_name()}: New email received from {get_args(event)['sender']}"
        elif (
            hasattr(event.action, "app")
            and (
                event.app_class_name() == "EmailClientApp"
                or event.app_class_name() in APP_ALIAS["EmailClientApp"]
            )
            and event.function_name() == "reply_to_email_from_user"
        ):
            return f"{event.app_class_name()}: New email received"
        elif (
            hasattr(event.action, "app")
            and (
                event.app_class_name() == "MessagingApp"
                or event.app_class_name() in APP_ALIAS["MessagingApp"]
                or event.app_class_name() == "MessagingAppV2"
                or event.app_class_name() in APP_ALIAS["MessagingAppV2"]
            )
            and event.function_name() == "create_and_add_message"
        ):
            return f"{event.app_class_name()}: New message received in conversation ID: {get_args(event)['conversation_id']}"
        elif (
            hasattr(event.action, "app")
            and (
                event.app_class_name() == "Shopping"
                or event.app_class_name() == "ShoppingApp"
            )
            and event.function_name() == "add_product"
        ):
            return f"{event.app_class_name()}: New product added: {get_args(event)['name']}"
        elif (
            hasattr(event.action, "app")
            and (
                event.app_class_name() == "Shopping"
                or event.app_class_name() == "ShoppingApp"
            )
            and event.function_name() == "add_item_to_product"
        ):
            return f"{event.app_class_name()}: New item added to product with Id {get_args(event)['product_id']}"
        elif (
            hasattr(event.action, "app")
            and (
                event.app_class_name() == "Shopping"
                or event.app_class_name() == "ShoppingApp"
            )
            and event.function_name() == "cancel_order"
            and event.event_type in [EventType.USER, EventType.ENV]
        ):
            return f"{event.app_class_name()}: Order cancelled with Id {get_args(event)['order_id']}"
        elif (
            hasattr(event.action, "app")
            and (
                event.app_class_name() == "Shopping"
                or event.app_class_name() == "ShoppingApp"
            )
            and event.function_name() == "update_order_status"
        ):
            return f"{event.app_class_name()}: Updated order with Id {get_args(event)['order_id']}"
        elif (
            hasattr(event.action, "app")
            and (
                event.app_class_name() == "Shopping"
                or event.app_class_name() == "ShoppingApp"
            )
            and event.function_name() == "add_discount_code"
        ):
            return f"{event.app_class_name()}: New discount code for item with Id {get_args(event)['item_id']}"
        elif (
            hasattr(event.action, "app")
            and (
                event.app_class_name() == "RentAFlat"
                or event.app_class_name() == "ApartmentListingApp"
            )
            and event.function_name() == "add_new_apartment"
        ):
            return f"{event.app_class_name()}: New apartment available with name {get_args(event)['name']}"
        elif (
            hasattr(event.action, "app")
            and event.app_class_name() == "CabApp"
            and event.function_name() in ["cancel_ride", "user_cancel_ride"]
            and event.event_type in [EventType.USER, EventType.ENV]
        ):
            return f"{event.app_class_name()}: ride cancelled"
        elif (
            hasattr(event.action, "app")
            and event.app_class_name() == "CabApp"
            and event.function_name() == "update_ride_status"
        ):
            return f"{event.app_class_name()}: ride status updated"
        elif (
            hasattr(event.action, "app")
            and event.app_class_name() == "CabApp"
            and event.function_name() == "end_ride"
        ):
            return f"{event.app_class_name()}: ride completed"
        elif (
            hasattr(event.action, "app")
            and event.app_class_name() in ["Calendar", "CalendarApp"]
            and event.function_name() == "add_calendar_event_by_attendee"
        ):
            return f"{event.app_class_name()}: New calendar event added by {get_args(event)['who_add']}"
        elif (
            hasattr(event.action, "app")
            and event.app_class_name() in ["Calendar", "CalendarApp"]
            and event.function_name() == "delete_calendar_event_by_attendee"
        ):
            return f"{event.app_class_name()}: Calendar event deleted by {get_args(event)['who_delete']}"
        else:
            return None
    else:
        return None
