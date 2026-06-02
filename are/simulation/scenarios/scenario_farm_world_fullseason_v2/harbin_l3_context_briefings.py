from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

BriefingMode = Literal[
    "false",
    "kwoo",
    "library",
    "true",
    "l2_human_same",
    "l2_human_differ",
    "l2_textsim_differ",
    "l2_textsim_grouped_differ",
    "l2_pathsim_differ",
    "l2_pathsim_grouped_differ",
    "l3_pathsim_same",
    "l3_pathsim_differ",
]

_CONTEXT_ROOT = Path(__file__).parent / "l2_l1_splits" / "knowledge_library_pilot"
_FARMING_GROUPS = {"establishment", "management", "harvest"}
_GROUPED_RETRIEVAL_QUOTAS = {
    "establishment": 1,
    "management": 2,
    "harvest": 1,
}

_SCENARIO_LIBRARY_SLUGS: dict[str, str] = {
    "scenario_full_season_hb_heihe43_early_density_weed_nutrient_recovery": "hb_heihe43_early_density_weed_nutrient_recovery",
    "scenario_full_season_hb_coldspring_planting_window_heihe50": "hb_coldspring_planting_window_heihe50",
    "scenario_full_season_hb_wetcold_high_residue_establishment": "hb_wetcold_high_residue_establishment",
    "scenario_full_season_heinong84_staggered_planting": "heinong84_staggered_planting",
    "scenario_full_season_hb_fertilizer_quota_edge_lowfertility": "hb_fertilizer_quota_edge_lowfertility",
    "scenario_full_season_hb_insect_after_fungicide_budget_conflict": "hb_insect_after_fungicide_budget_conflict",
    "scenario_full_season_hb_two_dry_patches_one_irrigation": "hb_two_dry_patches_one_irrigation",
    "scenario_full_season_hb_wetjune_shortwindow_trafficability": "hb_wetjune_shortwindow_trafficability",
    "scenario_full_season_hb_storage_capacity_limit_batching": "hb_storage_capacity_limit_batching",
    "scenario_full_season_hb_three_cultivar_wet_disease_dry_harvest_sequence": "hb_three_cultivar_wet_disease_dry_harvest_sequence",
}

_LIBRARY_BY_SCENARIO_ID: dict[str, Path] = {
    scenario_id: _CONTEXT_ROOT / slug / "library_same_l3.md"
    for scenario_id, slug in _SCENARIO_LIBRARY_SLUGS.items()
}

KWOO_CONTEXT_ENV_VAR = "FARM_ARE_KWOO_CONTEXT_PATH"
RETRIEVED_CONTEXT_ENV_VAR = "FARM_ARE_RETRIEVED_CONTEXT_PATH"
TEXTSIM_TOP_K_ENV_VAR = "FARM_ARE_TEXTSIM_TOP_K"

