"""Controlled Wet-June specification fixtures, never paper observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from are.simulation.distributed.models import stable_digest


@dataclass(frozen=True)
class WetJunePolicyFixture:
    fixture_id: str
    policy_id: Literal["wetjune-fungicide-policy", "wetjune-harvest-policy"]
    facts: dict[str, object]
    requirement_state: Literal["true", "false", "unknown"]
    deadline_state: Literal["open", "closed"]
    expected_responses: tuple[str, ...]
    communication_condition_independent: bool = True

    @property
    def exogenous_digest(self) -> str:
        return stable_digest({"fixture_id": self.fixture_id, "facts": self.facts})


def controlled_wetjune_policy_fixtures() -> tuple[WetJunePolicyFixture, ...]:
    safe_open = ("reobserve", "defer", "abstain")
    return (
        WetJunePolicyFixture(
            "no_actionable_disease",
            "wetjune-fungicide-policy",
            {
                "disease:confirmed": False,
                "disease:affected_scope": None,
                "weather:spray_window_open": True,
                "soil:trafficable": True,
            },
            "false",
            "open",
            safe_open,
        ),
        WetJunePolicyFixture(
            "immediate_spray_window",
            "wetjune-fungicide-policy",
            {
                "disease:confirmed": True,
                "weather:spray_window_open": True,
                "soil:trafficable": True,
                "disease:affected_scope": (20, 43),
            },
            "true",
            "open",
            ("execute", "reobserve", "defer"),
        ),
        WetJunePolicyFixture(
            "closed_window_then_open",
            "wetjune-fungicide-policy",
            {
                "weather:spray_window_open": False,
                "soil:trafficable": True,
                "disease:confirmed": True,
                "disease:affected_scope": (20, 43),
            },
            "false",
            "open",
            safe_open,
        ),
        WetJunePolicyFixture(
            "window_closed_past_deadline",
            "wetjune-fungicide-policy",
            {
                "weather:spray_window_open": False,
                "soil:trafficable": True,
                "disease:confirmed": True,
                "disease:affected_scope": (20, 43),
            },
            "false",
            "closed",
            ("abstain",),
        ),
        WetJunePolicyFixture(
            "trafficability_deterioration",
            "wetjune-fungicide-policy",
            {
                "weather:spray_window_open": True,
                "soil:trafficable": False,
                "disease:confirmed": True,
                "disease:affected_scope": (20, 43),
            },
            "false",
            "open",
            safe_open,
        ),
        WetJunePolicyFixture(
            "stale_handoff",
            "wetjune-fungicide-policy",
            {
                "weather:spray_window_open": True,
                "soil:trafficable": True,
                "disease:confirmed": True,
                "disease:affected_scope": (20, 43),
                "__stale_fact_key": "weather:spray_window_open",
            },
            "false",
            "open",
            safe_open,
        ),
        WetJunePolicyFixture(
            "superseding_disease_scope",
            "wetjune-fungicide-policy",
            {
                "weather:spray_window_open": True,
                "soil:trafficable": True,
                "disease:confirmed": True,
                "disease:affected_scope": (30, 43),
                "__superseded_scope": (20, 43),
            },
            "true",
            "open",
            ("execute", "reobserve", "defer"),
        ),
        WetJunePolicyFixture(
            "r5_disease_recurrence",
            "wetjune-fungicide-policy",
            {
                "disease:confirmed": True,
                "weather:spray_window_open": True,
                "soil:trafficable": True,
                "disease:affected_scope": (20, 43),
            },
            "true",
            "open",
            ("execute", "reobserve", "defer"),
        ),
        WetJunePolicyFixture(
            "successful_treatment_recovery",
            "wetjune-fungicide-policy",
            {
                "disease:confirmed": False,
                "disease:affected_scope": None,
                "weather:spray_window_open": True,
                "soil:trafficable": True,
                "treatment:response": 1.0,
            },
            "false",
            "open",
            safe_open,
        ),
        WetJunePolicyFixture(
            "harvest_moisture_above_18",
            "wetjune-harvest-policy",
            {
                "crop:grain_moisture": 19.0,
                "crop:mature": True,
                "weather:harvest_window_open": True,
                "soil:trafficable": True,
            },
            "false",
            "open",
            safe_open,
        ),
        WetJunePolicyFixture(
            "harvest_requires_drying",
            "wetjune-harvest-policy",
            {
                "crop:grain_moisture": 15.0,
                "crop:mature": True,
                "weather:harvest_window_open": True,
                "soil:trafficable": True,
            },
            "true",
            "open",
            ("execute", "reobserve", "defer"),
        ),
        WetJunePolicyFixture(
            "direct_safe_storage",
            "wetjune-harvest-policy",
            {
                "crop:grain_moisture": 13.0,
                "crop:mature": True,
                "weather:harvest_window_open": True,
                "soil:trafficable": True,
            },
            "true",
            "open",
            ("execute", "reobserve", "defer"),
        ),
    )
