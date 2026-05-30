from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

BriefingMode = Literal["false", "kwoo", "library", "true"]

_CONTEXT_ROOT = Path(__file__).parent / "l2_l1_splits" / "knowledge_library_pilot"

_LIBRARY_BY_SCENARIO_ID: dict[str, Path] = {
    "scenario_full_season_hb_heihe43_early_density_weed_nutrient_recovery": (
        _CONTEXT_ROOT
        / "hb_heihe43_early_density_weed_nutrient_recovery"
        / "library_same_l3.md"
    ),
    "scenario_full_season_hb_coldspring_planting_window_heihe50": (
        _CONTEXT_ROOT
        / "hb_coldspring_planting_window_heihe50"
        / "library_same_l3.md"
    ),
    "scenario_full_season_hb_wetcold_high_residue_establishment": (
        _CONTEXT_ROOT
        / "hb_wetcold_high_residue_establishment"
        / "library_same_l3.md"
    ),
    "scenario_full_season_heinong84_staggered_planting": (
        _CONTEXT_ROOT / "heinong84_staggered_planting" / "library_same_l3.md"
    ),
}

KWOO_CONTEXT_ENV_VAR = "FARM_ARE_KWOO_CONTEXT_PATH"


def normalize_briefing_mode(value: Any) -> BriefingMode:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "true"
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "human", "detail_true"}:
        return "true"
    if normalized in {"0", "false", "no", "off", "none", "no_context", "detail_false"}:
        return "false"
    if normalized in {"kwoo", "kwoo_context", "detail_kwoo"}:
        return "kwoo"
    if normalized in {"library", "library_context", "detail_library"}:
        return "library"
    raise ValueError(
        "detailed_briefing must be one of false, kwoo, library, true; "
        f"got {value!r}"
    )


def _read_required_context_file(path: str | os.PathLike[str], label: str) -> str:
    fp = Path(path).expanduser()
    if not fp.is_absolute():
        fp = Path.cwd() / fp
    if not fp.is_file():
        raise FileNotFoundError(f"{label} context file not found: {fp}")
    text = fp.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"{label} context file is empty: {fp}")
    return text


def _extract_kwoo_detailed_briefing(raw_text: str, scenario_id: str, source: Path) -> str:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text.strip()

    scenarios = payload.get("scenarios") if isinstance(payload, dict) else None
    if not isinstance(scenarios, list):
        raise ValueError(
            "Kwoo JSON context must contain a 'scenarios' list when JSON is used: "
            f"{source}"
        )

    for item in scenarios:
        if not isinstance(item, dict):
            continue
        if item.get("scenario_id") != scenario_id:
            continue
        detailed = str(item.get("detailed_briefing") or "").strip()
        if not detailed:
            raise ValueError(
                "Kwoo JSON matched scenario_id but detailed_briefing is empty: "
                f"{scenario_id}"
            )
        return detailed

    raise KeyError(
        "Kwoo JSON context does not contain detailed_briefing for scenario_id "
        f"{scenario_id!r}: {source}"
    )


def _load_kwoo_context(scenario_id: str) -> str:
    path = os.environ.get(KWOO_CONTEXT_ENV_VAR)
    if not path:
        raise ValueError(
            f"detailed_briefing='kwoo' requires {KWOO_CONTEXT_ENV_VAR} to point "
            "to the Kwoo context file. No default Kwoo content is generated."
        )
    fp = Path(path).expanduser()
    if not fp.is_absolute():
        fp = Path.cwd() / fp
    raw_text = _read_required_context_file(fp, "Kwoo")
    return _extract_kwoo_detailed_briefing(raw_text, scenario_id, fp)


def build_l3_context_briefing(spec: Any, detailed_briefing: Any) -> str:
    mode = normalize_briefing_mode(detailed_briefing)
    base = str(getattr(spec, "briefing_text", "") or "")
    if mode == "false":
        return base
    if mode == "true":
        return str(getattr(spec, "detailed_briefing_text", None) or base)
    scenario_id = str(getattr(spec, "scenario_id", "") or "")
    if mode == "kwoo":
        return f"{base}\n\nKwoo context:\n{_load_kwoo_context(scenario_id)}"

    library_path = _LIBRARY_BY_SCENARIO_ID.get(scenario_id)
    if library_path is None or not library_path.is_file():
        raise FileNotFoundError(
            "Library context requested, but no scenario-specific skill library "
            f"is registered for {scenario_id!r}."
        )
    library_text = library_path.read_text(encoding="utf-8").strip()
    return f"{base}\n\nKnowledge library context:\n{library_text}"
