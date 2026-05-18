"""Run wet-June A/B zoned soybean oracle and export daily engine CSVs."""
from __future__ import annotations

import argparse
import ast
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from are.simulation.apps.app import App  # noqa: E402
from are.simulation.apps.farm_world import WeatherApp  # noqa: E402
from are.simulation.apps.farm_world.farm_world_app import (  # noqa: E402
    DEFAULT_RIDGE_WIDTH_M,
    FIELD_LENGTH_M,
    FarmWorldApp,
)
from are.simulation.environment import Environment, EnvironmentConfig  # noqa: E402
from are.simulation.notification_system import VerboseNotificationSystem  # noqa: E402
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_wet_june_ab_zoned_disease import (  # noqa: E402
    A_ZONE_END,
    AFFECTED_END,
    AFFECTED_START,
    B_ZONE_START,
    SCENARIO_ID,
    ScenarioFullSeasonWetJuneABZonedDisease,
)
from are.simulation.tool_utils import OperationType, app_tool, data_tool  # noqa: E402
from are.simulation.types import EnvironmentType, event_registered  # noqa: E402
from are.simulation.utils.type_utils import type_check  # noqa: E402
from scripts.fullseason.harbin_l3_trace_utils import _action_justifications  # noqa: E402


TRACE_APP_NAME = "WetJuneABDailyStateTrace"


