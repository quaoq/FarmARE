"""Run fast-draining dry-patch irrigation oracle and export daily engine CSVs."""
from __future__ import annotations

import argparse
import ast
import csv
import json
import sys
import time
from datetime import datetime, timezone
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
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_fastdraining_dry_patch_irrigation import (  # noqa: E402
    AFFECTED_END,
    AFFECTED_START,
    REFERENCE_EAST_END,
    REFERENCE_EAST_START,
    REFERENCE_WEST_END,
    REFERENCE_WEST_START,
    SCENARIO_ID,
    ScenarioFullSeasonFastDrainingDryPatchIrrigation,
)
from are.simulation.tool_utils import OperationType, app_tool, data_tool  # noqa: E402
from are.simulation.types import EnvironmentType, event_registered  # noqa: E402
from are.simulation.utils.type_utils import type_check  # noqa: E402
from scripts.fullseason.harbin_l3_trace_utils import _action_justifications  # noqa: E402


TRACE_APP_NAME = "FastDrainingDryPatchDailyTrace"

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
    "avg_recent_irrigation_mm",
    "max_recent_irrigation_mm",
    "avg_cumulative_irrigation_mm",
    "max_cumulative_irrigation_mm",
    "avg_canopy_temp_proxy_c",
    "max_canopy_temp_proxy_c",
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
    "stage",
    "days_after_planting",
    "top_vwc",
    "root_vwc",
    "water_stress",
    "top_temp_c",
    "canopy_temp_proxy_c",
    "recent_irrigation_mm",
    "cumulative_irrigation_mm",
    "nutrient_index",
    "nutrient_stress",
    "stand_fraction",
    "weed_pressure",
    "insect_pressure",
    "disease_pressure",
    "lai",
    "canopy_cover",
    "ndvi_proxy",
    "aboveground_biomass_g_m2",
    "yield_potential_g_m2",
    "grain_moisture_frac",
    "biological_yield_g_m2",
    "recovered_yield_g_m2",
    "soil_tags_json",
    "biotic_tags_json",
    "management_tags_json",
]


class FastDrainingDryPatchDailyTraceApp(App):
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
        now = datetime.fromtimestamp(
            float(self._farm_world_app.time_manager.time()),
            tz=timezone.utc,
        ).isoformat()

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

        soil_params = getattr(physics.soil, "params", None)
        wilting = float(getattr(soil_params, "wilting_point_vwc", 0.14))
        stress_threshold = float(getattr(soil_params, "water_stress_vwc", 0.18))
        air_temp = float(weather.get("temp_c") or 0.0)

        field_acc = _new_accumulator()
        zone_acc = {
            "affected_20_31": _new_accumulator(),
            "reference_west_0_11": _new_accumulator(),
            "reference_east_44_53": _new_accumulator(),
            "whole_field": _new_accumulator(),
        }
        ridges: list[dict[str, Any]] = []

        for rid in sorted(physics.soil.states.keys()):
            soil = physics.soil.states[rid]
            phen = physics.phenology.states[rid]
            canopy = physics.canopy.states[rid]
            mgmt = physics.management.states[rid]
            biotic = physics.biotic.states[rid]
            yld = physics.yield_recovery.states[rid]

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
            canopy_temp_proxy = air_temp + max(0.0, 1.0 - water_stress) * 4.0

            sample = {
                "stage": getattr(phen.stage, "value", str(phen.stage)),
                "top_vwc": float(soil.top_vwc),
                "root_vwc": root_vwc,
                "water_stress": water_stress,
                "top_temp_c": float(soil.top_temp_c),
                "canopy_temp_proxy_c": canopy_temp_proxy,
                "recent_irrigation_mm": float(mgmt.recent_irrigation_mm),
                "cumulative_irrigation_mm": float(mgmt.cumulative_irrigation_mm),
                "nutrient_index": float(mgmt.nutrient_index),
                "nutrient_stress": float(mgmt.nutrient_stress),
                "stand_fraction": float(mgmt.stand_fraction),
                "weed_pressure": float(biotic.weed_pressure),
                "insect_pressure": float(biotic.insect_pressure),
                "disease_pressure": float(biotic.disease_pressure),
                "lai": float(canopy.lai),
                "canopy_cover": float(canopy.canopy_cover),
                "ndvi_proxy": float(canopy.ndvi_proxy),
                "aboveground_biomass_g_m2": float(canopy.aboveground_biomass_g_m2),
                "yield_potential_g_m2": float(canopy.yield_potential_g_m2),
                "grain_moisture_frac": yld.grain_moisture_frac,
                "biological_yield_g_m2": float(yld.biological_yield_g_m2),
                "recovered_yield_g_m2": float(
                    yld.recovered_yield_g_m2_at_market_moisture
                ),
            }
            _add_sample(field_acc, sample)
            for zone in _zones_for_ridge(rid):
                _add_sample(zone_acc[zone], sample)

            if include_ridge_details:
                ridges.append(
                    {
                        "ridge_id": rid,
                        "zone": _primary_zone_name(rid),
                        "days_after_planting": int(phen.days_after_planting),
                        "soil_tags": list(soil.tags),
                        "biotic_tags": list(biotic.tags),
                        "management_tags": list(mgmt.tags),
                        **_round_sample(sample),
                    }
                )

        payload["field_summary"] = _summarize(field_acc)
        payload["zone_summaries"] = {
            zone: _summarize(acc) for zone, acc in zone_acc.items()
        }
        if include_ridge_details:
            payload["ridges"] = ridges
        return payload


