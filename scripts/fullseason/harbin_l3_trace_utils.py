"""Shared trace utilities for Harbin L3 full-season scenario diagnostics."""
from __future__ import annotations

import ast
import csv
import json
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from are.simulation.apps.app import App
from are.simulation.apps.farm_world import WeatherApp
from are.simulation.apps.farm_world.farm_world_app import (
    DEFAULT_RIDGE_WIDTH_M,
    FIELD_LENGTH_M,
    FarmWorldApp,
)
from are.simulation.environment import Environment, EnvironmentConfig
from are.simulation.notification_system import VerboseNotificationSystem
from are.simulation.tool_utils import OperationType, app_tool, data_tool
from are.simulation.types import EnvironmentType, event_registered
from are.simulation.utils.type_utils import type_check


FIELD_COLUMNS = [
    "event_id",
    "trace_index",
    "label",
    "date",
    "sim_datetime_utc",
    "weather_temp_c",
    "weather_humidity_pct",
    "weather_wind_speed_ms",
    "weather_rainfall_mm",
    "weather_solar_radiation",
    "physics_status",
    "day_ticks_run",
    "elapsed_s",
    "action_markers",
    "recent_actions_json",
    "stage_counts_json",
    "zone_summaries_json",
    "avg_days_after_planting",
    "avg_gdd",
    "avg_effective_development_gdd",
    "avg_top_vwc",
    "avg_root_vwc",
    "min_water_stress",
    "avg_nutrient_stress",
    "max_weed_pressure",
    "max_insect_pressure",
    "max_disease_pressure",
    "avg_lai",
    "avg_ndvi",
    "avg_canopy_temp_proxy_c",
    "avg_biological_yield_g_m2",
    "avg_recovered_yield_g_m2",
    "avg_grain_moisture",
]


RIDGE_COLUMNS = [
    "event_id",
    "trace_index",
    "label",
    "date",
    "sim_datetime_utc",
    "weather_wind_speed_ms",
    "weather_rainfall_mm",
    "ridge_id",
    "zone",
    "stage",
    "days_after_planting",
    "gdd",
    "effective_development_gdd",
    "top_vwc",
    "root_vwc",
    "water_stress",
    "nutrient_index",
    "nutrient_stress",
    "stand_fraction",
    "weed_pressure",
    "insect_pressure",
    "disease_pressure",
    "lai",
    "canopy_cover",
    "ndvi",
    "canopy_temp_proxy_c",
    "aboveground_biomass",
    "yield_potential",
    "grain_moisture",
    "biological_yield",
    "recovered_yield",
    "soil_tags_json",
    "biotic_tags_json",
    "management_tags_json",
    "action_marker",
]


ZoneSpec = tuple[str, int, int]


