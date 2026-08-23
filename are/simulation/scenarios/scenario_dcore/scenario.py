"""Manual native-scenario smoke entry point for repository scenario checks."""

from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_hb_wetjune_disease_recheck_after_fungicide import (
    ScenarioFullSeasonHBWetjuneDiseaseRecheckAfterFungicide,
)

if __name__ == "__main__":
    from are.simulation.scenarios.utils.cli_utils import run_and_validate

    run_and_validate(ScenarioFullSeasonHBWetjuneDiseaseRecheckAfterFungicide())