class TraceScenario(ScenarioFullSeasonFastDrainingDryPatchIrrigation):
    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        super().init_and_populate_apps(*args, **kwargs)
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        self._trace_app = FastDrainingDryPatchDailyTraceApp(
            farm_world_app=farm_world,
            weather_app=weather,
        )
        self.apps.append(self._trace_app)

    def _after_daily_advance(self, prev: Any, label: str) -> Any:
        return (
            self._trace_app.capture_daily_state(label, True)
            .oracle()
            .with_id(f"trace_{label}")
            .depends_on(prev, delay_seconds=1)
        )

    def _after_named_step(self, prev: Any, label: str) -> Any:
        return (
            self._trace_app.capture_daily_state(label, True)
            .oracle()
            .with_id(f"trace_{label}")
            .depends_on(prev, delay_seconds=1)
        )


def _new_accumulator() -> dict[str, Any]:
    return {
        "count": 0,
        "stage_counts": {},
        "top_vwc": [],
        "root_vwc": [],
        "water_stress": [],
        "recent_irrigation_mm": [],
        "cumulative_irrigation_mm": [],
        "canopy_temp_proxy_c": [],
        "lai": [],
        "ndvi_proxy": [],
        "nutrient_index": [],
        "nutrient_stress": [],
        "weed_pressure": [],
        "insect_pressure": [],
        "disease_pressure": [],
        "yield_potential_g_m2": [],
        "grain_moisture_frac": [],
    }


def _add_sample(acc: dict[str, Any], sample: dict[str, Any]) -> None:
    acc["count"] += 1
    stage = str(sample["stage"])
    acc["stage_counts"][stage] = acc["stage_counts"].get(stage, 0) + 1
    for key in [
        "top_vwc",
        "root_vwc",
        "water_stress",
        "recent_irrigation_mm",
        "cumulative_irrigation_mm",
        "canopy_temp_proxy_c",
        "lai",
        "ndvi_proxy",
        "nutrient_index",
        "nutrient_stress",
        "weed_pressure",
        "insect_pressure",
        "disease_pressure",
        "yield_potential_g_m2",
    ]:
        acc[key].append(float(sample[key]))
    if sample.get("grain_moisture_frac") is not None:
        acc["grain_moisture_frac"].append(float(sample["grain_moisture_frac"]))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _min(values: list[float]) -> float:
    return min(values) if values else 0.0


def _max(values: list[float]) -> float:
    return max(values) if values else 0.0