def make_trace_scenario(
    scenario_cls: type[Any],
    *,
    trace_app_name: str,
    zones: Sequence[ZoneSpec],
) -> type[Any]:
    class TraceScenario(scenario_cls):  # type: ignore[misc, valid-type]
        def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
            super().init_and_populate_apps(*args, **kwargs)
            farm_world = self.get_typed_app(FarmWorldApp)
            weather = self.get_typed_app(WeatherApp)
            self._trace_app = HarbinL3DailyTraceApp(
                farm_world_app=farm_world,
                weather_app=weather,
                name=trace_app_name,
                zones=zones,
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

    TraceScenario.__name__ = f"Trace{scenario_cls.__name__}"
    return TraceScenario


class HarbinL3DailyTraceApp(App):
    """Trace-only app inserted by scripts, not by production scenarios."""

    def __init__(
        self,
        farm_world_app: FarmWorldApp,
        weather_app: WeatherApp,
        *,
        name: str,
        zones: Sequence[ZoneSpec],
    ) -> None:
        super().__init__(name=name)
        self._farm_world_app = farm_world_app
        self._weather_app = weather_app
        self._zones = list(zones)
        self._last_action_index = 0

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
            "date": weather.get("date"),
            "sim_datetime_utc": now,
            "weather": weather,
            "advance_result": advance_result,
        }
        physics = getattr(self._farm_world_app, "_physics", None)
        if physics is None or not getattr(physics, "engines_active", False):
            payload["physics_active"] = False
            return payload

        recent_actions = list(physics.action_log[self._last_action_index :])
        self._last_action_index = len(physics.action_log)
        action_records = [_simplify(action) for action in recent_actions]
        action_markers = _action_markers_for_label(label)
        action_markers.extend(str(action.action_type) for action in recent_actions)
        action_markers = sorted(set(marker for marker in action_markers if marker))

        soil_params = getattr(physics.soil, "params", None)
        wilting = float(getattr(soil_params, "wilting_point_vwc", 0.14))
        stress_threshold = float(getattr(soil_params, "water_stress_vwc", 0.18))
        air_temp = float(weather.get("temp_c") or 0.0)

        field_acc = _new_accumulator()
        zone_acc = {name: _new_accumulator() for name, _, _ in self._zones}
        zone_acc["whole_field"] = _new_accumulator()
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
                "days_after_planting": int(phen.days_after_planting),
                "gdd": float(phen.accumulated_gdd),
                "effective_development_gdd": float(phen.effective_development_gdd),
                "top_vwc": float(soil.top_vwc),
                "root_vwc": root_vwc,
                "water_stress": water_stress,
                "nutrient_index": float(mgmt.nutrient_index),
                "nutrient_stress": float(mgmt.nutrient_stress),
                "stand_fraction": float(mgmt.stand_fraction),
                "weed_pressure": float(biotic.weed_pressure),
                "insect_pressure": float(biotic.insect_pressure),
                "disease_pressure": float(biotic.disease_pressure),
                "lai": float(canopy.lai),
                "canopy_cover": float(canopy.canopy_cover),
                "ndvi": float(canopy.ndvi_proxy),
                "canopy_temp_proxy_c": canopy_temp_proxy,
                "aboveground_biomass": float(canopy.aboveground_biomass_g_m2),
                "yield_potential": float(canopy.yield_potential_g_m2),
                "grain_moisture": _float_or_none(yld.grain_moisture_frac),
                "biological_yield": float(yld.biological_yield_g_m2),
                "recovered_yield": float(yld.recovered_yield_g_m2_at_market_moisture),
            }
            _add_sample(field_acc, sample)
            _add_sample(zone_acc["whole_field"], sample)
            zone = _primary_zone_name(rid, self._zones)
            if zone != "whole_field" and zone in zone_acc:
                _add_sample(zone_acc[zone], sample)

            if include_ridge_details:
                ridges.append(
                    {
                        "ridge_id": rid,
                        "zone": zone,
                        "soil_tags": list(soil.tags),
                        "biotic_tags": list(biotic.tags),
                        "management_tags": list(mgmt.tags),
                        "action_marker": _ridge_action_marker(rid, action_markers, recent_actions),
                        **_round_sample(sample),
                    }
                )

        payload["field_summary"] = _summarize(field_acc)
        payload["zone_summaries"] = {
            zone: _summarize(acc) for zone, acc in zone_acc.items()
        }
        payload["recent_actions"] = action_records
        payload["action_markers"] = action_markers
        if include_ridge_details:
            payload["ridges"] = ridges
        return payload


