"""In-process transport with deterministic communication faults."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from are.simulation.distributed.models import Envelope


class FaultMode(str, Enum):
    NONE = "none"
    DELAY = "delay"
    DROP = "drop"
    DUPLICATE = "duplicate"
    REORDER = "reorder"


@dataclass(frozen=True)
class FaultRule:
    mode: FaultMode = FaultMode.NONE
    delay: float = 0.0
    duplicate_delay: float = 0.0
    reorder_bias: float = 0.0
    valid_until_world_time: float | None = None
    deadline_world_time: float | None = None
    delivery_world_time: float | None = None


@dataclass(frozen=True)
class FaultSchedule:
    default: FaultRule = field(default_factory=FaultRule)
    by_send_index: dict[int, FaultRule] = field(default_factory=dict)
    by_message_id: dict[str, FaultRule] = field(default_factory=dict)
    by_message_prefix: dict[str, FaultRule] = field(default_factory=dict)
    by_fact_version: dict[str, FaultRule] = field(default_factory=dict)

    def rule_for(
        self,
        send_index: int,
        *,
        message_id: str = "",
        fact_versions: tuple[str, ...] = (),
    ) -> FaultRule:
        if message_id in self.by_message_id:
            return self.by_message_id[message_id]
        for prefix in sorted(
            self.by_message_prefix, key=lambda value: (-len(value), value)
        ):
            if message_id.startswith(prefix):
                return self.by_message_prefix[prefix]
        for fact_version in fact_versions:
            if fact_version in self.by_fact_version:
                return self.by_fact_version[fact_version]
        return self.by_send_index.get(send_index, self.default)


@dataclass(order=True, frozen=True)
class ScheduledDelivery:
    deliver_at: float
    order_key: float
    send_index: int
    copy_index: int
    envelope: Envelope = field(compare=False)


class InProcessTransport:
    def __init__(
        self,
        actor_ids: tuple[str, ...],
        schedule: FaultSchedule | None = None,
        seed: int = 0,
    ):
        self.actor_ids = actor_ids
        self.schedule = schedule or FaultSchedule()
        self.seed = seed
        self.rng = random.Random(seed)
        self._send_index = 0
        self._pending: list[ScheduledDelivery] = []
        self._sent: list[Envelope] = []
        self._dropped: list[str] = []
        self._delivered_copies: list[tuple[str, int]] = []
        self._delivery_history: list[dict[str, Any]] = []
        self._applied_rules: list[dict[str, Any]] = []

    def send(self, envelope: Envelope, current_time: float) -> Envelope:
        if (
            envelope.sender not in self.actor_ids
            or envelope.recipient not in self.actor_ids
        ):
            raise ValueError("message sender and recipient must be registered actors")
        self._send_index += 1
        message_id = envelope.message_id or f"m{self._send_index:06d}"
        envelope = envelope.model_copy(
            update={"message_id": message_id, "send_time": current_time}
        )
        self._sent.append(envelope)
        fact_versions = tuple(
            claim.fact_version_id
            for claim in getattr(envelope, "claims", ())
            if claim.fact_version_id
        )
        rule = self.schedule.rule_for(
            self._send_index,
            message_id=message_id,
            fact_versions=fact_versions,
        )
        self._applied_rules.append(
            {
                "send_index": self._send_index,
                "message_id": message_id,
                "mode": rule.mode.value,
                "delay": rule.delay,
                "duplicate_delay": rule.duplicate_delay,
                "reorder_bias": rule.reorder_bias,
                "deadline_world_time": rule.deadline_world_time,
                "delivery_world_time": rule.delivery_world_time,
                "fact_versions": list(fact_versions),
                "valid_until": (
                    rule.valid_until_world_time
                    if rule.valid_until_world_time is not None
                    else min(
                        (
                            claim.valid_until
                            for claim in getattr(envelope, "claims", ())
                            if claim.valid_until is not None
                        ),
                        default=None,
                    )
                ),
            }
        )
        if rule.mode == FaultMode.DROP:
            self._dropped.append(message_id)
            return envelope
        base_delay = max(
            0.0,
            rule.delay,
            (
                rule.delivery_world_time - current_time
                if rule.delivery_world_time is not None
                else 0.0
            ),
        )
        order_bias = rule.reorder_bias if rule.mode == FaultMode.REORDER else 0.0
        self._pending.append(
            ScheduledDelivery(
                deliver_at=current_time + base_delay,
                order_key=current_time + base_delay + order_bias,
                send_index=self._send_index,
                copy_index=0,
                envelope=envelope,
            )
        )
        if rule.mode == FaultMode.DUPLICATE:
            self._pending.append(
                ScheduledDelivery(
                    deliver_at=current_time
                    + base_delay
                    + max(0.0, rule.duplicate_delay),
                    order_key=current_time
                    + base_delay
                    + max(0.0, rule.duplicate_delay),
                    send_index=self._send_index,
                    copy_index=1,
                    envelope=envelope,
                )
            )
        self._pending.sort()
        return envelope

    def next_delivery_time(self) -> float | None:
        return min((delivery.deliver_at for delivery in self._pending), default=None)

    def deliver_next(self, logical_time: float) -> list[ScheduledDelivery]:
        due = [
            delivery
            for delivery in self._pending
            if delivery.deliver_at <= logical_time
        ]
        self._pending = [
            delivery for delivery in self._pending if delivery.deliver_at > logical_time
        ]
        due.sort(
            key=lambda delivery: (
                delivery.order_key,
                delivery.send_index,
                delivery.copy_index,
            )
        )
        self._delivered_copies.extend(
            (delivery.envelope.message_id, delivery.copy_index) for delivery in due
        )
        self._delivery_history.extend(
            {
                "message_id": delivery.envelope.message_id,
                "send_index": delivery.send_index,
                "copy_index": delivery.copy_index,
                "send_time": delivery.envelope.send_time,
                "delivered_at": delivery.deliver_at,
                "processed_at": logical_time,
                "scheduled_at": delivery.deliver_at,
                "delivery_sequence": len(self._delivery_history) + index,
            }
            for index, delivery in enumerate(due)
        )
        return due

    def pending_for(self, actor_id: str) -> tuple[ScheduledDelivery, ...]:
        return tuple(
            delivery
            for delivery in sorted(self._pending)
            if delivery.envelope.recipient == actor_id
        )

    def watermark(self, actor_id: str) -> bool:
        return not self.pending_for(actor_id)

    def snapshot(self) -> dict[str, Any]:
        return {
            "sent": [message.model_dump(mode="json") for message in self._sent],
            "pending": [
                {
                    "message_id": delivery.envelope.message_id,
                    "deliver_at": delivery.deliver_at,
                    "copy_index": delivery.copy_index,
                }
                for delivery in sorted(self._pending)
            ],
            "dropped": list(self._dropped),
            "delivered_copies": list(self._delivered_copies),
            "delivery_history": list(self._delivery_history),
            "applied_rules": list(self._applied_rules),
            "seed": self.seed,
        }

    def fault_manifestation(self, intended: str) -> dict[str, Any]:
        """Reconstruct whether a named treatment changed message delivery."""

        applied = list(self._applied_rules)
        delivered = list(self._delivery_history)
        dropped = set(self._dropped)
        by_message: dict[str, list[dict[str, Any]]] = {}
        for item in delivered:
            by_message.setdefault(str(item["message_id"]), []).append(item)
        positive_delays = [
            item
            for item in delivered
            if float(item["scheduled_at"]) > float(item["send_time"])
        ]
        validity_delays = []
        deadline_delays = []
        for rule in applied:
            valid_until = rule.get("valid_until")
            arrivals = by_message.get(str(rule["message_id"]), [])
            if valid_until is not None and arrivals:
                validity_delays.extend(
                    {
                        "message_id": rule["message_id"],
                        "send_time": item["send_time"],
                        "delivered_at": item["delivered_at"],
                        "valid_until": valid_until,
                        "past_validity": float(item["delivered_at"])
                        > float(valid_until),
                        "crossed_validity": float(item["send_time"])
                        <= float(valid_until)
                        < float(item["delivered_at"]),
                    }
                    for item in arrivals
                )
            deadline = rule.get("deadline_world_time")
            if deadline is not None and arrivals:
                deadline_delays.extend(
                    {
                        "message_id": rule["message_id"],
                        "send_time": item["send_time"],
                        "delivered_at": item["delivered_at"],
                        "deadline_world_time": deadline,
                        "past_deadline": float(item["delivered_at"]) > float(deadline),
                        "crossed_deadline": float(item["send_time"])
                        <= float(deadline)
                        < float(item["delivered_at"]),
                    }
                    for item in arrivals
                )
        first_deliveries = sorted(
            (
                min(items, key=lambda value: value["delivery_sequence"])
                for items in by_message.values()
            ),
            key=lambda value: value["delivery_sequence"],
        )
        delivery_send_order = [int(item["send_index"]) for item in first_deliveries]
        inversions = [
            (delivery_send_order[left], delivery_send_order[right])
            for left in range(len(delivery_send_order))
            for right in range(left + 1, len(delivery_send_order))
            if delivery_send_order[left] > delivery_send_order[right]
        ]
        duplicate_messages = [
            message_id for message_id, items in by_message.items() if len(items) > 1
        ]
        applied_by_message = {str(item["message_id"]): item for item in applied}
        delayed_treatment_ids = {
            str(item["message_id"])
            for item in applied
            if item["mode"] == FaultMode.DELAY.value
        }
        reorder_send_indices = {
            int(item["send_index"])
            for item in applied
            if item["mode"] == FaultMode.REORDER.value
        }
        treatment_delays = [
            item
            for item in positive_delays
            if str(item["message_id"]) in delayed_treatment_ids
        ]
        treatment_inversions = [
            item
            for item in inversions
            if item[0] in reorder_send_indices or item[1] in reorder_send_indices
        ]
        checks = {
            "none": bool(applied)
            and len(by_message) == len(applied_by_message)
            and not dropped
            and not duplicate_messages
            and not positive_delays
            and not inversions,
            "delay": bool(treatment_delays),
            "delay_within_validity": any(
                float(item["delivered_at"]) > float(item["send_time"])
                and not item["past_validity"]
                for item in validity_delays
                if str(item["message_id"]) in delayed_treatment_ids
            ),
            "delay_past_validity": any(
                item["crossed_validity"]
                for item in validity_delays
                if str(item["message_id"]) in delayed_treatment_ids
            ),
            "delay_past_deadline": any(
                item["crossed_deadline"]
                for item in deadline_delays
                if str(item["message_id"]) in delayed_treatment_ids
            ),
            "drop": bool(dropped),
            "duplicate": bool(duplicate_messages),
            "reorder": bool(treatment_inversions),
            "mixed": sum(
                bool(value)
                for value in (
                    dropped,
                    duplicate_messages,
                    treatment_delays,
                    treatment_inversions,
                )
            )
            >= 2,
        }
        return {
            "fault": intended,
            "manifested": bool(checks.get(intended, False)),
            "dropped_message_ids": sorted(dropped),
            "duplicate_message_ids": sorted(duplicate_messages),
            "delayed_message_ids": sorted(
                {str(item["message_id"]) for item in positive_delays}
            ),
            "past_validity": validity_delays,
            "past_deadline": deadline_delays,
            "receive_order_inversions": treatment_inversions,
            "applied_rules": applied,
        }