def _summarize(acc: dict[str, Any]) -> dict[str, Any]:
    return {
        "ridge_count": acc["count"],
        "stage_counts": dict(acc["stage_counts"]),
        "avg_top_vwc": round(_mean(acc["top_vwc"]), 4),
        "min_top_vwc": round(_min(acc["top_vwc"]), 4),
        "avg_root_vwc": round(_mean(acc["root_vwc"]), 4),
        "min_root_vwc": round(_min(acc["root_vwc"]), 4),
        "avg_water_stress": round(_mean(acc["water_stress"]), 4),
        "min_water_stress": round(_min(acc["water_stress"]), 4),
        "avg_recent_irrigation_mm": round(_mean(acc["recent_irrigation_mm"]), 4),
        "max_recent_irrigation_mm": round(_max(acc["recent_irrigation_mm"]), 4),
        "avg_cumulative_irrigation_mm": round(_mean(acc["cumulative_irrigation_mm"]), 4),
        "max_cumulative_irrigation_mm": round(_max(acc["cumulative_irrigation_mm"]), 4),
        "avg_canopy_temp_proxy_c": round(_mean(acc["canopy_temp_proxy_c"]), 4),
        "max_canopy_temp_proxy_c": round(_max(acc["canopy_temp_proxy_c"]), 4),
        "avg_lai": round(_mean(acc["lai"]), 4),
        "max_lai": round(_max(acc["lai"]), 4),
        "avg_ndvi_proxy": round(_mean(acc["ndvi_proxy"]), 4),
        "min_ndvi_proxy": round(_min(acc["ndvi_proxy"]), 4),
        "avg_nutrient_index": round(_mean(acc["nutrient_index"]), 4),
        "min_nutrient_index": round(_min(acc["nutrient_index"]), 4),
        "min_nutrient_stress": round(_min(acc["nutrient_stress"]), 4),
        "max_weed_pressure": round(_max(acc["weed_pressure"]), 4),
        "max_disease_pressure": round(_max(acc["disease_pressure"]), 4),
        "max_insect_pressure": round(_max(acc["insect_pressure"]), 4),
        "avg_yield_potential_g_m2": round(_mean(acc["yield_potential_g_m2"]), 4),
        "max_yield_potential_g_m2": round(_max(acc["yield_potential_g_m2"]), 4),
        "avg_grain_moisture_frac": round(_mean(acc["grain_moisture_frac"]), 4),
    }


def _round_sample(sample: dict[str, Any]) -> dict[str, Any]:
    rounded: dict[str, Any] = {}
    for key, value in sample.items():
        if isinstance(value, float):
            rounded[key] = round(value, 4)
        else:
            rounded[key] = value
    return rounded


def _primary_zone_name(ridge_id: int) -> str:
    if AFFECTED_START <= ridge_id <= AFFECTED_END:
        return "affected_20_31"
    if REFERENCE_WEST_START <= ridge_id <= REFERENCE_WEST_END:
        return "reference_west_0_11"
    if REFERENCE_EAST_START <= ridge_id <= REFERENCE_EAST_END:
        return "reference_east_44_53"
    return "whole_field"