_MANUAL_DIFFER_SKILLS: dict[str, list[tuple[str, str]]] = {
    "hb_heihe43_early_density_weed_nutrient_recovery": [
        ("hb_coldspring_planting_window_heihe50", "heihe50_coldspring_field_prep"),
        ("hb_coldspring_planting_window_heihe50", "heihe50_coldspring_planting_window"),
        ("hb_coldspring_planting_window_heihe50", "heihe50_coldspring_emergence_scouting"),
        ("hb_coldspring_planting_window_heihe50", "heihe50_coldspring_harvest_drydown_store"),
    ],
    "hb_coldspring_planting_window_heihe50": [
        ("hb_wetcold_high_residue_establishment", "wetcold_residue_field_prep"),
        ("hb_wetcold_high_residue_establishment", "wetcold_residue_planting_window"),
        ("hb_wetcold_high_residue_establishment", "wetcold_residue_replant_recovery"),
        ("heinong84_staggered_planting", "hn84_mid_zone_planting_window"),
        ("hb_wetcold_high_residue_establishment", "wetcold_residue_staged_harvest"),
    ],
    "hb_wetcold_high_residue_establishment": [
        ("hb_coldspring_planting_window_heihe50", "heihe50_coldspring_field_prep"),
        ("hb_coldspring_planting_window_heihe50", "heihe50_coldspring_planting_window"),
        ("hb_coldspring_planting_window_heihe50", "heihe50_coldspring_emergence_scouting"),
        ("hb_fertilizer_quota_edge_lowfertility", "severe_edge_gap_replant"),
        ("heinong84_staggered_planting", "hn84_staggered_harvest_sequence"),
    ],
    "heinong84_staggered_planting": [
        ("hb_wetcold_high_residue_establishment", "wetcold_residue_field_prep"),
        ("hb_coldspring_planting_window_heihe50", "heihe50_coldspring_planting_window"),
        ("hb_three_cultivar_wet_disease_dry_harvest_sequence", "three_cultivar_planting"),
        ("hb_wetcold_high_residue_establishment", "wetcold_residue_replant_recovery"),
        ("hb_storage_capacity_limit_batching", "remaining_batch_after_west"),
    ],
    "hb_fertilizer_quota_edge_lowfertility": [
        ("hb_heihe43_early_density_weed_nutrient_recovery", "heihe43_vc_nutrient_recovery"),
        ("hb_two_dry_patches_one_irrigation", "two_patch_irrigation_priority"),
        ("hb_wetcold_high_residue_establishment", "wetcold_residue_replant_recovery"),
        ("hb_heihe43_early_density_weed_nutrient_recovery", "heihe43_recommended_density_planting"),
        ("hb_wetjune_shortwindow_trafficability", "harvest_dry_store"),
    ],
    "hb_insect_after_fungicide_budget_conflict": [
        ("hb_wetjune_shortwindow_trafficability", "wetjune_fungicide_window"),
        ("hb_three_cultivar_wet_disease_dry_harvest_sequence", "hn84_wet_disease_control"),
        ("hb_fertilizer_quota_edge_lowfertility", "mild_edge_quota_topup"),
        ("hb_storage_capacity_limit_batching", "capacity_aware_first_batch"),
        ("hb_three_cultivar_wet_disease_dry_harvest_sequence", "zone_harvest_sequence"),
    ],
    "hb_two_dry_patches_one_irrigation": [
        ("hb_three_cultivar_wet_disease_dry_harvest_sequence", "hn58_dry_water_management"),
        ("hb_fertilizer_quota_edge_lowfertility", "severe_edge_nutrient_recovery"),
        ("hb_heihe43_early_density_weed_nutrient_recovery", "heihe43_vc_weed_control"),
        ("hb_storage_capacity_limit_batching", "standard_density_planting"),
        ("hb_wetjune_shortwindow_trafficability", "harvest_dry_store"),
    ],
    "hb_wetjune_shortwindow_trafficability": [
        ("hb_insect_after_fungicide_budget_conflict", "budgeted_disease_control"),
        ("hb_three_cultivar_wet_disease_dry_harvest_sequence", "hn84_wet_disease_control"),
        ("hb_heihe43_early_density_weed_nutrient_recovery", "heihe43_vc_weed_control"),
        ("hb_storage_capacity_limit_batching", "capacity_aware_first_batch"),
        ("hb_coldspring_planting_window_heihe50", "heihe50_coldspring_harvest_drydown_store"),
    ],
    "hb_storage_capacity_limit_batching": [
        ("hb_three_cultivar_wet_disease_dry_harvest_sequence", "zone_harvest_sequence"),
        ("hb_wetcold_high_residue_establishment", "wetcold_residue_staged_harvest"),
        ("heinong84_staggered_planting", "hn84_staggered_harvest_sequence"),
        ("hb_heihe43_early_density_weed_nutrient_recovery", "heihe43_recommended_density_planting"),
        ("hb_two_dry_patches_one_irrigation", "harvest_dry_store"),
    ],
    "hb_three_cultivar_wet_disease_dry_harvest_sequence": [
        ("heinong84_staggered_planting", "hn84_field_prep_early_zone_planting"),
        ("heinong84_staggered_planting", "hn84_mid_zone_planting_window"),
        ("hb_wetjune_shortwindow_trafficability", "wetjune_fungicide_window"),
        ("hb_two_dry_patches_one_irrigation", "two_patch_irrigation_priority"),
        ("hb_storage_capacity_limit_batching", "remaining_batch_after_west"),
    ],
}


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
    if normalized in {"l2_human_same", "detail_l2_human_same"}:
        return "l2_human_same"
    if normalized in {"l2_human_differ", "detail_l2_human_differ"}:
        return "l2_human_differ"
    if normalized in {"l2_textsim_differ", "detail_l2_textsim_differ"}:
        return "l2_textsim_differ"
    if normalized in {"l2_textsim_grouped_differ", "detail_l2_textsim_grouped_differ"}:
        return "l2_textsim_grouped_differ"
    if normalized in {"l2_pathsim_differ", "detail_l2_pathsim_differ"}:
        return "l2_pathsim_differ"
    if normalized in {"l2_pathsim_grouped_differ", "detail_l2_pathsim_grouped_differ"}:
        return "l2_pathsim_grouped_differ"
    if normalized in {"l3_pathsim_same", "detail_l3_pathsim_same"}:
        return "l3_pathsim_same"
    if normalized in {"l3_pathsim_differ", "detail_l3_pathsim_differ"}:
        return "l3_pathsim_differ"
    raise ValueError(
        "detailed_briefing must be one of false, kwoo, library, true, "
        "l2_human_same, l2_human_differ, l2_textsim_differ, "
        "l2_textsim_grouped_differ, "
        "l2_pathsim_differ, l2_pathsim_grouped_differ, "
        "l3_pathsim_same, l3_pathsim_differ; "
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


def _context_slug_for_scenario(scenario_id: str) -> str | None:
    return _SCENARIO_LIBRARY_SLUGS.get(scenario_id)


def _load_library_json(slug: str) -> dict[str, Any]:
    path = _CONTEXT_ROOT / slug / "library_same_l3.json"
    if not path.is_file():
        raise FileNotFoundError(f"Skill library JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_all_skill_cards() -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for slug in sorted(set(_SCENARIO_LIBRARY_SLUGS.values())):
        try:
            payload = _load_library_json(slug)
        except FileNotFoundError:
            continue
        for skill in payload.get("skills", []):
            if not isinstance(skill, dict):
                continue
            cards.append(
                {
                    "source_slug": slug,
                    "source_l3_scenario_id": payload.get("source_l3_scenario_id", ""),
                    "skill": skill,
                }
            )
    return cards


def _format_jsonish(value: Any, *, max_len: int = 900) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _render_skill_card(
    card: dict[str, Any], *, same_l3: bool, score: float | None = None
) -> str:
    skill = card["skill"]
    source = card.get("source_slug", "")
    source_l3 = card.get("source_l3_scenario_id", "")
    source_l2 = skill.get("source_l2_scenario_id") or ""
    header = f"### Skill: {skill.get('skill_id', 'unknown')}"
    if score is not None:
        header += f" (retrieval_score={score:.4f})"
    lines = [
        header,
        f"- source_l3: `{source_l3}`",
        f"- source_library: `{source}`",
        f"- source_l2: `{source_l2}`",
        f"- farming_group: `{skill.get('farming_group', '')}`",
        f"- task_type: `{skill.get('task_type', '')}`; stage: `{skill.get('crop_stage', '')}`",
        f"- belief: {skill.get('belief', '')}",
        f"- evidence_chain: {_format_jsonish(skill.get('evidence_chain', []), max_len=700)}",
        f"- constraints: {_format_jsonish(skill.get('constraints', []), max_len=900)}",
        f"- success_checks: {_format_jsonish(skill.get('success_checks', []), max_len=700)}",
        f"- oracle_event_template: {_format_jsonish(skill.get('oracle_events', []), max_len=1400)}",
    ]
    if not same_l3:
        lines.append(
            "- transfer_note: This is an example from a different L3. Use the "
            "skill structure, evidence chain, and agronomic constraints; do not "
            "blindly copy source-L3 dates, ridges, or rates unless target tools "
            "confirm them."
        )
    return "\n".join(lines)


def _render_skill_context(
    title: str,
    cards: list[dict[str, Any]],
    *,
    same_l3: bool,
    scored: dict[tuple[str, str], float] | None = None,
) -> str:
    if not cards:
        raise ValueError(f"No skill cards selected for {title}")
    mode_note = (
        "These L2 skills are from the same source L3 and may include exact "
        "targets/parameters for the mechanism pilot."
        if same_l3
        else "These L2 skills are retrieved from different source L3 scenarios. "
        "They are in-context examples, not a target-scenario answer."
    )
    parts = [f"# {title}", "", mode_note, ""]
    for card in cards:
        skill = card["skill"]
        key = (str(card.get("source_slug", "")), str(skill.get("skill_id", "")))
        parts.append(
            _render_skill_card(
                card,
                same_l3=same_l3,
                score=scored.get(key) if scored is not None else None,
            )
        )
        parts.append("")
    return "\n".join(parts).strip()


def _skills_by_key() -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for card in _load_all_skill_cards():
        skill_id = str(card["skill"].get("skill_id", ""))
        if skill_id:
            out[(str(card["source_slug"]), skill_id)] = card
    return out


def _same_l3_skill_context(scenario_id: str) -> str:
    slug = _context_slug_for_scenario(scenario_id)
    if slug is None:
        raise FileNotFoundError(f"No same-L3 skill library registered for {scenario_id!r}")
    payload = _load_library_json(slug)
    cards = [
        {"source_slug": slug, "source_l3_scenario_id": payload.get("source_l3_scenario_id", ""), "skill": skill}
        for skill in payload.get("skills", [])
        if isinstance(skill, dict)
    ]
    return _render_skill_context("L2_HUMAN_SAME atomic skill context", cards, same_l3=True)


def _manual_differ_skill_context(scenario_id: str) -> str:
    slug = _context_slug_for_scenario(scenario_id)
    if slug is None:
        raise FileNotFoundError(f"No source slug registered for {scenario_id!r}")
    selected = _MANUAL_DIFFER_SKILLS.get(slug)
    if not selected:
        raise FileNotFoundError(f"No manual cross-L3 L2 skill selection for {scenario_id!r}")
    by_key = _skills_by_key()
    cards = [by_key[key] for key in selected if key in by_key]
    missing = [key for key in selected if key not in by_key]
    if missing:
        raise KeyError(f"Manual L2 skill selection references missing skills: {missing}")
    return _render_skill_context("L2_HUMAN_DIFFER atomic skill context", cards, same_l3=False)


def _tokenize(text: str) -> Counter[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    stop = {
        "the",
        "and",
        "or",
        "to",
        "of",
        "a",
        "an",
        "in",
        "for",
        "with",
        "by",
        "is",
        "are",
        "this",
        "that",
        "scenario",
        "field",
        "ridges",
        "ridge",
    }
    return Counter(t for t in tokens if len(t) > 1 and t not in stop)


def _text_score(query: str, card: dict[str, Any]) -> float:
    skill = card["skill"]
    skill_text = " ".join(
        [
            str(skill.get("skill_id", "")),
            str(skill.get("task_type", "")),
            str(skill.get("crop_stage", "")),
            str(skill.get("belief", "")),
            " ".join(map(str, skill.get("constraints", []) or [])),
            " ".join(map(str, skill.get("success_checks", []) or [])),
        ]
    )
    q = _tokenize(query)
    s = _tokenize(skill_text)
    if not q or not s:
        return 0.0
    overlap = sum(min(q[token], s[token]) for token in q.keys() & s.keys())
    return overlap / ((sum(q.values()) ** 0.5) * (sum(s.values()) ** 0.5))


def _target_query_text(spec: Any) -> str:
    return " ".join(
        str(getattr(spec, name, "") or "")
        for name in (
            "scenario_id",
            "profile_name",
            "description",
            "briefing_text",
            "primary_metric",
        )
    )


def _textsim_differ_skill_context(spec: Any) -> str:
    scenario_id = str(getattr(spec, "scenario_id", "") or "")
    source_slug = _context_slug_for_scenario(scenario_id)
    top_k = int(os.environ.get(TEXTSIM_TOP_K_ENV_VAR, "6"))
    query = _target_query_text(spec)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for card in _load_all_skill_cards():
        if card.get("source_slug") == source_slug:
            continue
        ranked.append((_text_score(query, card), card))
    ranked.sort(key=lambda item: item[0], reverse=True)
    selected = ranked[:top_k]
    scored = {
        (str(card.get("source_slug", "")), str(card["skill"].get("skill_id", ""))): score
        for score, card in selected
    }
    cards = [card for _, card in selected]
    return _render_skill_context(
        "L2_TEXTSIM_DIFFER atomic skill context",
        cards,
        same_l3=False,
        scored=scored,
    )


def _skill_farming_group(card: dict[str, Any]) -> str:
    skill = card.get("skill") or {}
    group = str(skill.get("farming_group") or "").strip()
    if group not in _FARMING_GROUPS:
        skill_id = str(skill.get("skill_id") or "unknown")
        source = str(card.get("source_slug") or "unknown")
        raise ValueError(
            f"Atomic skill {source}/{skill_id} must define farming_group as one "
            f"of {sorted(_FARMING_GROUPS)}; got {group!r}"
        )
    return group


def _textsim_grouped_differ_skill_context(spec: Any) -> str:
    scenario_id = str(getattr(spec, "scenario_id", "") or "")
    source_slug = _context_slug_for_scenario(scenario_id)
    query = _target_query_text(spec)
    by_group: dict[str, list[tuple[float, dict[str, Any]]]] = {
        "establishment": [],
        "management": [],
        "harvest": [],
    }
    for card in _load_all_skill_cards():
        if card.get("source_slug") == source_slug:
            continue
        score = _text_score(query, card)
        group = _skill_farming_group(card)
        by_group.setdefault(group, []).append((score, card))

    selected: list[tuple[float, dict[str, Any], str]] = []
    for group in ("establishment", "management", "harvest"):
        ranked_group = sorted(by_group.get(group, []), key=lambda item: item[0], reverse=True)
        for score, card in ranked_group[: _GROUPED_RETRIEVAL_QUOTAS[group]]:
            selected.append((score, card, group))

    cards = [card for _, card, _ in selected]
    scored = {
        (str(card.get("source_slug", "")), str(card["skill"].get("skill_id", ""))): score
        for score, card, _ in selected
    }
    context = _render_skill_context(
        "L2_TEXTSIM_GROUPED_DIFFER atomic skill context",
        cards,
        same_l3=False,
        scored=scored,
    )
    group_lines = ["", "Grouped retrieval:", ""]
    for score, card, group in selected:
        skill = card["skill"]
        group_lines.append(
            f"- `{group}` textsim_score={score:.4f}: `{skill.get('skill_id', 'unknown')}` "
            f"from `{card.get('source_slug', '')}`"
        )
    return context + "\n" + "\n".join(group_lines)


def _load_retrieved_context(scenario_id: str, mode: str) -> str:
    path = os.environ.get(RETRIEVED_CONTEXT_ENV_VAR)
    if not path:
        raise ValueError(
            f"detailed_briefing={mode!r} requires {RETRIEVED_CONTEXT_ENV_VAR} "
            "to point to a JSON context map generated by the experiment runner."
        )
    fp = Path(path).expanduser()
    if not fp.is_absolute():
        fp = Path.cwd() / fp
    raw_text = _read_required_context_file(fp, "Retrieved")
    payload = json.loads(raw_text)
    contexts = payload.get("contexts") if isinstance(payload, dict) else None
    if not isinstance(contexts, dict):
        raise ValueError(f"Retrieved context file must contain a contexts object: {fp}")
    text = str(contexts.get(scenario_id) or "").strip()
    if not text:
        raise KeyError(f"Retrieved context file has no context for {scenario_id!r}: {fp}")
    return text


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

    if mode == "l2_human_same":
        return f"{base}\n\nKnowledge library context:\n{_same_l3_skill_context(scenario_id)}"
    if mode == "l2_human_differ":
        return f"{base}\n\nKnowledge library context:\n{_manual_differ_skill_context(scenario_id)}"
    if mode == "l2_textsim_differ":
        return f"{base}\n\nKnowledge library context:\n{_textsim_differ_skill_context(spec)}"
    if mode == "l2_textsim_grouped_differ":
        return f"{base}\n\nKnowledge library context:\n{_textsim_grouped_differ_skill_context(spec)}"
    if mode in {"l2_pathsim_differ", "l2_pathsim_grouped_differ", "l3_pathsim_same", "l3_pathsim_differ"}:
        return f"{base}\n\nRetrieved workflow context:\n{_load_retrieved_context(scenario_id, mode)}"

    library_path = _LIBRARY_BY_SCENARIO_ID.get(scenario_id)
    if library_path is None or not library_path.is_file():
        raise FileNotFoundError(
            "Library context requested, but no scenario-specific skill library "
            f"is registered for {scenario_id!r}."
        )
    library_text = library_path.read_text(encoding="utf-8").strip()
    return f"{base}\n\nKnowledge library context:\n{library_text}"