class WetJuneABDailyStateTraceApp(App):
    """Trace-only probe used by this export script, not by the scenario."""

    def __init__(
        self,
        farm_world_app: FarmWorldApp,
        weather_app: WeatherApp,
        name: str = TRACE_APP_NAME,
    ) -> None:
        super().__init__(name=name)
        self._farm_world_app = farm_world_app
        self._weather_app = weather_app

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def capture_daily_state(
        self,
        label: str,
        include_ridge_details: bool = True,
    ) -> dict[str, Any]:
        advance_result = self._farm_world_app.advance_physics_time()
        weather = self._weather_app.get_current_weather_snapshot()
        now = datetime_from_timestamp(float(self._farm_world_app.time_manager.time()))

        payload: dict[str, Any] = {
            "label": label,
            "sim_datetime_utc": now,
            "weather": weather,
            "advance_result": advance_result,
        }
        physics = getattr(self._farm_world_app, "_physics", None)
        if physics is None or not getattr(physics, "engines_active", False):
            payload["physics_active"] = False
            return payload

        ridge_ids = sorted(physics.soil.states.keys())
        soil_params = getattr(physics.soil, "params", None)
        wilting = float(getattr(soil_params, "wilting_point_vwc", 0.14))
        stress_threshold = float(getattr(soil_params, "water_stress_vwc", 0.18))
        fungicide_counts = self._fungicide_application_counts(physics)

        field_acc = _new_accumulator()
        zone_acc = {
            "A_heinong84_standard": _new_accumulator(),
            "B_heinong60_high_density": _new_accumulator(),
            "B_affected_40_55": _new_accumulator(),
            "B_unaffected": _new_accumulator(),
        }
        ridges: list[dict[str, Any]] = []
        for rid in ridge_ids:
            soil = physics.soil.states[rid]
            phen = physics.phenology.states[rid]
            canopy = physics.canopy.states[rid]
            mgmt = physics.management.states[rid]
            biotic = physics.biotic.states[rid]
            yld = physics.yield_recovery.states[rid]
            ridge = self._farm_world_app.get_ridge(rid)

            root_vwc = float(soil.root_vwc)
            if root_vwc >= stress_threshold:
                water_stress = 1.0
            elif root_vwc <= wilting:
                water_stress = 0.0
            else:
                water_stress = max(
                    0.0,
                    min(1.0, (root_vwc - wilting) / (stress_threshold - wilting)),
                )

            stage = getattr(phen.stage, "value", str(phen.stage))
            sample = {
                "stage": stage,
                "top_vwc": float(soil.top_vwc),
                "root_vwc": root_vwc,
                "water_stress": water_stress,
                "lai": float(canopy.lai),
                "ndvi_proxy": float(canopy.ndvi_proxy),
                "nutrient_index": float(mgmt.nutrient_index),
                "nutrient_stress": float(mgmt.nutrient_stress),
                "weed_pressure": float(biotic.weed_pressure),
                "insect_pressure": float(biotic.insect_pressure),
                "disease_pressure": float(biotic.disease_pressure),
                "biotic_fungicide_residual_days_left": float(
                    biotic.fungicide_residual_days_left
                ),
                "management_fungicide_residual_days_left": float(
                    mgmt.fungicide_residual_days_left
                ),
                "fungicide_application_count": float(fungicide_counts.get(rid, 0)),
                "yield_potential_g_m2": float(canopy.yield_potential_g_m2),
                "grain_moisture_frac": yld.grain_moisture_frac,
            }
            _add_sample(field_acc, sample)
            zone_name = _zone_name(rid)
            _add_sample(zone_acc[zone_name], sample)
            if AFFECTED_START <= rid <= AFFECTED_END:
                _add_sample(zone_acc["B_affected_40_55"], sample)
            elif rid >= B_ZONE_START:
                _add_sample(zone_acc["B_unaffected"], sample)

            if include_ridge_details:
                ridges.append(
                    {
                        "ridge_id": rid,
                        "zone": zone_name,
                        "seed_type": ridge.seed_type,
                        "seed_spacing_cm": ridge.seed_spacing_cm,
                        "seeds_planted": int(ridge.seeds_planted),
                        "stage": stage,
                        "days_after_planting": int(phen.days_after_planting),
                        "top_vwc": round(float(soil.top_vwc), 4),
                        "root_vwc": round(root_vwc, 4),
                        "water_stress": round(water_stress, 4),
                        "top_temp_c": round(float(soil.top_temp_c), 2),
                        "lai": round(float(canopy.lai), 4),
                        "canopy_cover": round(float(canopy.canopy_cover), 4),
                        "ndvi_proxy": round(float(canopy.ndvi_proxy), 4),
                        "aboveground_biomass_g_m2": round(
                            float(canopy.aboveground_biomass_g_m2),
                            4,
                        ),
                        "yield_potential_g_m2": round(
                            float(canopy.yield_potential_g_m2),
                            4,
                        ),
                        "nutrient_index": round(float(mgmt.nutrient_index), 4),
                        "nutrient_stress": round(float(mgmt.nutrient_stress), 4),
                        "stand_fraction": round(float(mgmt.stand_fraction), 4),
                        "weed_pressure": round(float(biotic.weed_pressure), 4),
                        "insect_pressure": round(float(biotic.insect_pressure), 4),
                        "disease_pressure": round(float(biotic.disease_pressure), 4),
                        "biotic_fungicide_residual_days_left": int(
                            biotic.fungicide_residual_days_left
                        ),
                        "management_fungicide_residual_days_left": int(
                            mgmt.fungicide_residual_days_left
                        ),
                        "fungicide_applied_in_tick": "fungicide_applied"
                        in getattr(biotic, "tags", []),
                        "fungicide_applied_by_action_log": fungicide_counts.get(
                            rid,
                            0,
                        )
                        > 0,
                        "fungicide_application_count": fungicide_counts.get(rid, 0),
                        "biotic_tags": list(getattr(biotic, "tags", [])),
                        "management_tags": list(getattr(mgmt, "tags", [])),
                        "grain_moisture_frac": (
                            round(float(yld.grain_moisture_frac), 4)
                            if yld.grain_moisture_frac is not None
                            else None
                        ),
                        "biological_yield_g_m2": round(
                            float(yld.biological_yield_g_m2),
                            4,
                        ),
                        "recovered_yield_g_m2": round(
                            float(yld.recovered_yield_g_m2_at_market_moisture),
                            4,
                        ),
                    }
                )

        payload.update(
            {
                "physics_active": True,
                "field_summary": _summary(field_acc),
                "zone_summaries": {
                    name: _summary(acc) for name, acc in zone_acc.items()
                },
            }
        )
        if include_ridge_details:
            payload["ridges"] = ridges
        return payload

    def _fungicide_application_counts(self, physics: Any) -> dict[int, int]:
        counts: dict[int, int] = {}
        now = float(self._farm_world_app.time_manager.time())
        for action in getattr(physics, "action_log", []):
            if getattr(action, "action_type", "") != "fungicide":
                continue
            if float(getattr(action, "timestamp", 0.0)) > now:
                continue
            for ridge_id in getattr(action, "ridge_ids", []):
                counts[int(ridge_id)] = counts.get(int(ridge_id), 0) + 1
        return counts


