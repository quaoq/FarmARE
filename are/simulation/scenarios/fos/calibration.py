"""
Frozen agronomic calibration for the spatiotemporal path metric (FARM-FOS).

This module is the *anti-circularity firewall*. Every weight and tolerance here
is a fixed agronomic prior — the relative importance of a farm operation and the
half-width of the crop-development window in which it matters — taken from
published soybean crop-stage agronomy, NOT fitted to any yield measurement.

Provenance of the windows (all standard soybean agronomy, independent of our
simulator):
  - Planting / stand establishment is foundational: stand fraction multiplies
    season-long canopy; window is narrow (soil temp + moisture gate).
  - The critical weed-free period is early vegetative (VE-V3): a few days.
  - R5 (pod-fill / seed-fill) is the most water-sensitive reproductive stage.
  - Foliar disease control matters across R3-R6; ~1 week tolerance.
  - Harvest timing is asymmetric: a few days late within the post-R8 grace is
    cheap, but harvesting before maturity or far past it (shatter, field loss)
    is expensive.

CRITICAL INVARIANT: nothing in this module reads a yield value, a correlation,
or any run output. It is a pure constant table plus an optional engine-probe
helper used ONLY for after-the-fact corroboration (never to set the weights).
The unit test ``test_calibration_is_yield_independent`` enforces this.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionCalibration:
    """Frozen agronomic prior for one farm-operation category.

    Attributes:
        weight:        relative yield-importance prior in [0, 1]. Used only in
                       *ratio* form (normalised by the per-scenario sum), so the
                       absolute scale is irrelevant — only the ordering matters.
        sigma_days:    half-width (days) of the agronomic timing window; the
                       triangular kernel reaches zero credit at this offset.
        kernel:        "triangular" (symmetric) or "harvest" (asymmetric).
        grace_days:    harvest only — late offset that still earns full credit.
        sigma_late:    harvest only — tolerance for offsets beyond the grace.
        sigma_early:   harvest only — tolerance for harvesting *before* maturity
                       (kept small: early harvest is penalised hard).
        rationale:     human-readable agronomic justification (for the paper).
    """

    weight: float
    sigma_days: float
    kernel: str = "triangular"
    grace_days: float = 0.0
    sigma_late: float = 0.0
    sigma_early: float = 0.0
    rationale: str = ""


# ---------------------------------------------------------------------------
# THE FROZEN TABLE.  Keyed by farm-operation category (resolved from the tool
# function name).  Values are agronomic priors, set a priori, never tuned.
# ---------------------------------------------------------------------------
FROZEN_CALIBRATION: dict[str, ActionCalibration] = {
    "plant": ActionCalibration(
        weight=1.00, sigma_days=5.0,
        rationale="Stand establishment multiplies season-long canopy; narrow "
                  "soil-temp/moisture planting window.",
    ),
    "replant": ActionCalibration(
        weight=0.60, sigma_days=5.0,
        rationale="Emergence recovery only useful inside a short re-seed window.",
    ),
    "irrigate": ActionCalibration(
        weight=0.80, sigma_days=7.0,
        rationale="R5 pod-fill is the most water-sensitive stage; ~1wk window.",
    ),
    "fungicide": ActionCalibration(
        weight=0.70, sigma_days=7.0,
        rationale="Foliar disease control across R3-R6; protectant timing ~1wk.",
    ),
    "pesticide": ActionCalibration(
        weight=0.60, sigma_days=7.0,
        rationale="Insect threshold control during V4+-R5; ~1wk action window.",
    ),
    "herbicide": ActionCalibration(
        weight=0.55, sigma_days=3.0,
        rationale="Critical weed-free period VE-V3; narrow, escalates fast.",
    ),
    "fertigate": ActionCalibration(
        weight=0.50, sigma_days=14.0,
        rationale="Nutrient correction; slow stress dynamics give a broad window.",
    ),
    "base_fertilize": ActionCalibration(
        weight=0.40, sigma_days=14.0,
        rationale="Pre-plant/early nutrient base; broad timing tolerance.",
    ),
    "harvest": ActionCalibration(
        weight=0.90, sigma_days=14.0, kernel="harvest",
        grace_days=7.0, sigma_late=14.0, sigma_early=5.0,
        rationale="Asymmetric: late within grace is cheap (drydown), early "
                  "(pre-R8) and far-late (shatter/field loss) are expensive.",
    ),
    "dry_grain": ActionCalibration(
        weight=0.30, sigma_days=5.0,
        rationale="Post-harvest moisture/quality; short safe-drying window.",
    ),
    "store_grain": ActionCalibration(
        weight=0.20, sigma_days=7.0,
        rationale="Storage step; modest, broad quality effect.",
    ),
    "incorporate_residue": ActionCalibration(
        weight=0.10, sigma_days=21.0,
        rationale="Residue management; little same-season yield effect.",
    ),
}


# Map a resolved tool function name -> calibration category. Tool names arrive
# as "<ClassName>__<function>" (or "<AppName>__<function>" for drone/robot);
# we match on the function suffix. Only *decision* actions appear here; every
# observation / logistics / setup tool (sensors, weather, advance_time,
# check_status, inspect_*, fly_survey, charge, load_*, attach/detach, get_*,
# configure_*, level, form_ridges, set_*, commit_daily_physics) is intentionally
# absent and contributes ZERO yield weight.
_FUNCTION_TO_CATEGORY: dict[str, str] = {
    "plant_seeds": "plant",
    "replant_seeds": "replant",
    "irrigate": "irrigate",
    "irrigate_range": "irrigate",
    "apply_fungicide": "fungicide",
    "apply_pesticide": "pesticide",
    "spray_pesticide": "pesticide",
    "apply_pesticide_manual": "pesticide",
    "apply_herbicide": "herbicide",
    "apply_fertigation": "fertigate",
    "base_fertilize": "base_fertilize",
    "harvest": "harvest",
    "dry_grain": "dry_grain",
    "store_grain": "store_grain",
    "incorporate_residue": "incorporate_residue",
}


def category_for_tool(tool_name: str) -> str | None:
    """Resolve a workflow tool_name to a calibration category, or None.

    None means the action carries no yield weight (observation/logistics/setup)
    and is excluded from FARM-FOS scoring.
    """
    if not tool_name:
        return None
    func = tool_name.split("__")[-1]
    return _FUNCTION_TO_CATEGORY.get(func)


def calibration_for_tool(tool_name: str) -> ActionCalibration | None:
    cat = category_for_tool(tool_name)
    return FROZEN_CALIBRATION.get(cat) if cat else None


def temporal_kernel(delta_days: float, calib: ActionCalibration) -> float:
    """Credit multiplier in [0, 1] for a timing offset ``delta_days`` (agent − oracle).

    Triangular (symmetric) for most actions; asymmetric for harvest.
    """
    if calib.kernel == "harvest":
        if delta_days < 0.0:  # harvested before the ideal (pre-maturity) — harsh
            return max(0.0, 1.0 - abs(delta_days) / max(calib.sigma_early, 1e-9))
        if delta_days <= calib.grace_days:  # late but within drydown grace
            return 1.0
        over = delta_days - calib.grace_days
        return max(0.0, 1.0 - over / max(calib.sigma_late, 1e-9))
    # triangular
    return max(0.0, 1.0 - abs(delta_days) / max(calib.sigma_days, 1e-9))
