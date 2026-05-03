"""
Structured record of an action taken by a farm tool.

Per the integration guide
(scenario_farm_world_physics/physics_action_tick_integration_guide.md, section
"Actions should be recorded"), every operation tool appends one of these to
FarmWorldApp's action history. The record is consumed by:

  - the physics orchestrator (to translate intent into engine inputs),
  - the workflow validator (sequence-level evaluation),
  - debugging traces.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FarmActionRecord:
    action_id: str
    timestamp: float
    actor_app: str
    action_type: str
    ridge_ids: list[int]
    parameters: dict[str, Any] = field(default_factory=dict)
    direct_effect_summary: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