def run_trace(
    *,
    scenario_cls: type[Any],
    scenario_id: str,
    trace_app_name: str,
    zones: Sequence[ZoneSpec],
    field_csv: Path,
    ridge_csv: Path,
    trace_json: Path,
    diagnostics: Callable[[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]], list[str]]
    | None = None,
) -> dict[str, Any]:
    trace_scenario_cls = make_trace_scenario(
        scenario_cls,
        trace_app_name=trace_app_name,
        zones=zones,
    )
    scenario = trace_scenario_cls()
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

    traces = _trace_payloads(events, trace_app_name)
    if not traces:
        raise SystemExit(f"No {trace_app_name} payloads were captured")

    field_rows: list[dict[str, Any]] = []
    ridge_rows: list[dict[str, Any]] = []
    for trace_index, (event_id, payload) in enumerate(traces, start=1):
        field_rows.append(_field_row(event_id, trace_index, payload))
        ridge_rows.extend(_ridge_rows(event_id, trace_index, payload))

    _write_csv(field_csv, FIELD_COLUMNS, field_rows)
    _write_csv(ridge_csv, RIDGE_COLUMNS, ridge_rows)

    completed_events, failed_events, error_returns = _completed_events(events)
    warnings = generic_diagnostics(field_rows, ridge_rows, completed_events)
    if diagnostics is not None:
        warnings.extend(diagnostics(field_rows, ridge_rows, completed_events))

    trace_payload = {
        "scenario_id": scenario_id,
        "completed_events": completed_events,
        "action_justifications": _action_justifications(events),
        "diagnostic_warnings": warnings,
    }
    trace_json.parent.mkdir(parents=True, exist_ok=True)
    trace_json.write_text(
        json.dumps(trace_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {
        "scenario_id": scenario_id,
        "trace_events": len(traces),
        "field_csv": str(field_csv),
        "field_rows": len(field_rows),
        "ridge_csv": str(ridge_csv),
        "ridge_rows": len(ridge_rows),
        "trace_json": str(trace_json),
        "events_completed": len(events),
        "failed_events": failed_events[:10],
        "failed_event_count": len(failed_events),
        "error_returns": error_returns[:10],
        "error_return_count": len(error_returns),
        "diagnostic_warnings": warnings[:20],
        "diagnostic_warning_count": len(warnings),
        "duration_s": round(time.time() - started, 2),
        "yield": _yield_summary(scenario, zones),
    }
    try:
        env.stop()
    except Exception:
        pass
    return summary


def generic_diagnostics(
    field_rows: list[dict[str, Any]],
    ridge_rows: list[dict[str, Any]],
    completed_events: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    stage_order = {
        "NOT_PLANTED": 0,
        "PLANTED_PRE_EMERGENCE": 1,
        "VE": 2,
        "VC": 3,
        "V1": 4,
        "V2": 5,
        "V3": 6,
        "V4_PLUS": 7,
        "R1_BEGINNING_BLOOM": 8,
        "R3_BEGINNING_POD": 9,
        "R5_BEGINNING_SEED": 10,
        "R6_FULL_SEED": 11,
        "R7_BEGINNING_MATURITY": 12,
        "R8_FULL_MATURITY": 13,
    }
    by_ridge: dict[int, list[dict[str, Any]]] = {}
    for row in ridge_rows:
        by_ridge.setdefault(int(row["ridge_id"]), []).append(row)
    for rid, rows in by_ridge.items():
        rows.sort(key=lambda r: int(r["trace_index"]))
        last_order = -1
        first_r8_biomass: float | None = None
        first_harvest_recovered: float | None = None
        last_root_vwc: float | None = None
        for row in rows:
            stage = str(row.get("stage"))
            order = stage_order.get(stage, last_order)
            if order < last_order:
                warnings.append(f"ridge {rid} stage regressed at trace {row['trace_index']}: {stage}")
                break
            last_order = order
            biomass = float(row.get("aboveground_biomass") or 0.0)
            if stage == "R8_FULL_MATURITY":
                if first_r8_biomass is None:
                    first_r8_biomass = biomass
                elif biomass - first_r8_biomass > 5.0:
                    warnings.append(
                        f"ridge {rid} biomass increased >5 g/m2 after first R8"
                    )
                    break
            recovered = float(row.get("recovered_yield") or 0.0)
            marker = str(row.get("action_marker") or "")
            if "harvest" in marker and first_harvest_recovered is None:
                first_harvest_recovered = recovered
            elif first_harvest_recovered is not None and recovered - first_harvest_recovered > 1.0:
                warnings.append(
                    f"ridge {rid} recovered yield increased after harvest marker"
                )
                break
            root_vwc = float(row.get("root_vwc") or 0.0)
            if last_root_vwc is not None and abs(root_vwc - last_root_vwc) > 0.12:
                warnings.append(
                    f"ridge {rid} root_vwc jump >0.12 at trace {row['trace_index']}"
                )
                break
            last_root_vwc = root_vwc
    for event in completed_events:
        value = event.get("return_value")
        if isinstance(value, dict) and "error" in value:
            warnings.append(
                f"tool error return {event.get('event_id')}: {event.get('function')} -> {value.get('error')}"
            )
    return warnings


def _new_accumulator() -> dict[str, Any]:
    return {
        "count": 0,
        "stage_counts": {},
        "days_after_planting": [],
        "gdd": [],
        "effective_development_gdd": [],
        "top_vwc": [],
        "root_vwc": [],
        "water_stress": [],
        "nutrient_stress": [],
        "weed_pressure": [],
        "insect_pressure": [],
        "disease_pressure": [],
        "lai": [],
        "ndvi": [],
        "canopy_temp_proxy_c": [],
        "biological_yield": [],
        "recovered_yield": [],
        "grain_moisture": [],
    }


def _add_sample(acc: dict[str, Any], sample: dict[str, Any]) -> None:
    acc["count"] += 1
    stage = str(sample["stage"])
    acc["stage_counts"][stage] = acc["stage_counts"].get(stage, 0) + 1
    for key in [
        "days_after_planting",
        "gdd",
        "effective_development_gdd",
        "top_vwc",
        "root_vwc",
        "water_stress",
        "nutrient_stress",
        "weed_pressure",
        "insect_pressure",
        "disease_pressure",
        "lai",
        "ndvi",
        "canopy_temp_proxy_c",
        "biological_yield",
        "recovered_yield",
    ]:
        acc[key].append(float(sample[key]))
    if sample.get("grain_moisture") is not None:
        acc["grain_moisture"].append(float(sample["grain_moisture"]))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _summarize(acc: dict[str, Any]) -> dict[str, Any]:
    return {
        "ridge_count": acc["count"],
        "stage_counts": dict(acc["stage_counts"]),
        "avg_days_after_planting": round(_mean(acc["days_after_planting"]), 2),
        "avg_gdd": round(_mean(acc["gdd"]), 2),
        "avg_effective_development_gdd": round(_mean(acc["effective_development_gdd"]), 2),
        "avg_top_vwc": round(_mean(acc["top_vwc"]), 4),
        "avg_root_vwc": round(_mean(acc["root_vwc"]), 4),
        "min_water_stress": round(min(acc["water_stress"]), 4) if acc["water_stress"] else 0.0,
        "avg_nutrient_stress": round(_mean(acc["nutrient_stress"]), 4),
        "max_weed_pressure": round(max(acc["weed_pressure"]), 4) if acc["weed_pressure"] else 0.0,
        "max_insect_pressure": round(max(acc["insect_pressure"]), 4) if acc["insect_pressure"] else 0.0,
        "max_disease_pressure": round(max(acc["disease_pressure"]), 4) if acc["disease_pressure"] else 0.0,
        "avg_lai": round(_mean(acc["lai"]), 4),
        "avg_ndvi": round(_mean(acc["ndvi"]), 4),
        "avg_canopy_temp_proxy_c": round(_mean(acc["canopy_temp_proxy_c"]), 4),
        "avg_biological_yield_g_m2": round(_mean(acc["biological_yield"]), 4),
        "avg_recovered_yield_g_m2": round(_mean(acc["recovered_yield"]), 4),
        "avg_grain_moisture": round(_mean(acc["grain_moisture"]), 4),
    }


def _round_sample(sample: dict[str, Any]) -> dict[str, Any]:
    rounded: dict[str, Any] = {}
    for key, value in sample.items():
        if isinstance(value, float):
            rounded[key] = round(value, 4)
        else:
            rounded[key] = value
    return rounded


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)


def _primary_zone_name(ridge_id: int, zones: Sequence[ZoneSpec]) -> str:
    for name, start, end in zones:
        if start <= ridge_id <= end:
            return name
    return "other_ridges"


def _zones_for_ridge(ridge_id: int, zones: Sequence[ZoneSpec]) -> list[str]:
    return ["whole_field", _primary_zone_name(ridge_id, zones)]


def _action_markers_for_label(label: str) -> list[str]:
    markers: list[str] = []
    label = label.lower()
    if label.startswith("o_wait") or "_wait_" in label:
        return markers
    if "plant" in label:
        markers.append("planting")
    if "insecticide" in label or "pesticide" in label:
        markers.append("pesticide")
    if "fungicide" in label:
        markers.append("fungicide")
    if "harvest" in label:
        markers.append("harvest")
    if "dry" in label or "harvest_store" in label:
        markers.append("dry")
    if "store" in label:
        markers.append("store")
    return markers


def _ridge_action_marker(
    ridge_id: int,
    action_markers: list[str],
    recent_actions: Sequence[Any],
) -> str:
    ridge_markers: set[str] = set()
    for action in recent_actions:
        ridge_ids = list(getattr(action, "ridge_ids", []) or [])
        action_type = str(getattr(action, "action_type", ""))
        if ridge_ids and ridge_id in ridge_ids:
            ridge_markers.add(action_type)
    return "|".join(sorted(marker for marker in ridge_markers if marker))


def _simplify(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "..."
    if is_dataclass(value):
        return _simplify(asdict(value), depth=depth + 1)
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


def _is_daily_trace_event(event: Any, trace_app_name: str) -> bool:
    app = event.app_name() or event.app_class_name() or ""
    fn = event.function_name() or ""
    return app == trace_app_name and fn == "capture_daily_state"


def _trace_payloads(events: list[Any], trace_app_name: str) -> list[tuple[str, dict[str, Any]]]:
    traces: list[tuple[str, dict[str, Any]]] = []
    for event in events:
        if event.failed() or not _is_daily_trace_event(event, trace_app_name):
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
        "date": payload.get("date") or weather.get("date"),
        "sim_datetime_utc": payload.get("sim_datetime_utc"),
        "weather_temp_c": weather.get("temp_c"),
        "weather_humidity_pct": weather.get("humidity_pct"),
        "weather_wind_speed_ms": weather.get("wind_speed_ms"),
        "weather_rainfall_mm": weather.get("rainfall_mm"),
        "weather_solar_radiation": weather.get("solar_radiation"),
        "physics_status": advance.get("status"),
        "day_ticks_run": advance.get("day_ticks_run"),
        "elapsed_s": advance.get("elapsed_s"),
        "action_markers": "|".join(payload.get("action_markers", [])),
        "recent_actions_json": json.dumps(payload.get("recent_actions", []), ensure_ascii=False, sort_keys=True),
        "stage_counts_json": json.dumps(summary.get("stage_counts", {}), ensure_ascii=False, sort_keys=True),
        "zone_summaries_json": json.dumps(payload.get("zone_summaries", {}), ensure_ascii=False, sort_keys=True),
        **{key: summary.get(key) for key in FIELD_COLUMNS if key in summary},
    }


def _ridge_rows(event_id: str, trace_index: int, payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    weather = payload.get("weather") or {}
    for ridge in payload.get("ridges", []) or []:
        rows.append(
            {
                "event_id": event_id,
                "trace_index": trace_index,
                "label": payload.get("label"),
                "date": payload.get("date"),
                "sim_datetime_utc": payload.get("sim_datetime_utc"),
                "weather_wind_speed_ms": weather.get("wind_speed_ms"),
                "weather_rainfall_mm": weather.get("rainfall_mm"),
                "ridge_id": ridge.get("ridge_id"),
                "zone": ridge.get("zone"),
                "stage": ridge.get("stage"),
                "days_after_planting": ridge.get("days_after_planting"),
                "gdd": ridge.get("gdd"),
                "effective_development_gdd": ridge.get("effective_development_gdd"),
                "top_vwc": ridge.get("top_vwc"),
                "root_vwc": ridge.get("root_vwc"),
                "water_stress": ridge.get("water_stress"),
                "nutrient_index": ridge.get("nutrient_index"),
                "nutrient_stress": ridge.get("nutrient_stress"),
                "stand_fraction": ridge.get("stand_fraction"),
                "weed_pressure": ridge.get("weed_pressure"),
                "insect_pressure": ridge.get("insect_pressure"),
                "disease_pressure": ridge.get("disease_pressure"),
                "lai": ridge.get("lai"),
                "canopy_cover": ridge.get("canopy_cover"),
                "ndvi": ridge.get("ndvi"),
                "canopy_temp_proxy_c": ridge.get("canopy_temp_proxy_c"),
                "aboveground_biomass": ridge.get("aboveground_biomass"),
                "yield_potential": ridge.get("yield_potential"),
                "grain_moisture": ridge.get("grain_moisture"),
                "biological_yield": ridge.get("biological_yield"),
                "recovered_yield": ridge.get("recovered_yield"),
                "soil_tags_json": json.dumps(ridge.get("soil_tags", []), ensure_ascii=False),
                "biotic_tags_json": json.dumps(ridge.get("biotic_tags", []), ensure_ascii=False),
                "management_tags_json": json.dumps(ridge.get("management_tags", []), ensure_ascii=False),
                "action_marker": ridge.get("action_marker"),
            }
        )
    return rows


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _completed_events(events: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    completed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    error_returns: list[dict[str, Any]] = []
    for event in events:
        value = _simplify(getattr(event.metadata, "return_value", None))
        record = {
            "event_id": str(event.event_id),
            "app": event.app_name() or event.app_class_name(),
            "function": event.function_name(),
            "failed": event.failed(),
            "return_value": value,
            "exception": str(getattr(event.metadata, "exception", "")) if event.failed() else None,
        }
        completed.append(record)
        if event.failed():
            failed.append(record)
        if isinstance(value, dict) and "error" in value:
            error_returns.append(
                {
                    "event_id": str(event.event_id),
                    "app": event.app_name() or event.app_class_name(),
                    "function": event.function_name(),
                    "error": value["error"],
                }
            )
    return completed, failed, error_returns


def _action_justifications(events: list[Any]) -> list[dict[str, Any]]:
    check_functions = {
        "get_current_weather",
        "get_forecast",
        "read_soil_sensors",
        "read_canopy_sensors",
        "get_farm_overview",
        "get_ridge_range_state",
        "get_inventory",
        "get_status",
        "fly_survey",
        "inspect_crop_health",
        "inspect_emergence",
    }
    action_functions = {
        "plant_seeds",
        "spray_pesticide",
        "apply_pesticide",
        "apply_fungicide",
        "harvest",
        "dry_grain",
        "store_grain",
        "apply_fertigation",
        "irrigate",
    }
    prior_checks: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    for event in events:
        fn = event.function_name() or ""
        simplified_return = _simplify(getattr(event.metadata, "return_value", None))
        event_record = {
            "event_id": str(event.event_id),
            "function": fn,
            "app": event.app_name() or event.app_class_name(),
            "return_value": simplified_return,
        }
        if fn in check_functions:
            prior_checks.append(event_record)
            prior_checks = prior_checks[-14:]
        if fn in action_functions:
            out.append(
                {
                    "action_event_id": str(event.event_id),
                    "action_function": fn,
                    "action_return_value": simplified_return,
                    "prior_check_event_ids": [check["event_id"] for check in prior_checks[-8:]],
                    "prior_check_returns": prior_checks[-8:],
                    "reason": _reason_for_action(str(event.event_id), fn),
                }
            )
    return out


def _reason_for_action(event_id: str, fn: str) -> str:
    if fn == "plant_seeds":
        return "planting follows weather, forecast, soil, inventory, and tractor checks"
    if fn in {"spray_pesticide", "apply_pesticide"}:
        return "pesticide follows scouting/check returns and should be targeted to thresholded ridges"
    if fn == "apply_fungicide":
        return "fungicide follows disease-threshold scouting/check returns and should be targeted"
    if fn == "harvest":
        return "harvest follows weather, soil trafficability, overview, and range readiness checks"
    if fn in {"dry_grain", "store_grain"}:
        return "post-harvest grain handling should immediately follow the harvested batch"
    return f"{fn} follows the preceding scouting/check returns"


def _yield_summary(scenario: Any, zones: Sequence[ZoneSpec]) -> dict[str, Any]:
    farm_world = scenario.get_typed_app(FarmWorldApp)
    physics = getattr(farm_world, "_physics", None)
    if physics is None or not getattr(physics, "engines_active", False):
        return {"physics_active": False}

    ridge_area_m2 = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M
    zone_totals = {
        name: {"biological_kg": 0.0, "recovered_kg": 0.0}
        for name, _, _ in zones
    }
    zone_totals["other_ridges"] = {"biological_kg": 0.0, "recovered_kg": 0.0}
    zone_totals["whole_field"] = {"biological_kg": 0.0, "recovered_kg": 0.0}
    planted = 0
    r8 = 0
    harvested = 0
    for rid, yld in physics.yield_recovery.states.items():
        phen = physics.phenology.states.get(rid)
        bio = float(yld.biological_yield_g_m2) * ridge_area_m2 / 1000.0
        rec = float(yld.recovered_yield_g_m2_at_market_moisture) * ridge_area_m2 / 1000.0
        for zone in _zones_for_ridge(rid, zones):
            zone_totals[zone]["biological_kg"] += bio
            zone_totals[zone]["recovered_kg"] += rec
        if phen and phen.planted:
            planted += 1
        if phen and str(getattr(phen.stage, "value", phen.stage)) == "R8_FULL_MATURITY":
            r8 += 1
        if yld.harvested:
            harvested += 1

    rec_total = zone_totals["whole_field"]["recovered_kg"]
    return {
        "physics_active": True,
        "biological_yield_kg_total": round(zone_totals["whole_field"]["biological_kg"], 2),
        "recovered_yield_kg_total": round(rec_total, 2),
        "recovered_yield_kg_ha": round(rec_total / (64 * ridge_area_m2) * 10000.0, 2),
        "recovered_yield_kg_mu": round(rec_total / (64 * ridge_area_m2) * 666.6667, 2),
        "zone_totals": {
            zone: {key: round(value, 2) for key, value in totals.items()}
            for zone, totals in zone_totals.items()
        },
        "ridges_planted": planted,
        "ridges_r8": r8,
        "ridges_harvested": harvested,
    }
