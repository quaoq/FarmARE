"""Native wait scheduling must respect actor intent without advancing the farm."""

from __future__ import annotations

import pytest

from are.simulation.distributed.models import (
    AgentIntent,
    DistributedRunnerConfig,
    IntentKind,
)
from are.simulation.distributed.native_season import NativeDistributedSeasonRunner


class Controller:
    def __init__(self, intents):
        self.intents = iter(intents)
        self.views = []
        self.results = []
        self.complete = False

    def initialize(self, actor, local_view):
        pass

    def decide(self, view):
        self.views.append(view)
        intent = next(self.intents)
        self.complete = intent.kind == IntentKind.FINISH
        return intent

    def observe(self, result):
        self.results.append(result)

    def is_complete(self):
        return self.complete


def finish():
    return AgentIntent(kind=IntentKind.FINISH)


def send():
    return AgentIntent(
        kind=IntentKind.SEND, recipient="field_intelligence", text="new handoff"
    )


def run(field, operations):
    return NativeDistributedSeasonRunner(
        controllers={
            "field_intelligence": field,
            "operations": operations,
        }
    ).run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            controller_mode="scripted",
            handoff_mode="free_text",
            enforcement_mode="off",
            max_logical_steps=8,
        )
    )


@pytest.mark.parametrize("new_handoff", [False, True])
def test_native_wait_interval_and_new_handoff_wakeup(new_handoff, monkeypatch):
    # Remove wall-clock execution overhead; explicit native clock offsets still
    # apply, so a scheduler bug that advances farm time fails this assertion.
    monkeypatch.setattr(
        "are.simulation.time_manager.TimeManager.real_time_passed", lambda self: 0.0
    )
    field = Controller([AgentIntent(kind=IntentKind.WAIT, wait=10), finish()])
    operations = Controller([send(), finish()] if new_handoff else [finish()])
    run(field, operations)
    assert len(field.views) == 2
    elapsed = field.views[1].logical_time - field.views[0].logical_time
    assert (elapsed < 10) if new_handoff else (elapsed >= 10)
    assert field.views[0].world_time == field.views[1].world_time
    assert field.results[0]["wake_at_logical_time"] == field.views[0].logical_time + 10
    assert field.results[0]["farm_time_advanced"] is False


def test_handoff_never_revives_a_finished_actor():
    field = Controller([finish()])
    operations = Controller([send(), finish()])
    result = run(field, operations)
    assert len(field.views) == 1
    assert result.trace.events[-1].action == "farm.execution_terminated"
    assert result.trace.events[-1].season_phase == "field_prep"
    assert (
        result.trace.outcome["termination_by_actor"]["field_intelligence"]
        == "voluntary_completion"
    )
