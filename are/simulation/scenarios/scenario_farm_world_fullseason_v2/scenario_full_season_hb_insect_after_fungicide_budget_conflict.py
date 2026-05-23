from __future__ import annotations

from typing import Any

from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_batch_catalog import (
    get_spec,
)
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_batch_scenario import (
    build_batch_events,
    init_batch_apps,
)
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
    harbin_start_time,
)
from are.simulation.scenarios.utils.registry import register_scenario

SPEC = get_spec("hb_insect_after_fungicide_budget_conflict")
SCENARIO_ID = SPEC.scenario_id
PROFILE_NAME = SPEC.profile_name
SCENARIO_DESCRIPTION = SPEC.description
BRIEFING_TEXT = SPEC.briefing_text


@register_scenario(SCENARIO_ID)
class ScenarioFullSeasonHBInsectAfterFungicideBudgetConflict(Scenario):
    """{SCENARIO_DESCRIPTION}"""

    start_time: float | None = harbin_start_time()
    duration: float | None = 180 * 24 * 3600
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    nb_turns: int = 1

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        init_batch_apps(self, SPEC)

    def _after_named_step(self, prev: Any, label: str) -> Any:
        return prev

    def build_events_flow(self) -> None:
        build_batch_events(self, SPEC)