def _zones_for_ridge(ridge_id: int) -> list[str]:
    zones = ["whole_field"]
    if AFFECTED_START <= ridge_id <= AFFECTED_END:
        zones.append("affected_20_31")
    if REFERENCE_WEST_START <= ridge_id <= REFERENCE_WEST_END:
        zones.append("reference_west_0_11")
    if REFERENCE_EAST_START <= ridge_id <= REFERENCE_EAST_END:
        zones.append("reference_east_44_53")
    return zones


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
        "stage_counts_json": json.dumps(summary.get("stage_counts", {}), ensure_ascii=False, sort_keys=True),
        "zone_summaries_json": json.dumps(payload.get("zone_summaries", {}), ensure_ascii=False, sort_keys=True),
        **{key: summary.get(key) for key in FIELD_COLUMNS if key in summary},
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
                "stage": ridge.get("stage"),
                "days_after_planting": ridge.get("days_after_planting"),
                "top_vwc": ridge.get("top_vwc"),
                "root_vwc": ridge.get("root_vwc"),
                "water_stress": ridge.get("water_stress"),
                "top_temp_c": ridge.get("top_temp_c"),
                "canopy_temp_proxy_c": ridge.get("canopy_temp_proxy_c"),
                "recent_irrigation_mm": ridge.get("recent_irrigation_mm"),
                "cumulative_irrigation_mm": ridge.get("cumulative_irrigation_mm"),
                "nutrient_index": ridge.get("nutrient_index"),
                "nutrient_stress": ridge.get("nutrient_stress"),
                "stand_fraction": ridge.get("stand_fraction"),
                "weed_pressure": ridge.get("weed_pressure"),
                "insect_pressure": ridge.get("insect_pressure"),
                "disease_pressure": ridge.get("disease_pressure"),
                "lai": ridge.get("lai"),
                "canopy_cover": ridge.get("canopy_cover"),
                "ndvi_proxy": ridge.get("ndvi_proxy"),
                "aboveground_biomass_g_m2": ridge.get("aboveground_biomass_g_m2"),
                "yield_potential_g_m2": ridge.get("yield_potential_g_m2"),
                "grain_moisture_frac": ridge.get("grain_moisture_frac"),
                "biological_yield_g_m2": ridge.get("biological_yield_g_m2"),
                "recovered_yield_g_m2": ridge.get("recovered_yield_g_m2"),
                "soil_tags_json": json.dumps(ridge.get("soil_tags", []), ensure_ascii=False),
                "biotic_tags_json": json.dumps(ridge.get("biotic_tags", []), ensure_ascii=False),
                "management_tags_json": json.dumps(ridge.get("management_tags", []), ensure_ascii=False),
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
    zone_totals = {
        "affected_20_31": {"biological_kg": 0.0, "recovered_kg": 0.0},
        "reference_west_0_11": {"biological_kg": 0.0, "recovered_kg": 0.0},
        "reference_east_44_53": {"biological_kg": 0.0, "recovered_kg": 0.0},
        "whole_field": {"biological_kg": 0.0, "recovered_kg": 0.0},
    }
    bio_total = 0.0
    rec_total = 0.0
    planted = 0
    r8 = 0
    harvested = 0
    for rid, yld in physics.yield_recovery.states.items():
        phen = physics.phenology.states.get(rid)
        bio = float(yld.biological_yield_g_m2) * ridge_area_m2 / 1000.0
        rec = float(yld.recovered_yield_g_m2_at_market_moisture) * ridge_area_m2 / 1000.0
        bio_total += bio
        rec_total += rec
        zone_totals["whole_field"]["biological_kg"] += bio
        zone_totals["whole_field"]["recovered_kg"] += rec
        if phen and phen.planted:
            planted += 1
        if phen and str(getattr(phen.stage, "value", phen.stage)) == "R8_FULL_MATURITY":
            r8 += 1
        if yld.harvested:
            harvested += 1
        if AFFECTED_START <= rid <= AFFECTED_END:
            zone_totals["affected_20_31"]["biological_kg"] += bio
            zone_totals["affected_20_31"]["recovered_kg"] += rec
        if REFERENCE_WEST_START <= rid <= REFERENCE_WEST_END:
            zone_totals["reference_west_0_11"]["biological_kg"] += bio
            zone_totals["reference_west_0_11"]["recovered_kg"] += rec
        if REFERENCE_EAST_START <= rid <= REFERENCE_EAST_END:
            zone_totals["reference_east_44_53"]["biological_kg"] += bio
            zone_totals["reference_east_44_53"]["recovered_kg"] += rec

    rounded_zones = {
        zone: {key: round(value, 2) for key, value in totals.items()}
        for zone, totals in zone_totals.items()
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
        default=Path("docs/ai/fastdraining-dry-patch-field-summary.csv"),
    )
    parser.add_argument(
        "--ridge-csv",
        type=Path,
        default=Path("docs/ai/fastdraining-dry-patch-ridge-states.csv"),
    )
    parser.add_argument(
        "--trace-json",
        type=Path,
        default=Path("docs/ai/fastdraining-dry-patch-oracle-trace.json"),
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