class TraceScenario(ScenarioFullSeasonWetJuneABZonedDisease):
    """Trace-only subclass that instruments the clean scenario event graph."""

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        super().init_and_populate_apps(*args, **kwargs)
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        self.apps.append(
            WetJuneABDailyStateTraceApp(
                farm_world_app=farm_world,
                weather_app=weather,
            )
        )

    def _after_daily_advance(self, prev: Any, label: str) -> Any:
        return self._capture(prev, label)

    def _after_named_step(self, prev: Any, label: str) -> Any:
        return self._capture(prev, label)

    def _capture(self, prev: Any, label: str) -> Any:
        trace = self.get_typed_app(WetJuneABDailyStateTraceApp, TRACE_APP_NAME)
        return (
            trace.capture_daily_state(label, True)
            .oracle()
            .with_id(f"trace_{label}")
            .depends_on(prev, delay_seconds=1)
        )


def datetime_from_timestamp(timestamp: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _zone_name(ridge_id: int) -> str:
    if ridge_id <= A_ZONE_END:
        return "A_heinong84_standard"
    return "B_heinong60_high_density"


def _new_accumulator() -> dict[str, Any]:
    return {
        "count": 0,
        "stage_counts": {},
        "top_vwc": [],
        "root_vwc": [],
        "water_stress": [],
        "lai": [],
        "ndvi_proxy": [],
        "nutrient_index": [],
        "nutrient_stress": [],
        "weed_pressure": [],
        "insect_pressure": [],
        "disease_pressure": [],
        "biotic_fungicide_residual_days_left": [],
        "management_fungicide_residual_days_left": [],
        "fungicide_application_count": [],
        "yield_potential_g_m2": [],
        "grain_moisture_frac": [],
    }


def _add_sample(acc: dict[str, Any], sample: dict[str, Any]) -> None:
    acc["count"] += 1
    stage = sample["stage"]
    acc["stage_counts"][stage] = acc["stage_counts"].get(stage, 0) + 1
    for key in (
        "top_vwc",
        "root_vwc",
        "water_stress",
        "lai",
        "ndvi_proxy",
        "nutrient_index",
        "nutrient_stress",
        "weed_pressure",
        "insect_pressure",
        "disease_pressure",
        "biotic_fungicide_residual_days_left",
        "management_fungicide_residual_days_left",
        "fungicide_application_count",
        "yield_potential_g_m2",
    ):
        acc[key].append(float(sample[key]))
    if sample["grain_moisture_frac"] is not None:
        acc["grain_moisture_frac"].append(float(sample["grain_moisture_frac"]))


def _summary(acc: dict[str, Any]) -> dict[str, Any]:
    def avg(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    def min_or_zero(values: list[float]) -> float:
        return round(min(values), 4) if values else 0.0

    def max_or_zero(values: list[float]) -> float:
        return round(max(values), 4) if values else 0.0

    fungicide_counts = acc["fungicide_application_count"]
    return {
        "ridge_count": acc["count"],
        "stage_counts": acc["stage_counts"],
        "avg_top_vwc": avg(acc["top_vwc"]),
        "min_top_vwc": min_or_zero(acc["top_vwc"]),
        "avg_root_vwc": avg(acc["root_vwc"]),
        "min_root_vwc": min_or_zero(acc["root_vwc"]),
        "avg_water_stress": avg(acc["water_stress"]),
        "min_water_stress": min_or_zero(acc["water_stress"]),
        "avg_lai": avg(acc["lai"]),
        "max_lai": max_or_zero(acc["lai"]),
        "avg_ndvi_proxy": avg(acc["ndvi_proxy"]),
        "min_ndvi_proxy": min_or_zero(acc["ndvi_proxy"]),
        "avg_nutrient_index": avg(acc["nutrient_index"]),
        "min_nutrient_index": min_or_zero(acc["nutrient_index"]),
        "min_nutrient_stress": min_or_zero(acc["nutrient_stress"]),
        "max_weed_pressure": max_or_zero(acc["weed_pressure"]),
        "max_disease_pressure": max_or_zero(acc["disease_pressure"]),
        "max_insect_pressure": max_or_zero(acc["insect_pressure"]),
        "max_biotic_fungicide_residual_days_left": max_or_zero(
            acc["biotic_fungicide_residual_days_left"]
        ),
        "max_management_fungicide_residual_days_left": max_or_zero(
            acc["management_fungicide_residual_days_left"]
        ),
        "fungicide_applied_ridge_count": int(
            sum(1 for value in fungicide_counts if value > 0.0)
        ),
        "max_fungicide_application_count": int(max(fungicide_counts))
        if fungicide_counts
        else 0,
        "avg_yield_potential_g_m2": avg(acc["yield_potential_g_m2"]),
        "max_yield_potential_g_m2": max_or_zero(acc["yield_potential_g_m2"]),
        "avg_grain_moisture_frac": avg(acc["grain_moisture_frac"]),
    }

FIELD_COLUMNS = [
    "event_id",
    "trace_index",
    "label",
    "sim_datetime_utc",
    "weather_date",
    "weather_temp_c",
    "weather_humidity_pct",
    "weather_wind_speed_ms",
    "weather_rainfall_mm",
    "weather_solar_radiation",
    "physics_status",
    "day_ticks_run",
    "subdaily_irrigation",
    "elapsed_s",
    "stage_counts_json",
    "zone_summaries_json",
    "avg_top_vwc",
    "min_top_vwc",
    "avg_root_vwc",
    "min_root_vwc",
    "avg_water_stress",
    "min_water_stress",
    "avg_lai",
    "max_lai",
    "avg_ndvi_proxy",
    "min_ndvi_proxy",
    "avg_nutrient_index",
    "min_nutrient_index",
    "min_nutrient_stress",
    "max_weed_pressure",
    "max_disease_pressure",
    "max_insect_pressure",
    "max_biotic_fungicide_residual_days_left",
    "max_management_fungicide_residual_days_left",
    "fungicide_applied_ridge_count",
    "max_fungicide_application_count",
    "avg_yield_potential_g_m2",
    "max_yield_potential_g_m2",
    "avg_grain_moisture_frac",
]

RIDGE_COLUMNS = [
    "event_id",
    "trace_index",
    "label",
    "sim_datetime_utc",
    "weather_date",
    "ridge_id",
    "zone",
    "seed_type",
    "seed_spacing_cm",
    "seeds_planted",
    "stage",
    "days_after_planting",
    "top_vwc",
    "root_vwc",
    "water_stress",
    "top_temp_c",
    "lai",
    "canopy_cover",
    "ndvi_proxy",
    "aboveground_biomass_g_m2",
    "yield_potential_g_m2",
    "nutrient_index",
    "nutrient_stress",
    "stand_fraction",
    "weed_pressure",
    "insect_pressure",
    "disease_pressure",
    "biotic_fungicide_residual_days_left",
    "management_fungicide_residual_days_left",
    "fungicide_applied_in_tick",
    "fungicide_applied_by_action_log",
    "fungicide_application_count",
    "biotic_tags_json",
    "management_tags_json",
    "grain_moisture_frac",
    "biological_yield_g_m2",
    "recovered_yield_g_m2",
]


def _simplify(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "..."
    if isinstance(value, dict):
        return {str(k): _simplify(v, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_simplify(v, depth=depth + 1) for v in value]
    if hasattr(value, "to_dict"):
        try:
            return _simplify(value.to_dict(), depth=depth + 1)
        except Exception:
            return repr(value)
    if hasattr(value, "__dict__") and value.__class__.__module__.startswith("are."):
        return _simplify(vars(value), depth=depth + 1)
    return value


def _parse_return_value(value: Any) -> dict[str, Any] | None:
    value = _simplify(value)
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_daily_trace_event(event: Any) -> bool:
    app = event.app_name() or event.app_class_name() or ""
    fn = event.function_name() or ""
    return app == TRACE_APP_NAME and fn == "capture_daily_state"


def _trace_payloads(events: list[Any]) -> list[tuple[str, dict[str, Any]]]:
    traces: list[tuple[str, dict[str, Any]]] = []
    for event in events:
        if event.failed() or not _is_daily_trace_event(event):
            continue
        data = _parse_return_value(event.metadata.return_value)
        if data is not None:
            traces.append((str(event.event_id), data))
    return traces


def _field_row(event_id: str, trace_index: int, payload: dict[str, Any]) -> dict[str, Any]:
    weather = payload.get("weather") or {}
    advance = payload.get("advance_result") or {}
    summary = payload.get("field_summary") or {}
    return {
        "event_id": event_id,
        "trace_index": trace_index,
        "label": payload.get("label"),
        "sim_datetime_utc": payload.get("sim_datetime_utc"),
        "weather_date": weather.get("date"),
        "weather_temp_c": weather.get("temp_c"),
        "weather_humidity_pct": weather.get("humidity_pct"),
        "weather_wind_speed_ms": weather.get("wind_speed_ms"),
        "weather_rainfall_mm": weather.get("rainfall_mm"),
        "weather_solar_radiation": weather.get("solar_radiation"),
        "physics_status": advance.get("status"),
        "day_ticks_run": advance.get("day_ticks_run"),
        "subdaily_irrigation": advance.get("subdaily_irrigation"),
        "elapsed_s": advance.get("elapsed_s"),
        "stage_counts_json": json.dumps(
            summary.get("stage_counts", {}),
            ensure_ascii=False,
            sort_keys=True,
        ),
        "zone_summaries_json": json.dumps(
            payload.get("zone_summaries", {}),
            ensure_ascii=False,
            sort_keys=True,
        ),
        "avg_top_vwc": summary.get("avg_top_vwc"),
        "min_top_vwc": summary.get("min_top_vwc"),
        "avg_root_vwc": summary.get("avg_root_vwc"),
        "min_root_vwc": summary.get("min_root_vwc"),
        "avg_water_stress": summary.get("avg_water_stress"),
        "min_water_stress": summary.get("min_water_stress"),
        "avg_lai": summary.get("avg_lai"),
        "max_lai": summary.get("max_lai"),
        "avg_ndvi_proxy": summary.get("avg_ndvi_proxy"),
        "min_ndvi_proxy": summary.get("min_ndvi_proxy"),
        "avg_nutrient_index": summary.get("avg_nutrient_index"),
        "min_nutrient_index": summary.get("min_nutrient_index"),
        "min_nutrient_stress": summary.get("min_nutrient_stress"),
        "max_weed_pressure": summary.get("max_weed_pressure"),
        "max_disease_pressure": summary.get("max_disease_pressure"),
        "max_insect_pressure": summary.get("max_insect_pressure"),
        "max_biotic_fungicide_residual_days_left": summary.get(
            "max_biotic_fungicide_residual_days_left"
        ),
        "max_management_fungicide_residual_days_left": summary.get(
            "max_management_fungicide_residual_days_left"
        ),
        "fungicide_applied_ridge_count": summary.get("fungicide_applied_ridge_count"),
        "max_fungicide_application_count": summary.get(
            "max_fungicide_application_count"
        ),
        "avg_yield_potential_g_m2": summary.get("avg_yield_potential_g_m2"),
        "max_yield_potential_g_m2": summary.get("max_yield_potential_g_m2"),
        "avg_grain_moisture_frac": summary.get("avg_grain_moisture_frac"),
    }


def _ridge_rows(
    event_id: str,
    trace_index: int,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    weather = payload.get("weather") or {}
    rows: list[dict[str, Any]] = []
    for ridge in payload.get("ridges", []) or []:
        rows.append(
            {
                "event_id": event_id,
                "trace_index": trace_index,
                "label": payload.get("label"),
                "sim_datetime_utc": payload.get("sim_datetime_utc"),
                "weather_date": weather.get("date"),
                "ridge_id": ridge.get("ridge_id"),
                "zone": ridge.get("zone"),
                "seed_type": ridge.get("seed_type"),
                "seed_spacing_cm": ridge.get("seed_spacing_cm"),
                "seeds_planted": ridge.get("seeds_planted"),
                "stage": ridge.get("stage"),
                "days_after_planting": ridge.get("days_after_planting"),
                "top_vwc": ridge.get("top_vwc"),
                "root_vwc": ridge.get("root_vwc"),
                "water_stress": ridge.get("water_stress"),
                "top_temp_c": ridge.get("top_temp_c"),
                "lai": ridge.get("lai"),
                "canopy_cover": ridge.get("canopy_cover"),
                "ndvi_proxy": ridge.get("ndvi_proxy"),
                "aboveground_biomass_g_m2": ridge.get("aboveground_biomass_g_m2"),
                "yield_potential_g_m2": ridge.get("yield_potential_g_m2"),
                "nutrient_index": ridge.get("nutrient_index"),
                "nutrient_stress": ridge.get("nutrient_stress"),
                "stand_fraction": ridge.get("stand_fraction"),
                "weed_pressure": ridge.get("weed_pressure"),
                "insect_pressure": ridge.get("insect_pressure"),
                "disease_pressure": ridge.get("disease_pressure"),
                "biotic_fungicide_residual_days_left": ridge.get(
                    "biotic_fungicide_residual_days_left"
                ),
                "management_fungicide_residual_days_left": ridge.get(
                    "management_fungicide_residual_days_left"
                ),
                "fungicide_applied_in_tick": ridge.get("fungicide_applied_in_tick"),
                "fungicide_applied_by_action_log": ridge.get(
                    "fungicide_applied_by_action_log"
                ),
                "fungicide_application_count": ridge.get("fungicide_application_count"),
                "biotic_tags_json": json.dumps(
                    ridge.get("biotic_tags", []),
                    ensure_ascii=False,
                ),
                "management_tags_json": json.dumps(
                    ridge.get("management_tags", []),
                    ensure_ascii=False,
                ),
                "grain_moisture_frac": ridge.get("grain_moisture_frac"),
                "biological_yield_g_m2": ridge.get("biological_yield_g_m2"),
                "recovered_yield_g_m2": ridge.get("recovered_yield_g_m2"),
            }
        )
    return rows


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _yield_summary(scenario: Any) -> dict[str, Any]:
    farm_world = scenario.get_typed_app(FarmWorldApp)
    physics = getattr(farm_world, "_physics", None)
    if physics is None or not getattr(physics, "engines_active", False):
        return {"physics_active": False}

    ridge_area_m2 = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M
    bio_total = 0.0
    rec_total = 0.0
    planted = 0
    r8 = 0
    harvested = 0
    zone_totals: dict[str, dict[str, float]] = {
        "A_heinong84_standard": {"biological_kg": 0.0, "recovered_kg": 0.0},
        "B_heinong60_high_density": {"biological_kg": 0.0, "recovered_kg": 0.0},
        "B_affected_40_55": {"biological_kg": 0.0, "recovered_kg": 0.0},
    }

    for rid in sorted(physics.yield_recovery.states.keys()):
        yld = physics.yield_recovery.states[rid]
        phen = physics.phenology.states.get(rid)
        biological = float(yld.biological_yield_g_m2) * ridge_area_m2 / 1000.0
        recovered = (
            float(yld.recovered_yield_g_m2_at_market_moisture)
            * ridge_area_m2
            / 1000.0
        )
        bio_total += biological
        rec_total += recovered
        zone = "A_heinong84_standard" if rid <= 31 else "B_heinong60_high_density"
        zone_totals[zone]["biological_kg"] += biological
        zone_totals[zone]["recovered_kg"] += recovered
        if 40 <= rid <= 55:
            zone_totals["B_affected_40_55"]["biological_kg"] += biological
            zone_totals["B_affected_40_55"]["recovered_kg"] += recovered
        if phen is not None and getattr(phen, "planted", False):
            planted += 1
        if getattr(yld, "r8_reached", False):
            r8 += 1
        if getattr(yld, "harvested", False):
            harvested += 1

    rounded_zones = {
        zone: {key: round(value, 2) for key, value in values.items()}
        for zone, values in zone_totals.items()
    }
    return {
        "physics_active": True,
        "biological_yield_kg_total": round(bio_total, 2),
        "recovered_yield_kg_total": round(rec_total, 2),
        "recovered_yield_kg_ha": round(rec_total / (64 * ridge_area_m2) * 10000.0, 2),
        "recovered_yield_kg_mu": round(rec_total / (64 * ridge_area_m2) * 666.6667, 2),
        "zone_totals": rounded_zones,
        "ridges_planted": planted,
        "ridges_r8": r8,
        "ridges_harvested": harvested,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--field-csv",
        type=Path,
        default=Path("docs/ai/wet_june_ab_zoned_daily_field_summary.csv"),
    )
    parser.add_argument(
        "--ridge-csv",
        type=Path,
        default=Path("docs/ai/wet_june_ab_zoned_daily_ridge_states.csv"),
    )
    parser.add_argument(
        "--trace-json",
        type=Path,
        default=Path("docs/ai/wet_june_ab_zoned_oracle_trace.json"),
    )
    args = parser.parse_args()

    scenario = TraceScenario()
    scenario.initialize()

    env_config = EnvironmentConfig(
        oracle_mode=True,
        queue_based_loop=True,
        time_increment_in_seconds=getattr(scenario, "time_increment_in_seconds", 1),
        exit_when_no_events=True,
        start_time=scenario.start_time,
        duration=scenario.duration,
    )
    env = Environment(
        environment_type=EnvironmentType.CLI,
        config=env_config,
        notification_system=VerboseNotificationSystem(),
    )

    started = time.time()
    env.run(scenario, wait_for_end=False)
    env.join()
    events = env.event_log.list_view()
    traces = _trace_payloads(events)
    if not traces:
        raise SystemExit(f"No {TRACE_APP_NAME} payloads were captured")

    field_rows: list[dict[str, Any]] = []
    ridge_rows: list[dict[str, Any]] = []
    for trace_index, (event_id, payload) in enumerate(traces, start=1):
        field_rows.append(_field_row(event_id, trace_index, payload))
        ridge_rows.extend(_ridge_rows(event_id, trace_index, payload))

    _write_csv(args.field_csv, FIELD_COLUMNS, field_rows)
    _write_csv(args.ridge_csv, RIDGE_COLUMNS, ridge_rows)

    trace_payload = {
        "scenario_id": SCENARIO_ID,
        "completed_events": [
            {
                "event_id": str(event.event_id),
                "app": event.app_name() or event.app_class_name(),
                "function": event.function_name(),
                "failed": event.failed(),
                "return_value": _simplify(getattr(event.metadata, "return_value", None)),
                "exception": (
                    str(getattr(event.metadata, "exception", ""))
                    if event.failed()
                    else None
                ),
            }
            for event in events
        ],
        "action_justifications": _action_justifications(events),
    }
    args.trace_json.parent.mkdir(parents=True, exist_ok=True)
    args.trace_json.write_text(
        json.dumps(trace_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    failed_events = [
        {
            "event_id": str(event.event_id),
            "app": event.app_name() or event.app_class_name(),
            "function": event.function_name(),
            "exception": str(getattr(event.metadata, "exception", "")),
        }
        for event in events
        if event.failed()
    ]
    error_returns = []
    for event in events:
        value = _simplify(getattr(event.metadata, "return_value", None))
        if isinstance(value, dict) and "error" in value:
            error_returns.append(
                {
                    "event_id": str(event.event_id),
                    "app": event.app_name() or event.app_class_name(),
                    "function": event.function_name(),
                    "error": value["error"],
                }
            )
    summary = {
        "scenario_id": SCENARIO_ID,
        "trace_events": len(traces),
        "field_csv": str(args.field_csv),
        "field_rows": len(field_rows),
        "ridge_csv": str(args.ridge_csv),
        "ridge_rows": len(ridge_rows),
        "trace_json": str(args.trace_json),
        "events_completed": len(events),
        "failed_events": failed_events[:10],
        "failed_event_count": len(failed_events),
        "error_returns": error_returns[:10],
        "error_return_count": len(error_returns),
        "duration_s": round(time.time() - started, 2),
        "yield": _yield_summary(scenario),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    try:
        env.stop()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
