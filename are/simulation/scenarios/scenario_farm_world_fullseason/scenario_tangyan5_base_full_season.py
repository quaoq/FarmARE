from __future__ import annotations

import argparse
import csv
import json
import math
import os
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    FarmWorldApp,
    GrowthStage,
    SeasonPhase,
    SeedType,
    SensorApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.apps.farm_world.farm_world_app import (
    DEFAULT_RIDGE_WIDTH_M,
    FIELD_LENGTH_M,
)
from are.simulation.apps.system import SystemApp
from are.simulation.physics import WeatherGenerator
from are.simulation.physics.weather_engine import default_harbin_soybean_config
from are.simulation.physics.weather_engine import WeatherDay
from are.simulation.scenarios.scenario_farm_world_fullseason.tangyan5_base_data import (
    TANGYAN5_BASE_PLOTS,
    TANGYAN5_BASE_WEATHER,
)
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.time_manager import TimeManager
from are.simulation.types import EventRegisterer


DEFAULT_WORKBOOK_PATH = ""
DEFAULT_TANGYAN5_DATA_SOURCE = "embedded:tangyan5_base_data.py"
DEFAULT_PLOT_ID = "Nor_HH43"
FIELD_MODE = "whole_field_single_treatment"
WEATHER_SOURCE_ACTUAL = "actual"
WEATHER_SOURCE_ENGINE = "engine"
WEATHER_SOURCES = (WEATHER_SOURCE_ACTUAL, WEATHER_SOURCE_ENGINE)
CALIBRATION_PROFILE_NONE = "none"
CALIBRATION_PROFILE_HEILONGJIANG_BLACK_SOIL_V1 = "heilongjiang_black_soil_v1"
CALIBRATION_PROFILE_HEILONGJIANG_BLACK_SOIL_V2 = "heilongjiang_black_soil_v2"
CALIBRATION_PROFILES = (
    CALIBRATION_PROFILE_NONE,
    CALIBRATION_PROFILE_HEILONGJIANG_BLACK_SOIL_V1,
    CALIBRATION_PROFILE_HEILONGJIANG_BLACK_SOIL_V2,
)
MANAGEMENT_PATH_EXPERT_BASELINE = "expert_baseline"
MANAGEMENT_PATH_STRESS_FREE_ORACLE = "stress_free_oracle"
MANAGEMENT_PATHS = (
    MANAGEMENT_PATH_EXPERT_BASELINE,
    MANAGEMENT_PATH_STRESS_FREE_ORACLE,
)
MANAGEMENT_PATH_BOTH = "both"
TANGYAN5_SOLAR_MJ_M2_MAX = 30.0
M2_PER_MU = 666.6666666667


def tangyan5_jsonify(obj: Any) -> Any:
    """Make phenology/canopy/soil dataclass trees JSON-safe (enums, dates)."""
    if obj is None:
        return None
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, (int, float, str, bool)):
        return obj
    if is_dataclass(obj) and not isinstance(obj, type):
        return tangyan5_jsonify(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): tangyan5_jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [tangyan5_jsonify(v) for v in obj]
    return str(obj)


def collect_event_graph(root: Any) -> list[Any]:
    """Collect an oracle chain from a root event through successor links."""
    ordered: list[Any] = []
    seen: set[int] = set()
    stack = [root]
    while stack:
        event = stack.pop(0)
        event_key = id(event)
        if event_key in seen:
            continue
        seen.add(event_key)
        ordered.append(event)
        stack[0:0] = list(getattr(event, "successors", []))
    return ordered


def tangyan5_physics_ridge_snapshot(
    farm_world: FarmWorldApp, ridge_id: int = 0
) -> dict[str, Any]:
    """Per-ridge engine state for diagnostics.

    ``FarmPhysicsState.snapshot()`` only returns engine metadata and pending
    queues — it does **not** include canopy/soil/yield_recovery. Use this helper
    (or the ``physics_ridge_0_pre_harvest`` field on oracle JSON) instead.
    """
    if not farm_world.physics_active:
        return {"error": "physics_inactive"}
    physics = farm_world.physics
    rid = int(ridge_id)
    mgmt = physics.management.states.get(rid)
    return {
        "ridge_id": rid,
        "phenology": tangyan5_jsonify(physics.phenology.states[rid]),
        "canopy": tangyan5_jsonify(physics.canopy.states[rid]),
        "soil": tangyan5_jsonify(physics.soil.states[rid]),
        "yield_recovery": tangyan5_jsonify(physics.yield_recovery.states[rid]),
        "biotic": tangyan5_jsonify(physics.biotic.states[rid]),
        "management": tangyan5_jsonify(mgmt) if mgmt is not None else None,
    }


def tangyan5_ridge_daily_trace_row(
    farm_world: FarmWorldApp,
    weather_day: Tangyan5WeatherDay | None,
    *,
    ridge_id: int,
    event: str,
    weather_source: str,
    field_date: date,
    target_yield_kg_ha: float,
    previous: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Flatten one ridge's engine state after a daily tick for CSV diagnostics."""
    physics = farm_world.physics
    phen = physics.phenology.states[ridge_id]
    canopy = physics.canopy.states[ridge_id]
    soil = physics.soil.states[ridge_id]
    mgmt = physics.management.states[ridge_id]
    yld = physics.yield_recovery.states[ridge_id]
    biotic = physics.biotic.states[ridge_id]
    ridge = farm_world.get_ridge(ridge_id)

    prev = previous or {}
    biomass_delta = canopy.aboveground_biomass_g_m2 - prev.get(
        "aboveground_biomass_g_m2", canopy.aboveground_biomass_g_m2
    )
    yield_delta = canopy.yield_potential_g_m2 - prev.get(
        "yield_potential_g_m2", canopy.yield_potential_g_m2
    )
    apar_delta = canopy.cumulative_apar_mj_m2 - prev.get(
        "cumulative_apar_mj_m2", canopy.cumulative_apar_mj_m2
    )
    recovered_kg_ha = yld.recovered_yield_g_m2_at_market_moisture * 10.0
    biological_kg_ha = yld.biological_yield_g_m2 * 10.0
    potential_kg_ha = canopy.yield_potential_g_m2 * 10.0
    tags = {
        "phenology": phen.tags,
        "canopy": canopy.tags,
        "soil": soil.tags,
        "management": mgmt.tags,
        "yield": yld.tags,
    }
    return {
        "weather_source": weather_source,
        "event": event,
        "field_date": field_date.isoformat(),
        "sim_utc_date": datetime.fromtimestamp(
            farm_world.time_manager.time(), tz=timezone.utc
        ).date().isoformat(),
        "field_local_date": (
            datetime.fromtimestamp(farm_world.time_manager.time(), tz=timezone.utc)
            + timedelta(hours=8)
        ).date().isoformat(),
        "ridge_id": ridge_id,
        "weather_temp_mean_c": None if weather_day is None else round(weather_day.temp_mean_c, 3),
        "weather_temp_min_c": None if weather_day is None else round(weather_day.temp_min_c, 3),
        "weather_temp_max_c": None if weather_day is None else round(weather_day.temp_max_c, 3),
        "weather_rain_mm": None if weather_day is None else round(weather_day.rain_mm, 3),
        "weather_solar_mj_m2": None if weather_day is None else round(weather_day.solar_mj_m2, 3),
        "weather_wind_ms": None if weather_day is None else round(weather_day.wind_ms, 3),
        "ridge_growth_stage": ridge.growth_stage,
        "ridge_days_since_planted": ridge.days_since_planted,
        "ridge_soil_vwc": round(ridge.soil_vwc, 4),
        "ridge_grain_moisture_pct": round(ridge.grain_moisture_pct, 3),
        "phenology_stage": phen.stage.value,
        "phenology_planted": phen.planted,
        "phenology_emerged": phen.emerged,
        "phenology_days_after_planting": phen.days_after_planting,
        "phenology_accumulated_gdd": round(phen.accumulated_gdd, 3),
        "phenology_effective_development_gdd": round(phen.effective_development_gdd, 3),
        "phenology_last_daily_gdd": round(phen.last_daily_gdd, 3),
        "phenology_last_effective_gdd": round(phen.last_effective_gdd, 3),
        "phenology_last_stress_multiplier": round(phen.last_stress_multiplier, 4),
        "phenology_emergence_date": None if phen.emergence_date is None else phen.emergence_date.isoformat(),
        "phenology_maturity_date": None if phen.maturity_date is None else phen.maturity_date.isoformat(),
        "soil_top_vwc": round(soil.top_vwc, 4),
        "soil_root_vwc": round(soil.root_vwc, 4),
        "soil_top_temp_c": round(soil.top_temp_c, 3),
        "soil_root_temp_c": round(soil.root_temp_c, 3),
        "soil_cumulative_evap_mm": round(soil.cumulative_evap_mm, 3),
        "soil_cumulative_transpiration_mm": round(soil.cumulative_transpiration_mm, 3),
        "soil_cumulative_drainage_mm": round(soil.cumulative_drainage_mm, 3),
        "soil_cumulative_runoff_mm": round(soil.cumulative_runoff_mm, 3),
        "management_stand_fraction": round(mgmt.stand_fraction, 4),
        "management_nutrient_index": round(mgmt.nutrient_index, 4),
        "management_nutrient_stress": round(mgmt.nutrient_stress, 4),
        "management_cumulative_irrigation_mm": round(mgmt.cumulative_irrigation_mm, 3),
        "management_cumulative_fertigation_amount": round(mgmt.cumulative_fertigation_amount, 3),
        "management_cumulative_base_fertilizer_amount": round(mgmt.cumulative_base_fertilizer_amount, 3),
        "biotic_weed_pressure": round(biotic.weed_pressure, 4),
        "biotic_insect_pressure": round(biotic.insect_pressure, 4),
        "biotic_disease_pressure": round(biotic.disease_pressure, 4),
        "canopy_initialized": canopy.initialized,
        "canopy_lai": round(canopy.lai, 4),
        "canopy_cover": round(canopy.canopy_cover, 4),
        "canopy_ndvi_proxy": round(canopy.ndvi_proxy, 4),
        "canopy_cumulative_apar_mj_m2": round(canopy.cumulative_apar_mj_m2, 3),
        "canopy_daily_apar_delta_mj_m2": round(apar_delta, 3),
        "canopy_aboveground_biomass_g_m2": round(canopy.aboveground_biomass_g_m2, 3),
        "canopy_daily_biomass_delta_g_m2": round(biomass_delta, 3),
        "canopy_yield_potential_g_m2": round(canopy.yield_potential_g_m2, 3),
        "canopy_daily_yield_delta_g_m2": round(yield_delta, 3),
        "canopy_potential_kg_ha": round(potential_kg_ha, 2),
        "canopy_cumulative_stress_days": round(canopy.cumulative_stress_days, 3),
        "yield_r8_reached": yld.r8_reached,
        "yield_maturity_date": None if yld.maturity_date is None else yld.maturity_date.isoformat(),
        "yield_grain_moisture_frac": None if yld.grain_moisture_frac is None else round(yld.grain_moisture_frac, 4),
        "yield_wet_dry_cycles_after_r8": yld.wet_dry_cycles_after_r8,
        "yield_biological_yield_g_m2": round(yld.biological_yield_g_m2, 3),
        "yield_biological_kg_ha": round(biological_kg_ha, 2),
        "yield_field_loss_fraction": round(yld.field_loss_fraction, 4),
        "yield_machine_loss_fraction": round(yld.machine_loss_fraction, 4),
        "yield_recovered_g_m2": round(yld.recovered_yield_g_m2_at_market_moisture, 3),
        "yield_recovered_kg_ha": round(recovered_kg_ha, 2),
        "gap_to_actual_recovered_kg_ha": round(recovered_kg_ha - target_yield_kg_ha, 2),
        "tags_json": json.dumps(tangyan5_jsonify(tags), ensure_ascii=False),
    }


XLSX_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


@dataclass(frozen=True)
class Tangyan5WeatherDay:
    day: date
    temp_mean_c: float
    temp_min_c: float
    temp_max_c: float
    wind_ms: float
    rain_mm: float
    humidity_pct: float
    solar_w_m2: float
    solar_mj_m2: float


@dataclass(frozen=True)
class Tangyan5Plot:
    plot_id: str
    variety: str
    seed_type: str
    planting_date: date
    emergence_date: date
    maturity_date: date
    harvest_date: date
    plot_area_m2: float
    plot_yield_kg: float
    seed_density_plants_ha: float
    seed_depth_cm: float
    seed_spacing_cm: float
    base_fertilizer_name: str
    base_fertilizer_date: date
    base_n_kg_ha: float
    base_p_kg_ha: float
    base_k_kg_ha: float
    topdress_name: str
    topdress_date: date
    topdress_n_kg_ha: float
    topdress_p_kg_ha: float
    topdress_k_kg_ha: float
    nutrient_index: float

    @property
    def actual_yield_kg_ha(self) -> float:
        return self.plot_yield_kg / self.plot_area_m2 * 10000.0

    @property
    def simulated_field_area_m2(self) -> float:
        return FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M * 64

    @property
    def actual_scaled_field_yield_kg(self) -> float:
        return self.actual_yield_kg_ha * self.simulated_field_area_m2 / 10000.0


@dataclass(frozen=True)
class Tangyan5SoilHydrology:
    """Soil hydraulic props when Tangyan5 applies a Heilongjiang fallback profile.

    If the workbook has particle-size columns we leave ``SoilEngine`` at its
    default parameters and ``ridge.soil_vwc=0.24`` (historical oracle path). If
    those columns are missing we load this reference (Songnen / NE Heilongjiang
    Mollisol-style) into ``SoilParameters`` and use its planting VWC.
    """

    clay_pct: float
    silt_pct: float
    sand_pct: float
    source_rows: str
    wilting_point_vwc: float
    field_capacity_vwc: float
    saturation_vwc: float
    initial_planting_soil_vwc: float


@dataclass(frozen=True)
class Tangyan5Trial:
    plot: Tangyan5Plot
    weather_by_date: dict[date, Tangyan5WeatherDay]
    soil_hydrology: Tangyan5SoilHydrology | None = None


class ActualWeatherGenerator:
    """Small WeatherGenerator-compatible adapter backed by embedded day rows."""

    def __init__(self, weather_by_date: dict[date, Tangyan5WeatherDay]) -> None:
        self.weather_by_date = weather_by_date

    def generate(
        self,
        start_date: date,
        end_date: date,
        events: list[Any] | None = None,
    ) -> list[WeatherDay]:
        if end_date < start_date:
            return []
        days: list[WeatherDay] = []
        current = start_date
        last: Tangyan5WeatherDay | None = None
        while current <= end_date:
            source = self.weather_by_date.get(current) or last
            if source is None:
                current += timedelta(days=1)
                continue
            days.append(
                WeatherDay(
                    day=current,
                    air_temp_mean_c=source.temp_mean_c,
                    air_temp_min_c=source.temp_min_c,
                    air_temp_max_c=source.temp_max_c,
                    rain_mm=source.rain_mm,
                    wind_ms=source.wind_ms,
                    solar_rad_mj_m2=source.solar_mj_m2,
                    is_raining=source.rain_mm > 0.0,
                    weather_tags=["actual_tangyan5_table"],
                )
            )
            last = source
            current += timedelta(days=1)
        return days


def _col_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    index = 0
    for ch in letters:
        index = index * 26 + ord(ch.upper()) - 64
    return index - 1


def _excel_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()


def _iso_date(value: str) -> date:
    return date.fromisoformat(value)


def _float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, str):
        value = value.strip()
    if value in ("", "/"):
        return default
    return float(value)


def _normalize_xlsx_header(sheet_name: str, header: list[str]) -> list[str]:
    if sheet_name == "土壤数据" and len(header) >= 4:
        # The Tangyan5 soil sheet stores the first row's plot/depth values in
        # the header row for columns C/D. Treat those columns as schema fields.
        header = list(header)
        header[2] = "小区"
        header[3] = "土层深度cm"
    return header


def _load_xlsx_tables(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    with ZipFile(path) as zf:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", XLSX_NS):
                shared_strings.append(
                    "".join(t.text or "" for t in item.findall(".//m:t", XLSX_NS))
                )

        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in rels.findall("rel:Relationship", XLSX_NS)
        }
        rid_key = f"{{{XLSX_NS['r']}}}id"
        sheet_targets = {
            sheet.attrib["name"]: "xl/" + rel_map[sheet.attrib[rid_key]].lstrip("/")
            for sheet in workbook.findall("m:sheets/m:sheet", XLSX_NS)
        }

        tables: dict[str, list[dict[str, Any]]] = {}
        for sheet_name, target in sheet_targets.items():
            rows = _read_sheet_rows(zf, target, shared_strings)
            if not rows:
                tables[sheet_name] = []
                continue
            header = [str(v).strip() if v is not None else "" for v in rows[0]]
            header = _normalize_xlsx_header(sheet_name, header)
            # The management sheet has a units row; data starts on row 3.
            data_rows = rows[2:] if sheet_name == "管理数据" else rows[1:]
            records: list[dict[str, Any]] = []
            for row in data_rows:
                if not any(v not in (None, "") for v in row):
                    continue
                record = {
                    header[i]: row[i] if i < len(row) else None
                    for i in range(len(header))
                    if header[i]
                }
                records.append(record)
            tables[sheet_name] = records
        return tables


def _read_sheet_rows(
    zf: ZipFile, target: str, shared_strings: list[str]
) -> list[list[Any]]:
    root = ET.fromstring(zf.read(target))
    rows: list[list[Any]] = []
    for row in root.findall("m:sheetData/m:row", XLSX_NS):
        values: list[Any] = []
        last_index = -1
        for cell in row.findall("m:c", XLSX_NS):
            index = _col_index(cell.attrib.get("r", "A"))
            while last_index + 1 < index:
                values.append(None)
                last_index += 1
            value_node = cell.find("m:v", XLSX_NS)
            inline_node = cell.find("m:is", XLSX_NS)
            value: Any = None
            if cell.attrib.get("t") == "s" and value_node is not None:
                value = shared_strings[int(value_node.text or 0)]
            elif cell.attrib.get("t") == "inlineStr" and inline_node is not None:
                value = "".join(
                    t.text or "" for t in inline_node.findall(".//m:t", XLSX_NS)
                )
            elif value_node is not None:
                value = value_node.text
            values.append(value)
            last_index = index
        rows.append(values)
    return rows


def load_tangyan5_trial(path: str | Path, plot_id: str = DEFAULT_PLOT_ID) -> Tangyan5Trial:
    tables = _load_xlsx_tables(path)
    management = _one_by(tables["管理数据"], "小区", plot_id)
    crop = _one_by(tables["作物数据"], "小区", plot_id)
    soil_rows = [row for row in tables["土壤数据"] if row.get("小区") == plot_id]

    planting_date = _excel_date(management["播种日期"])
    seed_density_plants_ha = _float(management["播种密度"]) * 10000.0
    seed_spacing_cm = _spacing_cm_from_density(seed_density_plants_ha)
    nutrient_index = _nutrient_index_from_soil(soil_rows)

    plot = Tangyan5Plot(
        plot_id=plot_id,
        variety=str(management["播种品种"]),
        seed_type=_seed_type_for_variety(str(management["播种品种"])),
        planting_date=planting_date,
        emergence_date=_excel_date(crop["出苗-日期"]),
        maturity_date=_excel_date(crop["成熟-日期"]),
        harvest_date=_excel_date(crop["收获-日期"]),
        plot_area_m2=_float(management["小区面积"]),
        plot_yield_kg=_float(crop["小区产量（kg）"]),
        seed_density_plants_ha=seed_density_plants_ha,
        seed_depth_cm=_float(management["播种深度"], 4.0),
        seed_spacing_cm=seed_spacing_cm,
        base_fertilizer_name=str(management["基肥肥料名称"]),
        base_fertilizer_date=_excel_date(management["基肥-施用时间"]),
        base_n_kg_ha=_float(management["基肥-氮肥施肥量"]),
        base_p_kg_ha=_float(management["基肥-磷肥施肥量"]),
        base_k_kg_ha=_float(management["基肥-钾肥施肥量"]),
        topdress_name=str(management["追肥-肥料名称"]),
        topdress_date=_excel_date(management["追肥-施用时间"]),
        topdress_n_kg_ha=_float(management["追肥-氮肥施肥量"]),
        topdress_p_kg_ha=_float(management["追肥-磷肥施肥量"]),
        topdress_k_kg_ha=_float(management["追肥-钾肥施肥量"]),
        nutrient_index=nutrient_index,
    )
    weather = {
        item.day: item
        for item in (
            _weather_day(row) for row in tables["气象数据"] if row.get("日期") is not None
        )
    }
    use_hydro = os.environ.get("FARM_TANGYAN5_USE_TABLE_SOIL_HYDROLOGY", "1").lower() not in (
        "0",
        "false",
        "no",
    )
    soil_hydro = tangyan5_soil_hydrology_from_lab_rows(soil_rows) if use_hydro else None
    return Tangyan5Trial(plot=plot, weather_by_date=weather, soil_hydrology=soil_hydro)


def load_tangyan5_embedded_trial(plot_id: str = DEFAULT_PLOT_ID) -> Tangyan5Trial:
    try:
        raw = TANGYAN5_BASE_PLOTS[plot_id]
    except KeyError as exc:
        known = ", ".join(sorted(TANGYAN5_BASE_PLOTS))
        raise ValueError(f"Unknown Tangyan5 plot_id {plot_id!r}; known: {known}") from exc

    plot = Tangyan5Plot(
        plot_id=str(raw["plot_id"]),
        variety=str(raw["variety"]),
        seed_type=str(raw["seed_type"]),
        planting_date=_iso_date(str(raw["planting_date"])),
        emergence_date=_iso_date(str(raw["emergence_date"])),
        maturity_date=_iso_date(str(raw["maturity_date"])),
        harvest_date=_iso_date(str(raw["harvest_date"])),
        plot_area_m2=float(raw["plot_area_m2"]),
        plot_yield_kg=float(raw["plot_yield_kg"]),
        seed_density_plants_ha=float(raw["seed_density_plants_ha"]),
        seed_depth_cm=float(raw["seed_depth_cm"]),
        seed_spacing_cm=float(raw["seed_spacing_cm"]),
        base_fertilizer_name=str(raw["base_fertilizer_name"]),
        base_fertilizer_date=_iso_date(str(raw["base_fertilizer_date"])),
        base_n_kg_ha=float(raw["base_n_kg_ha"]),
        base_p_kg_ha=float(raw["base_p_kg_ha"]),
        base_k_kg_ha=float(raw["base_k_kg_ha"]),
        topdress_name=str(raw["topdress_name"]),
        topdress_date=_iso_date(str(raw["topdress_date"])),
        topdress_n_kg_ha=float(raw["topdress_n_kg_ha"]),
        topdress_p_kg_ha=float(raw["topdress_p_kg_ha"]),
        topdress_k_kg_ha=float(raw["topdress_k_kg_ha"]),
        nutrient_index=float(raw["nutrient_index"]),
    )

    seed = int(TANGYAN5_BASE_WEATHER["seed"])
    generator = WeatherGenerator(config=default_harbin_soybean_config(), seed=seed)
    start = plot.planting_date
    end = plot.harvest_date + timedelta(days=10)
    weather_by_date: dict[date, Tangyan5WeatherDay] = {}
    print(f"Generating weather from {start} to {end} with seed {seed}...")
    for item in generator.generate(start_date=start, end_date=end):
        solar_w = float(item.solar_rad_mj_m2) / 0.0864
        weather_by_date[item.day] = Tangyan5WeatherDay(
            day=item.day,
            temp_mean_c=float(item.air_temp_mean_c),
            temp_min_c=float(item.air_temp_min_c),
            temp_max_c=float(item.air_temp_max_c),
            wind_ms=float(item.wind_ms),
            rain_mm=float(item.rain_mm),
            humidity_pct=60.0,
            solar_w_m2=solar_w,
            solar_mj_m2=float(item.solar_rad_mj_m2),
        )

    return Tangyan5Trial(
        plot=plot,
        weather_by_date=weather_by_date,
        soil_hydrology=None,
    )


def _one_by(rows: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    for row in rows:
        if row.get(key) == value:
            return row
    raise ValueError(f"Could not find {key}={value!r}")


def _seed_type_for_variety(variety: str) -> str:
    if "黑河43" in variety:
        return SeedType.HEIHE43.value
    if "黑河" in variety:
        return SeedType.STANDARD.value
    if "黑农" in variety:
        return SeedType.STRESS_TOLERANT.value
    return SeedType.STANDARD.value


def _spacing_cm_from_density(seed_density_plants_ha: float) -> float:
    field_area_ha = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M * 64 / 10000.0
    plants_per_ridge = seed_density_plants_ha * field_area_ha / 64
    return round(FIELD_LENGTH_M * 2 * 100.0 / plants_per_ridge, 2)


def _soil_initial_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    planting = [r for r in rows if str(r.get("生育期", "")).strip() == "播种"]
    return planting if planting else rows


def _mean_soil_value(rows: list[dict[str, Any]], column: str) -> float | None:
    values: list[float] = []
    for row in rows:
        raw = row.get(column)
        if raw is None or (isinstance(raw, str) and raw.strip() in ("", "/")):
            continue
        values.append(_float(raw))
    if not values:
        return None
    return sum(values) / len(values)


def _soil_depth_weight(row: dict[str, Any]) -> float:
    depth_cm = _float(row.get("土层深度cm"), 40.0)
    if depth_cm <= 20.0:
        return 0.50
    if depth_cm <= 40.0:
        return 0.30
    return 0.20


def _weighted_mean_soil_value(rows: list[dict[str, Any]], column: str) -> float | None:
    weighted_values: list[tuple[float, float]] = []
    for row in rows:
        raw = row.get(column)
        if raw is None or (isinstance(raw, str) and raw.strip() in ("", "/")):
            continue
        weighted_values.append((_float(raw), _soil_depth_weight(row)))
    if not weighted_values:
        return None
    total_weight = sum(weight for _, weight in weighted_values)
    return sum(value * weight for value, weight in weighted_values) / total_weight


def _nutrient_index_from_soil(rows: list[dict[str, Any]]) -> float:
    if not rows:
        # Typical productive Mollisol strip prior when no lab chemistry sheet.
        return 0.82
    use = _soil_initial_rows(rows)
    alkaline_n = _weighted_mean_soil_value(use, "碱解氮mg/kg")
    available_p = _weighted_mean_soil_value(use, "有效磷mg/kg")
    available_k = _weighted_mean_soil_value(use, "速效钾mg/kg")
    organic_c = _weighted_mean_soil_value(use, "总有机碳%")
    if (
        alkaline_n is None
        or available_p is None
        or available_k is None
        or organic_c is None
    ):
        return 0.82
    score = (
        min(1.0, alkaline_n / 170.0) * 0.32
        + min(1.0, available_p / 38.0) * 0.24
        + min(1.0, available_k / 215.0) * 0.22
        + min(1.0, organic_c / 1.65) * 0.22
    )
    return round(max(0.45, min(1.02, score)), 3)


def _tangyan5_texture_row_means(
    rows: list[dict[str, Any]],
) -> tuple[float, float, float, str] | None:
    """Return mean (clay%, silt%, sand%) from particle-size columns, or None."""
    if not rows:
        return None
    planting = [
        r
        for r in rows
        if str(r.get("生育期", "")).strip() == "播种"
        and _mean_soil_value([r], "0-2um/%") is not None
        and _mean_soil_value([r], "2-20um/%") is not None
        and _mean_soil_value([r], "20-2000um/%") is not None
    ]
    texture_rows = [
        r
        for r in rows
        if _mean_soil_value([r], "0-2um/%") is not None
        and _mean_soil_value([r], "2-20um/%") is not None
        and _mean_soil_value([r], "20-2000um/%") is not None
    ]
    use = planting if planting else texture_rows
    if not use:
        return None
    clay = _mean_soil_value(use, "0-2um/%") or 0.0
    silt = _mean_soil_value(use, "2-20um/%") or 0.0
    sand = _mean_soil_value(use, "20-2000um/%") or 0.0
    tot = clay + silt + sand
    if tot <= 1e-6:
        return None
    src = "播种_mean" if planting else "all_depths_mean"
    return (100.0 * clay / tot, 100.0 * silt / tot, 100.0 * sand / tot, src)


def _clip_float(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def heilongjiang_reference_soil_hydrology() -> Tangyan5SoilHydrology:
    """Songnen / NE Heilongjiang Mollisol-style defaults when the table is silent.

    Volumetric anchors are a simplified rain-fed soybean belt prior (not a
    county-specific measured pit profile). Used alone when lab texture is missing,
    and as a convex blend partner when particle-size columns are present.
    """
    return Tangyan5SoilHydrology(
        clay_pct=22.0,
        silt_pct=38.0,
        sand_pct=40.0,
        source_rows="heilongjiang_reference",
        wilting_point_vwc=0.14,
        field_capacity_vwc=0.33,
        saturation_vwc=0.45,
        initial_planting_soil_vwc=0.27,
    )


def _organic_carbon_adjusted_reference_soil_hydrology(
    rows: list[dict[str, Any]],
) -> Tangyan5SoilHydrology:
    ref = heilongjiang_reference_soil_hydrology()
    use = _soil_initial_rows(rows)
    organic_c_pct = _weighted_mean_soil_value(use, "总有机碳%")
    if organic_c_pct is None:
        return ref

    # Organic carbon is the only water-retention clue available for plots that
    # have chemistry rows but no particle-size rows. Keep the Heilongjiang
    # reference texture, then make modest scenario-level shifts in storage.
    carbon_delta = organic_c_pct - 1.20
    wilting_point = _clip_float(
        ref.wilting_point_vwc + 0.006 * carbon_delta,
        0.13,
        0.16,
    )
    field_capacity = _clip_float(
        ref.field_capacity_vwc + 0.025 * carbon_delta,
        0.30,
        0.37,
    )
    saturation = _clip_float(
        ref.saturation_vwc + 0.020 * carbon_delta,
        field_capacity + 0.08,
        0.50,
    )
    initial_vwc = _clip_float(
        field_capacity * 0.82,
        wilting_point + 0.04,
        field_capacity - 0.015,
    )
    return Tangyan5SoilHydrology(
        clay_pct=ref.clay_pct,
        silt_pct=ref.silt_pct,
        sand_pct=ref.sand_pct,
        source_rows="heilongjiang_reference_organic_c_adjusted",
        wilting_point_vwc=round(wilting_point, 4),
        field_capacity_vwc=round(field_capacity, 4),
        saturation_vwc=round(saturation, 4),
        initial_planting_soil_vwc=round(initial_vwc, 4),
    )


def tangyan5_soil_hydrology_from_lab_rows(
    rows: list[dict[str, Any]],
) -> Tangyan5SoilHydrology | None:
    """Return scene-level hydraulic state from the soil sheet.

    The workbook does not contain measured VWC/water-retention columns. When
    particle-size rows exist, derive reduced SoilEngine thresholds from texture
    and organic carbon; otherwise use the Heilongjiang reference profile while
    still taking nutrient status from the plot's chemistry rows.
    """
    texture = _tangyan5_texture_row_means(rows)
    if texture is None:
        return _organic_carbon_adjusted_reference_soil_hydrology(rows)
    clay, silt, sand, source_rows = texture
    use = _soil_initial_rows(rows)
    organic_c_pct = _mean_soil_value(use, "总有机碳%")
    if organic_c_pct is None:
        organic_c_pct = 1.2

    wilting_point = _clip_float(
        0.06 + 0.0045 * clay + 0.0008 * silt + 0.004 * organic_c_pct,
        0.10,
        0.20,
    )
    field_capacity = _clip_float(
        0.22 + 0.0035 * clay + 0.0012 * silt + 0.015 * organic_c_pct,
        wilting_point + 0.10,
        0.40,
    )
    saturation = _clip_float(
        0.42 + 0.0015 * clay + 0.0004 * silt + 0.005 * organic_c_pct,
        field_capacity + 0.06,
        0.52,
    )
    initial_vwc = _clip_float(
        field_capacity * 0.82,
        wilting_point + 0.04,
        field_capacity - 0.015,
    )
    return Tangyan5SoilHydrology(
        clay_pct=round(clay, 3),
        silt_pct=round(silt, 3),
        sand_pct=round(sand, 3),
        source_rows=f"lab_texture_{source_rows}",
        wilting_point_vwc=round(wilting_point, 4),
        field_capacity_vwc=round(field_capacity, 4),
        saturation_vwc=round(saturation, 4),
        initial_planting_soil_vwc=round(initial_vwc, 4),
    )


def _weather_day(row: dict[str, Any]) -> Tangyan5WeatherDay:
    day = _excel_date(row["日期"])
    temp_max = _float(row["日最高气温"])
    temp_min = _float(row["日最低气温"])
    temp_mean = (temp_max + temp_min) / 2.0
    wind = _float(row["10米高风速"])
    rain = _float(row["日降水量"])
    vapor_kpa = _float(row["水汽压"], 0.7)
    saturation_kpa = 0.6108 * math.exp((17.27 * temp_mean) / (temp_mean + 237.3))
    humidity = max(20.0, min(100.0, vapor_kpa / saturation_kpa * 100.0))
    solar_kj = _float(row.get("日太阳总辐射"), 0.0)
    if solar_kj > 0.0:
        solar_mj_raw = solar_kj / 1000.0
        solar_mj = solar_mj_raw
    else:
        sunshine_h = _float(row.get("日照时数"), 8.0)

        lat_rad = math.radians(45.75)  # 哈尔滨/糖研5号地近似纬度
        doy = day.timetuple().tm_yday

        dr = 1.0 + 0.033 * math.cos(2.0 * math.pi * doy / 365.0)
        delta = 0.409 * math.sin(2.0 * math.pi * doy / 365.0 - 1.39)

        x = -math.tan(lat_rad) * math.tan(delta)
        x = max(-1.0, min(1.0, x))
        omega_s = math.acos(x)

        ra = (24.0 * 60.0 / math.pi) * 0.0820 * dr * (
                omega_s * math.sin(lat_rad) * math.sin(delta)
                + math.cos(lat_rad) * math.cos(delta) * math.sin(omega_s)
        )

        max_daylight_h = 24.0 / math.pi * omega_s
        sunshine_h = max(0.0, min(sunshine_h, max_daylight_h))

        solar_mj = (0.25 + 0.50 * sunshine_h / max_daylight_h) * ra
        solar_mj = max(3.0, min(30.0, solar_mj))
    # The workbook header says kJ/m2, but Tangyan5 rows (including sunshine-hour
    # fallback rows) can reach 40-50 MJ/m2/day in late May, above plausible
    # ground-level daily global radiation for this latitude. Keep the table
    # weather shape but cap to a conservative clear-sky agronomic bound so ET
    # does not dry the seed-zone to wilting point in a few days.
    solar_mj = min(solar_mj, TANGYAN5_SOLAR_MJ_M2_MAX)
    solar_w = solar_mj / 0.0864
    return Tangyan5WeatherDay(
        day=day,
        temp_mean_c=temp_mean,
        temp_min_c=temp_min,
        temp_max_c=temp_max,
        wind_ms=wind,
        rain_mm=rain,
        humidity_pct=humidity,
        solar_w_m2=solar_w,
        solar_mj_m2=solar_mj,
    )


def _local_7am_timestamp(day: date) -> float:
    return (
        datetime(day.year, day.month, day.day, 7, 0, 0, tzinfo=timezone.utc).timestamp()
        - 8 * 3600
    )


@register_scenario("scenario_tangyan5_base_full_season")
class ScenarioTangyan5BaseFullSeason(Scenario):
    """Whole-field base full-season scenario fitted from curated Tangyan5 inputs.

    The embedded rows are small-plot observations. This scenario uses one
    selected plot row as the calibration treatment, then applies that treatment
    to the whole FarmWorld field: all 64 ridges.
    """

    workbook_path: str = DEFAULT_WORKBOOK_PATH
    plot_id: str = os.environ.get("FARM_TANGYAN5_PLOT", DEFAULT_PLOT_ID)
    weather_source: str = os.environ.get("FARM_TANGYAN5_WEATHER_SOURCE", WEATHER_SOURCE_ENGINE)
    weather_seed: int = int(os.environ.get("FARM_TANGYAN5_WEATHER_SEED", "5"))
    calibration_profile: str = os.environ.get(
        "FARM_TANGYAN5_CALIBRATION_PROFILE",
        CALIBRATION_PROFILE_NONE,
    )
    management_path: str = os.environ.get(
        "FARM_TANGYAN5_MANAGEMENT_PATH",
        MANAGEMENT_PATH_EXPERT_BASELINE,
    )
    duration: float | None = 180 * 24 * 3600
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        self.trial = load_tangyan5_embedded_trial(self.plot_id)  # type: ignore[attr-defined]
        self.start_time = _local_7am_timestamp(self.trial.plot.planting_date)  # type: ignore[attr-defined]

        aui = AgentUserInterface()
        farm_world = FarmWorldApp()
        weather = WeatherApp()
        sensor = SensorApp(farm_world_app=farm_world)
        tractor = TractorApp(farm_world_app=farm_world, weather_app=weather)
        system = SystemApp()
        self.apps = [aui, farm_world, weather, sensor, tractor, system]
        self._install_shared_time_manager()
        self._configure_initial_state()
        farm_world.attach_system_app(system)

    def _install_shared_time_manager(self) -> None:
        tm = TimeManager()
        tm.reset(float(self.start_time))
        for app in self.apps or []:
            app.register_time_manager(tm)

    def _configure_initial_state(self) -> None:
        trial: Tangyan5Trial = self.trial  # type: ignore[attr-defined]
        plot = trial.plot
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        tractor = self.get_typed_app(TractorApp)

        hydro = trial.soil_hydrology
        profile_meta: dict[str, Any] = {}
        if hydro is not None:
            profile_meta["tangyan5_soil_hydrology"] = {
                "clay_pct": hydro.clay_pct,
                "silt_pct": hydro.silt_pct,
                "sand_pct": hydro.sand_pct,
                "source_rows": hydro.source_rows,
                "wilting_point_vwc": hydro.wilting_point_vwc,
                "field_capacity_vwc": hydro.field_capacity_vwc,
                "saturation_vwc": hydro.saturation_vwc,
                "initial_planting_soil_vwc": hydro.initial_planting_soil_vwc,
            }

        if self.calibration_profile != CALIBRATION_PROFILE_NONE:
            profile_meta["calibration_profile"] = self.calibration_profile

        farm_world.configure_physics_profile(
            profile_name="tangyan5_actual_2025_base",
            location="糖研院5号地",
            scenario_type="actual_table_base_full_season",
            random_seed=5,
            plot_id=plot.plot_id,
            variety=plot.variety,
            source_data=DEFAULT_TANGYAN5_DATA_SOURCE,
            **profile_meta,
        )
        self._install_weather_overrides()
        self._set_weather_for_date(plot.planting_date)
        farm_world.set_season_phase(SeasonPhase.PLANTING.value)

        seeds_needed = int(
            plot.seed_density_plants_ha * plot.simulated_field_area_m2 / 10000.0
        )
        farm_world._inventory.seed_stock[plot.seed_type] = max(seeds_needed * 2, 500000)
        farm_world._inventory.fertilizer_kg = 2000.0
        farm_world._inventory.pesticide_liters = 2000.0
        farm_world._inventory.fuel_liters = 1500.0
        tractor._fuel_tank_l = 100.0

        if hydro is not None:
            sp0 = farm_world.physics.soil.params
            span = max(0.0, hydro.field_capacity_vwc - hydro.wilting_point_vwc)
            water_stress_vwc = hydro.wilting_point_vwc + max(0.03, 0.35 * span)
            water_stress_vwc = min(water_stress_vwc, hydro.field_capacity_vwc - 1e-3)
            irr_trig = hydro.wilting_point_vwc + 0.25 * span
            irr_trig = min(irr_trig, water_stress_vwc - 0.01)
            irr_trig = max(hydro.wilting_point_vwc + 0.01, irr_trig)
            farm_world.physics.soil.params = replace(
                sp0,
                wilting_point_vwc=hydro.wilting_point_vwc,
                field_capacity_vwc=hydro.field_capacity_vwc,
                saturation_vwc=hydro.saturation_vwc,
                water_stress_vwc=water_stress_vwc,
                irrigation_trigger_vwc=irr_trig,
            )
        initial_ridge_vwc = (
            hydro.initial_planting_soil_vwc
            if hydro is not None
            else (
                0.30
                if self.calibration_profile
                == CALIBRATION_PROFILE_HEILONGJIANG_BLACK_SOIL_V2
                else 0.26
            )
        )
        weather_day = trial.weather_by_date.get(plot.planting_date)
        soil_temp = weather_day.temp_mean_c if weather_day is not None else 13.0
        for ridge_id in range(farm_world.num_ridges):
            ridge = farm_world.get_ridge(ridge_id)
            ridge.planted = False
            ridge.seed_type = None
            ridge.days_since_planted = 0
            ridge.growth_stage = GrowthStage.BARE.value
            ridge.soil_vwc = initial_ridge_vwc
            ridge.soil_temp_c = soil_temp
            ridge.nutrient_index = plot.nutrient_index
            ridge.yield_potential = 1.0
            ridge.pest_pressure_base = 0.03
            ridge.pest_pressure = 0.03
            ridge.disease_pressure_base = 0.03
            ridge.disease_pressure = 0.03

    # def _apply_calibration_profile(self) -> None:
    #     if self.calibration_profile == CALIBRATION_PROFILE_NONE:
    #         return
    #     if self.calibration_profile not in {
    #         CALIBRATION_PROFILE_HEILONGJIANG_BLACK_SOIL_V1,
    #         CALIBRATION_PROFILE_HEILONGJIANG_BLACK_SOIL_V2,
    #     }:
    #         raise ValueError(
    #             f"Unknown calibration_profile {self.calibration_profile!r}; "
    #             f"expected {CALIBRATION_PROFILES}"
    #         )
    #
    #     farm_world = self.get_typed_app(FarmWorldApp)
    #     physics = farm_world.physics
    #
    #     if self.calibration_profile == CALIBRATION_PROFILE_HEILONGJIANG_BLACK_SOIL_V1:
    #         # Tangyan5 black-soil high-yield local calibration.
    #         #
    #         # Facts from the ridge-0 trace before this profile:
    #         # - LAI peaked below 0.8 and biomass stayed near 125 g/m2.
    #         # - Root VWC fell into severe stress during seed fill.
    #         #
    #         # This profile is intentionally kept as the original aggressive
    #         # yield-fitting experiment for reproducibility.
    #         soil_overrides = {
    #             "wilting_point_vwc": 0.14,
    #             "field_capacity_vwc": 0.40,
    #             "saturation_vwc": 0.52,
    #             "root_depth_m": 1.20,
    #             "max_infiltration_mm_day": 95.0,
    #             "top_drainage_rate": 0.24,
    #             "root_drainage_rate": 0.05,
    #             "rainfall_capture_efficiency": 0.98,
    #             "irrigation_efficiency": 0.95,
    #             "radiation_et_coeff": 0.28,
    #             "bare_soil_evap_fraction": 0.28,
    #         }
    #     else:
    #         # More conservative Heilongjiang black-soil calibration.
    #         #
    #         # V2 keeps the "black soil stores more water than the default loam"
    #         # direction, but avoids using near-perfect rainfall capture and
    #         # extremely low drainage as a hidden yield lever. The remaining yield
    #         # gap should be handled by canopy/phenology/harvest-timing work, not
    #         # by making the soil bucket unrealistically lossless.
    #         soil_overrides = {
    #             "wilting_point_vwc": 0.14,
    #             "field_capacity_vwc": 0.36,
    #             "saturation_vwc": 0.48,
    #             "root_depth_m": 0.8,
    #             "max_infiltration_mm_day": 65.0,
    #             "top_drainage_rate": 0.40,
    #             "root_drainage_rate": 0.15,
    #             "rainfall_capture_efficiency": 0.92,
    #             "irrigation_efficiency": 0.92,
    #             "top_root_redistribution_rate": 0.18,
    #             "top_root_redistribution_deadband_vwc": 0.03,
    #             "radiation_et_coeff": 0.42,
    #             "bare_soil_evap_fraction": 0.42,
    #         }
    #     physics.soil.params = replace(physics.soil.params, **soil_overrides)
    #     if self.calibration_profile == CALIBRATION_PROFILE_HEILONGJIANG_BLACK_SOIL_V2:
    #         physics.yield_recovery.params = replace(
    #             physics.yield_recovery.params,
    #             shatter_delay_grace_days=14,
    #             shatter_loss_per_day_after_grace=0.003,
    #             shatter_loss_dry_bonus=0.005,
    #             wet_dry_cycle_loss=0.006,
    #         )
    #     physics.management.params = replace(
    #         physics.management.params,
    #         nutrient_stress_min=0.72,
    #         nutrient_index_zero_growth=0.30,
    #         daily_nutrient_decay=0.0015,
    #         nutrient_uptake_coeff=0.0020,
    #         base_fertilizer_gain=1.45,
    #         fertigation_gain=0.32,
    #     )
    #     physics.canopy.params.initial_lai_at_emergence = 0.10


    def _install_weather_overrides(self) -> None:
        trial: Tangyan5Trial = self.trial  # type: ignore[attr-defined]
        physics = self.get_typed_app(FarmWorldApp).physics
        if self.weather_source == WEATHER_SOURCE_ACTUAL:
            physics.weather_generator = ActualWeatherGenerator(trial.weather_by_date)  # type: ignore[assignment]
        elif self.weather_source == WEATHER_SOURCE_ENGINE:
            physics.weather_generator = WeatherGenerator(
                config=default_harbin_soybean_config(),
                seed=int(self.weather_seed),
            )
        else:
            raise ValueError(
                f"Unknown weather_source {self.weather_source!r}; expected {WEATHER_SOURCES}"
            )

    def _set_weather_for_date(self, day: date) -> None:
        trial: Tangyan5Trial = self.trial  # type: ignore[attr-defined]
        weather = self.get_typed_app(WeatherApp)
        current = self._weather_day_for_date(day)
        if current is None:
            return
        forecast = []
        for offset in range(1, 8):
            item = self._weather_day_for_date(day + timedelta(days=offset))
            if item is None:
                continue
            forecast.append(_weather_app_day_dict(item))
        weather.set_weather(
            date=day.isoformat(),
            temp_c=current.temp_mean_c,
            humidity_pct=current.humidity_pct,
            wind_speed_ms=current.wind_ms,
            rainfall_mm=current.rain_mm,
            solar_radiation=current.solar_w_m2,
            forecast=forecast,
            avg_soil_vwc=self.get_typed_app(FarmWorldApp).get_avg_vwc(),
        )

    def _weather_day_for_date(self, day: date) -> Tangyan5WeatherDay | None:
        trial: Tangyan5Trial = self.trial  # type: ignore[attr-defined]
        if self.weather_source == WEATHER_SOURCE_ACTUAL:
            return trial.weather_by_date.get(day)

        if self.weather_source != WEATHER_SOURCE_ENGINE:
            raise ValueError(
                f"Unknown weather_source {self.weather_source!r}; expected {WEATHER_SOURCES}"
            )

        physics = self.get_typed_app(FarmWorldApp).physics
        generator = physics.weather_generator
        if generator is None:
            return None
        from are.simulation.apps.farm_world.physics_orchestrator import _generate_weather_day

        generated = _generate_weather_day(generator, day)
        if generated is None:
            return None
        solar_w = float(generated.solar_rad_mj_m2) / 0.0864
        return Tangyan5WeatherDay(
            day=day,
            temp_mean_c=float(generated.air_temp_mean_c),
            temp_min_c=float(generated.air_temp_min_c),
            temp_max_c=float(generated.air_temp_max_c),
            wind_ms=float(generated.wind_ms),
            rain_mm=float(generated.rain_mm),
            humidity_pct=55.0,
            solar_w_m2=solar_w,
            solar_mj_m2=float(generated.solar_rad_mj_m2),
        )

    def _apply_stress_free_oracle_intervention(self, call, label: str) -> dict[str, Any] | None:
        """Keep the whole field near non-limiting water/nutrient/biotic state.

        This is an upper-bound oracle, not an expert agronomy baseline: it reads
        hidden engine state every day and applies only the smallest intervention
        needed to keep growth stress near 1.0.
        """
        farm_world = self.get_typed_app(FarmWorldApp)
        if not farm_world.physics_active:
            return None

        physics = farm_world.physics
        stages = [state.stage.value for state in physics.phenology.states.values()]
        if stages and all(stage == "R8_FULL_MATURITY" for stage in stages):
            return None

        mgmt_states = list(physics.management.states.values())
        soil_states = list(physics.soil.states.values())
        biotic_states = list(physics.biotic.states.values())
        if not mgmt_states or not soil_states:
            return None

        mgmt_params = physics.management.params
        soil_params = physics.soil.params
        min_nutrient_index = min(float(state.nutrient_index) for state in mgmt_states)
        min_nutrient_stress = min(float(state.nutrient_stress) for state in mgmt_states)
        min_root_vwc = min(float(state.root_vwc) for state in soil_states)
        max_weed_pressure = max(float(state.weed_pressure) for state in biotic_states)
        max_insect_pressure = max(float(state.insect_pressure) for state in biotic_states)
        max_disease_pressure = max(float(state.disease_pressure) for state in biotic_states)

        target_nutrient_index = min(
            float(mgmt_params.nutrient_excess_threshold) - 0.01,
            float(mgmt_params.nutrient_index_full_growth) + 0.03,
        )
        nutrient_amount = 0.0
        if min_nutrient_stress < 0.995 or min_nutrient_index < target_nutrient_index:
            nutrient_gap = max(0.0, target_nutrient_index - min_nutrient_index)
            nutrient_amount = min(
                0.60,
                max(0.03, nutrient_gap / max(1e-6, float(mgmt_params.fertigation_gain))),
            )

        target_root_vwc = max(
            float(soil_params.water_stress_vwc) + 0.04,
            min(float(soil_params.field_capacity_vwc) - 0.08, 0.24),
        )
        water_mm = 0.0
        if min_root_vwc < target_root_vwc:
            root_deficit_mm = (target_root_vwc - min_root_vwc) * float(soil_params.root_depth_m) * 1000.0
            water_mm = min(10.0, max(2.0, root_deficit_mm * 0.20))

        result: dict[str, Any] = {
            "label": label,
            "pre_min_nutrient_index": round(min_nutrient_index, 4),
            "pre_min_nutrient_stress": round(min_nutrient_stress, 4),
            "target_nutrient_index": round(target_nutrient_index, 4),
            "pre_min_root_vwc": round(min_root_vwc, 4),
            "target_root_vwc": round(target_root_vwc, 4),
            "pre_max_weed_pressure": round(max_weed_pressure, 4),
            "pre_max_insect_pressure": round(max_insect_pressure, 4),
            "pre_max_disease_pressure": round(max_disease_pressure, 4),
        }

        if nutrient_amount > 0.0 or water_mm > 0.0:
            action_nutrient = max(0.01, nutrient_amount)
            action_water = max(0.1, water_mm)
            result["fertigation"] = call(
                f"{label}_stress_free_fertigation",
                farm_world.apply_fertigation,
                0,
                63,
                round(action_nutrient, 4),
                round(action_water, 2),
            )
            result["nutrient_amount"] = round(action_nutrient, 4)
            result["water_mm"] = round(action_water, 2)

        biotic_reset: dict[str, float] = {}
        if max_weed_pressure > 0.12:
            biotic_reset["weed_pressure"] = 0.03
        if max_insect_pressure > 0.12:
            biotic_reset["insect_pressure"] = 0.02
        if max_disease_pressure > 0.12:
            biotic_reset["disease_pressure"] = 0.02
        if biotic_reset:
            physics.biotic.set_pressure(list(range(farm_world.num_ridges)), **biotic_reset)
            result["idealized_biotic_reset"] = biotic_reset
            call(
                f"{label}_stress_free_biotic_reset",
                lambda: {"status": "ok", "pressures": biotic_reset},
            )

        if "fertigation" not in result and "idealized_biotic_reset" not in result:
            return None

        return result

    def build_events_flow(self) -> None:
        trial: Tangyan5Trial = self.trial  # type: ignore[attr-defined]
        plot = trial.plot
        aui = self.get_typed_app(AgentUserInterface)
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(
                content=(
                    f"请把整块 64 垄地都按糖研院5号地 {plot.plot_id}/{plot.variety} 的真实管理表执行一个基础全季："
                    f"{plot.planting_date} 播种，{plot.topdress_date} 追肥，"
                    f"{plot.harvest_date} 收获。Excel 小区数据只作为整块地的校准参数；"
                    "实际操作要覆盖 0-63 全部垄。每次操作前读取天气、库存和农场状态。"
                )
            ).with_id("briefing").depends_on(None, delay_seconds=5)
            prev = briefing
            prev = weather.get_current_weather().oracle().with_id("o_weather_planting").depends_on(prev, delay_seconds=1)
            prev = sensor.read_soil_sensors().oracle().with_id("o_soil_planting").depends_on(prev, delay_seconds=1)
            prev = tractor.get_status().oracle().with_id("o_tractor_before_prep").depends_on(prev, delay_seconds=1)
            prev = farm_world.get_inventory().oracle().with_id("o_inventory_before_prep").depends_on(prev, delay_seconds=1)
            prev = tractor.attach_implement("grader").oracle().with_id("o_attach_grader").depends_on(prev, delay_seconds=1)
            prev = tractor.level().oracle().with_id("o_level").depends_on(prev, delay_seconds=1)
            prev = tractor.detach_implement().oracle().with_id("o_detach_grader").depends_on(prev, delay_seconds=1)
            prev = tractor.load_fertilizer(500.0).oracle().with_id("o_load_base_fertilizer").depends_on(prev, delay_seconds=1)
            prev = tractor.base_fertilize().oracle().with_id("o_base_fertilize").depends_on(prev, delay_seconds=1)
            prev = tractor.form_ridges(DEFAULT_RIDGE_WIDTH_M).oracle().with_id("o_form_ridges").depends_on(prev, delay_seconds=1)
            prev = tractor.load_seeds(plot.seed_type, 300000).oracle().with_id("o_load_seed_1").depends_on(prev, delay_seconds=1)
            for start in range(0, 64, 4):
                prev = tractor.plant_seeds(start, start + 3, plot.seed_depth_cm, plot.seed_spacing_cm).oracle().with_id(f"o_plant_{start}_{start + 3}").depends_on(prev, delay_seconds=1)
                if start == 40:
                    prev = tractor.load_seeds(plot.seed_type, 300000).oracle().with_id("o_load_seed_2").depends_on(prev, delay_seconds=1)
            prev = farm_world.commit_daily_physics().oracle().with_id("o_commit_planting").depends_on(prev, delay_seconds=1)
            prev = system.advance_time(days=max(1, (plot.topdress_date - plot.planting_date).days)).oracle().with_id("o_wait_to_topdress").depends_on(prev, delay_seconds=1)
            prev = farm_world.apply_fertigation(0, 63, nutrient_amount=0.4, water_mm=5.0).oracle().with_id("o_table_topdress").depends_on(prev, delay_seconds=1)
            prev = system.advance_time(days=max(1, (plot.harvest_date - plot.topdress_date).days)).oracle().with_id("o_wait_to_harvest").depends_on(prev, delay_seconds=1)
            prev = weather.get_current_weather().oracle().with_id("o_weather_harvest").depends_on(prev, delay_seconds=1)
            prev = farm_world.get_farm_overview().oracle().with_id("o_overview_harvest").depends_on(prev, delay_seconds=1)
            prev = tractor.refuel(100.0).oracle().with_id("o_refuel_harvest").depends_on(prev, delay_seconds=1)
            prev = tractor.attach_implement("harvester").oracle().with_id("o_attach_harvester").depends_on(prev, delay_seconds=1)
            for start in range(0, 64, 4):
                prev = tractor.harvest(start, start + 3).oracle().with_id(f"o_harvest_{start}_{start + 3}").depends_on(prev, delay_seconds=1)
                if start % 8 == 4:
                    prev = tractor.unload_grain().oracle().with_id(f"o_unload_after_{start + 3}").depends_on(prev, delay_seconds=1)
            prev = farm_world.dry_grain(13.5).oracle().with_id("o_dry_grain").depends_on(prev, delay_seconds=1)
            prev = farm_world.store_grain().oracle().with_id("o_store_grain").depends_on(prev, delay_seconds=1)
            prev = aui.send_message_to_user(content="糖研院5号地 base full-season oracle 已完成。").oracle().with_id("o_report").depends_on(prev, delay_seconds=1)
            self.events = collect_event_graph(briefing)

    def run_table_oracle(self, fit_observed_harvest_state: bool = False) -> dict[str, Any]:
        trial: Tangyan5Trial = self.trial  # type: ignore[attr-defined]
        plot = trial.plot
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)
        operations: list[dict[str, Any]] = []

        def call(name: str, fn, *args, **kwargs) -> dict[str, Any]:
            result = fn(*args, **kwargs)
            operations.append({"step": name, "result": result})
            return result

        self._set_weather_for_date(plot.planting_date)
        call("attach_grader", tractor.attach_implement, "grader")
        call("level", tractor.level)
        call("detach_grader", tractor.detach_implement)
        call("load_base_fertilizer", tractor.load_fertilizer, 500.0)
        call("base_fertilize", tractor.base_fertilize)
        call("form_ridges", tractor.form_ridges, DEFAULT_RIDGE_WIDTH_M)

        for start in range(0, 64, 4):
            seeds_needed = int((4 * FIELD_LENGTH_M * 2 * 100.0) / plot.seed_spacing_cm)
            if tractor._seed_hopper < seeds_needed:
                call(f"load_seed_before_{start}", tractor.load_seeds, plot.seed_type, 300000)
            call(
                f"plant_{start}_{start + 3}",
                tractor.plant_seeds,
                start,
                start + 3,
                plot.seed_depth_cm,
                plot.seed_spacing_cm,
            )
        call("commit_planting", farm_world.commit_daily_physics)

        self._advance_to_date(plot.topdress_date, system)
        self._set_weather_for_date(plot.topdress_date)
        topdress_strength = max(
            0.1,
            (plot.topdress_n_kg_ha + plot.topdress_p_kg_ha + plot.topdress_k_kg_ha)
            / 30.0,
        )

        # call("table_topdress_fertigation", farm_world.apply_fertigation, 0, 63, topdress_strength, 5.0)
        call("commit_topdress", farm_world.commit_daily_physics)

        self._advance_to_date(plot.harvest_date, system)
        self._set_weather_for_date(plot.harvest_date)
        physics_ridge_0_pre_harvest = tangyan5_physics_ridge_snapshot(farm_world, 0)
        if fit_observed_harvest_state:
            self._fit_observed_harvest_state()
        call("harvest_overview", farm_world.get_farm_overview)
        if tractor._fuel_tank_l < 40.0:
            call("refuel_harvest", tractor.refuel, 100.0)
        call("attach_harvester", tractor.attach_implement, "harvester")
        harvest_failed = False
        for start in range(0, 64, 4):
            self._set_weather_for_date(plot.harvest_date)
            result = call(f"harvest_{start}_{start + 3}", tractor.harvest, start, start + 3)
            if "error" in result:
                harvest_failed = True
                break
            if start % 8 == 4:
                call(f"unload_after_{start + 3}", tractor.unload_grain)
        if not harvest_failed and tractor._grain_bin_kg > 0.0:
            call("final_unload", tractor.unload_grain)
        if not harvest_failed:
            call("dry_grain", farm_world.dry_grain, 13.5)
            call("store_grain", farm_world.store_grain)

        inventory = farm_world.get_inventory()
        simulated_kg = float(inventory.get("warehouse_grain_kg", 0.0)) + float(
            inventory.get("harvest_grain_kg", 0.0)
        )
        return {
            "plot_id": plot.plot_id,
            "variety": plot.variety,
            "field_mode": FIELD_MODE,
            "weather_source": self.weather_source,
            "weather_seed": int(self.weather_seed),
            "calibration_profile": self.calibration_profile,
            "calibration_plot_area_m2": round(plot.plot_area_m2, 2),
            "simulated_field_area_m2": round(plot.simulated_field_area_m2, 2),
            "ridges_planted": 64,
            "mode": "fitted_observed_harvest_state" if fit_observed_harvest_state else "natural_engine",
            "actual_plot_yield_kg": round(plot.plot_yield_kg, 3),
            "actual_yield_kg_ha": round(plot.actual_yield_kg_ha, 2),
            "actual_scaled_field_yield_kg": round(plot.actual_scaled_field_yield_kg, 2),
            "simulated_field_yield_kg": round(simulated_kg, 2),
            "simulated_yield_kg_ha": round(
                simulated_kg / plot.simulated_field_area_m2 * 10000.0, 2
            ),
            "absolute_error_kg_ha": round(
                simulated_kg / plot.simulated_field_area_m2 * 10000.0
                - plot.actual_yield_kg_ha,
                2,
            ),
            "inventory": inventory,
            "physics": farm_world.physics.snapshot(),
            "physics_ridge_0_pre_harvest": physics_ridge_0_pre_harvest,
            "operations": operations,
        }

    def trace_ridge_daily(
        self,
        ridge_id: int = 0,
        fit_observed_harvest_state: bool = False,
    ) -> dict[str, Any]:
        """Run the base full-season scenario and record one ridge after each day tick."""
        trial: Tangyan5Trial = self.trial  # type: ignore[attr-defined]
        plot = trial.plot
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)
        rows: list[dict[str, Any]] = []
        operations: list[dict[str, Any]] = []
        previous: dict[str, float] | None = None

        if not 0 <= int(ridge_id) < farm_world.num_ridges:
            raise ValueError(f"ridge_id must be 0-{farm_world.num_ridges - 1}, got {ridge_id}")
        ridge_id = int(ridge_id)
        if self.management_path not in MANAGEMENT_PATHS:
            raise ValueError(
                f"Unknown management_path {self.management_path!r}; "
                f"expected {MANAGEMENT_PATHS}"
            )

        def oracle_date() -> date:
            # Match run_table_oracle/_advance_to_date: the existing FARM scenario
            # uses the shared UTC simulation date for day-boundary physics ticks.
            return datetime.fromtimestamp(
                farm_world.time_manager.time(), tz=timezone.utc
            ).date()

        def call(name: str, fn, *args, **kwargs) -> dict[str, Any]:
            result = fn(*args, **kwargs)
            operations.append({"step": name, "result": result})
            return result

        def apply_oracle_intervention(label: str) -> None:
            if self.management_path != MANAGEMENT_PATH_STRESS_FREE_ORACLE:
                return
            intervention = self._apply_stress_free_oracle_intervention(call, label)
            if intervention is not None:
                capture(f"{label}_stress_free_intervention")

        def capture(event: str) -> None:
            nonlocal previous
            day = oracle_date()
            row = tangyan5_ridge_daily_trace_row(
                farm_world,
                self._weather_day_for_date(day),
                ridge_id=ridge_id,
                event=event,
                weather_source=self.weather_source,
                field_date=day,
                target_yield_kg_ha=plot.actual_yield_kg_ha,
                previous=previous,
            )
            row["management_path"] = self.management_path
            previous = {
                "aboveground_biomass_g_m2": float(row["canopy_aboveground_biomass_g_m2"]),
                "yield_potential_g_m2": float(row["canopy_yield_potential_g_m2"]),
                "cumulative_apar_mj_m2": float(row["canopy_cumulative_apar_mj_m2"]),
            }
            rows.append(row)

        self._set_weather_for_date(plot.planting_date)
        capture("initial_configured")
        call("attach_grader", tractor.attach_implement, "grader")
        call("level", tractor.level)
        call("detach_grader", tractor.detach_implement)
        call("load_base_fertilizer", tractor.load_fertilizer, 500.0)
        call("base_fertilize", tractor.base_fertilize)
        call("form_ridges", tractor.form_ridges, DEFAULT_RIDGE_WIDTH_M)
        for start in range(0, 64, 4):
            seeds_needed = int((4 * FIELD_LENGTH_M * 2 * 100.0) / plot.seed_spacing_cm)
            if tractor._seed_hopper < seeds_needed:
                call(f"load_seed_before_{start}", tractor.load_seeds, plot.seed_type, 300000)
            call(
                f"plant_{start}_{start + 3}",
                tractor.plant_seeds,
                start,
                start + 3,
                plot.seed_depth_cm,
                plot.seed_spacing_cm,
            )
        call("commit_planting", farm_world.commit_daily_physics)
        capture("commit_planting")

        while oracle_date() < plot.topdress_date:
            apply_oracle_intervention(f"oracle_pre_{oracle_date().isoformat()}")
            system.advance_time(days=1)
            capture("daily_before_topdress")

        if self.management_path == MANAGEMENT_PATH_EXPERT_BASELINE:
            self._set_weather_for_date(plot.topdress_date)
            topdress_strength = (plot.topdress_n_kg_ha + plot.topdress_p_kg_ha + plot.topdress_k_kg_ha) / 30.0
            call("table_topdress_fertigation", farm_world.apply_fertigation, 0, 63, topdress_strength, 5.0)
            call("commit_topdress", farm_world.commit_daily_physics)
            capture("commit_topdress")
        else:
            apply_oracle_intervention(f"oracle_topdress_window_{oracle_date().isoformat()}")

        while oracle_date() < plot.harvest_date:
            apply_oracle_intervention(f"oracle_pre_{oracle_date().isoformat()}")
            system.advance_time(days=1)
            capture("daily_before_harvest")

        self._set_weather_for_date(plot.harvest_date)
        if fit_observed_harvest_state:
            self._fit_observed_harvest_state()
            capture("fit_observed_pre_harvest")
        else:
            capture("natural_pre_harvest")

        call("harvest_overview", farm_world.get_farm_overview)
        if tractor._fuel_tank_l < 40.0:
            call("refuel_harvest", tractor.refuel, 100.0)
        call("attach_harvester", tractor.attach_implement, "harvester")
        harvest_failed = False
        for start in range(0, 64, 4):
            self._set_weather_for_date(plot.harvest_date)
            result = call(f"harvest_{start}_{start + 3}", tractor.harvest, start, start + 3)
            if "error" in result:
                harvest_failed = True
                break
            if start % 8 == 4:
                call(f"unload_after_{start + 3}", tractor.unload_grain)
        if not harvest_failed and tractor._grain_bin_kg > 0.0:
            call("final_unload", tractor.unload_grain)
        if not harvest_failed:
            call("dry_grain", farm_world.dry_grain, 13.5)
            call("store_grain", farm_world.store_grain)
        capture("post_harvest")

        inventory = farm_world.get_inventory()
        simulated_kg = float(inventory.get("warehouse_grain_kg", 0.0)) + float(
            inventory.get("harvest_grain_kg", 0.0)
        )
        return {
            "plot_id": plot.plot_id,
            "variety": plot.variety,
            "ridge_id": ridge_id,
            "field_mode": FIELD_MODE,
            "weather_source": self.weather_source,
            "weather_seed": int(self.weather_seed),
            "calibration_profile": self.calibration_profile,
            "management_path": self.management_path,
            "mode": (
                f"{self.management_path}_fitted_observed_harvest_state"
                if fit_observed_harvest_state
                else self.management_path
            ),
            "simulated_field_area_m2": round(plot.simulated_field_area_m2, 2),
            "actual_yield_kg_ha": round(plot.actual_yield_kg_ha, 2),
            "simulated_field_yield_kg": round(simulated_kg, 2),
            "simulated_yield_kg_ha": round(
                simulated_kg / plot.simulated_field_area_m2 * 10000.0, 2
            ),
            "trace_rows": rows,
            "operations": operations,
        }

    def _advance_to_date(self, target: date, system: SystemApp) -> None:
        farm_world = self.get_typed_app(FarmWorldApp)
        current = datetime.fromtimestamp(
            farm_world.time_manager.time(), tz=timezone.utc
        ).date()
        delta_days = (target - current).days
        if delta_days > 0:
            system.advance_time(days=delta_days)

    def _fit_observed_harvest_state(self) -> None:
        from are.simulation.physics import SoybeanStage
        from are.simulation.physics.yield_recovery_engine import GrowthStage as YieldStage

        trial: Tangyan5Trial = self.trial  # type: ignore[attr-defined]
        plot = trial.plot
        farm_world = self.get_typed_app(FarmWorldApp)
        physics = farm_world.physics
        target_g_m2 = plot.actual_yield_kg_ha / 10.0 / 0.954
        days_since_planting = max(0, (plot.harvest_date - plot.planting_date).days)
        for ridge_id in range(farm_world.num_ridges):
            ridge = farm_world.get_ridge(ridge_id)
            ridge.planted = True
            ridge.seed_type = plot.seed_type
            ridge.seed_spacing_cm = plot.seed_spacing_cm
            ridge.planted_at_sim_time = None
            ridge.days_since_planted = days_since_planting
            ridge.growth_stage = GrowthStage.R8.value
            ridge.grain_moisture_pct = 14.0
            ridge.yield_potential = 1.0

            phen = physics.phenology.states[ridge_id]
            phen.planted = True
            phen.stage = SoybeanStage.R8
            phen.emerged = True
            phen.days_after_planting = days_since_planting
            phen.maturity_date = plot.maturity_date

            canopy = physics.canopy.states[ridge_id]
            canopy.yield_potential_g_m2 = target_g_m2
            canopy.aboveground_biomass_g_m2 = max(canopy.aboveground_biomass_g_m2, target_g_m2 * 2.2)

            yield_state = physics.yield_recovery.states[ridge_id]
            yield_state.r8_reached = True
            yield_state.maturity_date = plot.maturity_date
            yield_state.grain_moisture_frac = 0.14
            yield_state.biological_yield_g_m2 = target_g_m2
        farm_world.advance_physics_time()
        # Keep the compatibility shadow harvestable after the no-op/idempotent sync.
        for ridge_id in range(farm_world.num_ridges):
            ridge = farm_world.get_ridge(ridge_id)
            ridge.planted_at_sim_time = None
            ridge.growth_stage = GrowthStage.R8.value
            ridge.grain_moisture_pct = 14.0
        # Touch enum import so static checkers do not treat YieldStage as accidental.
        _ = YieldStage.R8

    def validate(self, env) -> ScenarioValidationResult:
        return ScenarioValidationResult(
            success=True,
            rationale="Tangyan5 base full-season scenario is intended for oracle calibration diagnostics.",
        )


def _weather_app_day_dict(weather_day: Tangyan5WeatherDay) -> dict[str, Any]:
    return {
        "date": weather_day.day.isoformat(),
        "temp_c": weather_day.temp_mean_c,
        "humidity_pct": weather_day.humidity_pct,
        "wind_speed_ms": weather_day.wind_ms,
        "rainfall_mm": weather_day.rain_mm,
        "solar_radiation": weather_day.solar_w_m2,
    }


def tangyan5_oracle_rows_for_csv(result: Any) -> list[dict[str, Any]]:
    """Flatten ``run_oracle_diagnostics`` return value for CSV export."""
    if (
        isinstance(result, dict)
        and set(result.keys()) == set(WEATHER_SOURCES)
        and all(isinstance(result[k], dict) for k in WEATHER_SOURCES)
    ):
        return [result[k] for k in WEATHER_SOURCES]
    return [result]


def write_tangyan5_oracle_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "plot_id",
        "variety",
        "weather_source",
        "weather_seed",
        "calibration_profile",
        "mode",
        "simulated_field_yield_kg",
        "simulated_yield_kg_ha",
        "actual_yield_kg_ha",
        "absolute_error_kg_ha",
        "simulated_field_area_m2",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def write_tangyan5_trace_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _first_trace_date(rows: list[dict[str, Any]], predicate) -> str | None:
    for row in rows:
        if predicate(row):
            return str(row.get("field_date"))
    return None


def tangyan5_trace_summary(result: dict[str, Any]) -> dict[str, Any]:
    rows = result.get("trace_rows", [])
    if not rows:
        return {
            "weather_source": result.get("weather_source"),
            "management_path": result.get("management_path"),
            "rows": 0,
        }
    final = rows[-1]
    field_area_m2 = float(result.get("simulated_field_area_m2") or 0.0)
    simulated_yield_kg_ha = float(result.get("simulated_yield_kg_ha") or 0.0)
    actual_yield_kg_ha = float(result.get("actual_yield_kg_ha") or 0.0)
    simulated_field_yield_kg = float(
        result.get("simulated_field_yield_kg")
        or (
            simulated_yield_kg_ha * field_area_m2 / 10000.0
            if field_area_m2 > 0.0
            else 0.0
        )
    )
    actual_field_yield_kg = (
        actual_yield_kg_ha * field_area_m2 / 10000.0
        if field_area_m2 > 0.0
        else 0.0
    )
    return {
        "weather_source": result.get("weather_source"),
        "weather_seed": result.get("weather_seed"),
        "calibration_profile": result.get("calibration_profile"),
        "management_path": result.get("management_path"),
        "rows": len(rows),
        "actual_yield_kg_ha": result.get("actual_yield_kg_ha"),
        "simulated_yield_kg_ha": result.get("simulated_yield_kg_ha"),
        "simulated_yield_kg_per_mu": round(simulated_yield_kg_ha / 15.0, 2),
        "actual_yield_kg_per_mu": round(actual_yield_kg_ha / 15.0, 2),
        "simulated_field_area_m2": round(field_area_m2, 2),
        "simulated_field_area_mu": round(field_area_m2 / M2_PER_MU, 3),
        "simulated_field_yield_kg": round(simulated_field_yield_kg, 2),
        "actual_field_yield_kg": round(actual_field_yield_kg, 2),
        "field_yield_gap_kg": round(simulated_field_yield_kg - actual_field_yield_kg, 2),
        "emergence_date": _first_trace_date(
            rows, lambda r: bool(r.get("phenology_emerged"))
        ),
        "r1_date": _first_trace_date(
            rows, lambda r: str(r.get("phenology_stage")) == "R1_BEGINNING_BLOOM"
        ),
        "r5_date": _first_trace_date(
            rows, lambda r: str(r.get("phenology_stage")) == "R5_BEGINNING_SEED"
        ),
        "r8_date": _first_trace_date(
            rows, lambda r: str(r.get("phenology_stage")) == "R8_FULL_MATURITY"
        ),
        "max_lai": max(float(r.get("canopy_lai") or 0.0) for r in rows),
        "max_daily_biomass_g_m2": max(
            float(r.get("canopy_daily_biomass_delta_g_m2") or 0.0) for r in rows
        ),
        "final_biomass_g_m2": final.get("canopy_aboveground_biomass_g_m2"),
        "final_yield_potential_g_m2": final.get("canopy_yield_potential_g_m2"),
        "final_recovered_kg_ha": final.get("yield_recovered_kg_ha"),
        "final_grain_moisture_frac": final.get("yield_grain_moisture_frac"),
        "min_root_vwc": min(float(r.get("soil_root_vwc") or 0.0) for r in rows),
        "min_nutrient_stress": min(
            float(r.get("management_nutrient_stress") or 0.0) for r in rows
        ),
        "final_canopy_stress_days": final.get("canopy_cumulative_stress_days"),
    }


def tangyan5_trace_summaries(result: Any) -> Any:
    if isinstance(result, dict) and result and all(
        isinstance(value, dict) and "trace_rows" in value
        for value in result.values()
    ):
        return {key: tangyan5_trace_summary(value) for key, value in result.items()}
    if isinstance(result, dict):
        return tangyan5_trace_summary(result)
    return {}


def run_oracle_diagnostics(
    workbook_path: str = DEFAULT_WORKBOOK_PATH,
    plot_id: str = DEFAULT_PLOT_ID,
    weather_source: str = WEATHER_SOURCE_ENGINE,
    weather_seed: int = 5,
    calibration_profile: str = CALIBRATION_PROFILE_NONE,
    fit_observed_harvest_state: bool = False,
) -> dict[str, Any]:
    scenario = ScenarioTangyan5BaseFullSeason(
        workbook_path=workbook_path,
        plot_id=plot_id,
        weather_source=weather_source,
        weather_seed=weather_seed,
        calibration_profile=calibration_profile,
    )
    scenario.initialize()
    return scenario.run_table_oracle(
        fit_observed_harvest_state=fit_observed_harvest_state
    )


def run_daily_trace(
    workbook_path: str = DEFAULT_WORKBOOK_PATH,
    plot_id: str = DEFAULT_PLOT_ID,
    weather_source: str = WEATHER_SOURCE_ENGINE,
    weather_seed: int = 5,
    calibration_profile: str = CALIBRATION_PROFILE_NONE,
    management_path: str = MANAGEMENT_PATH_EXPERT_BASELINE,
    ridge_id: int = 0,
    fit_observed_harvest_state: bool = False,
) -> dict[str, Any]:
    scenario = ScenarioTangyan5BaseFullSeason(
        workbook_path=workbook_path,
        plot_id=plot_id,
        weather_source=weather_source,
        weather_seed=weather_seed,
        calibration_profile=calibration_profile,
        management_path=management_path,
    )
    scenario.initialize()
    return scenario.trace_ridge_daily(
        ridge_id=ridge_id,
        fit_observed_harvest_state=fit_observed_harvest_state,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workbook",
        default=DEFAULT_WORKBOOK_PATH,
        help="Deprecated and ignored. Tangyan5 runtime uses embedded repo data.",
    )
    parser.add_argument("--plot-id", default=DEFAULT_PLOT_ID)
    parser.add_argument(
        "--weather-source",
        choices=(*WEATHER_SOURCES, "both"),
        default=WEATHER_SOURCE_ENGINE,
    )
    parser.add_argument("--weather-seed", type=int, default=5)
    parser.add_argument(
        "--calibration-profile",
        choices=CALIBRATION_PROFILES,
        default=CALIBRATION_PROFILE_NONE,
        help=(
            "Scenario-local crop/soil calibration. "
            f"{CALIBRATION_PROFILE_HEILONGJIANG_BLACK_SOIL_V1} uses a black-soil "
            "high-yield soybean profile; "
            f"{CALIBRATION_PROFILE_HEILONGJIANG_BLACK_SOIL_V2} uses a more "
            "conservative black-soil profile; default leaves engine defaults unchanged."
        ),
    )
    parser.add_argument(
        "--management-path",
        choices=(*MANAGEMENT_PATHS, MANAGEMENT_PATH_BOTH),
        default=MANAGEMENT_PATH_EXPERT_BASELINE,
        help=(
            "expert_baseline follows the Tangyan5 table operations; "
            "stress_free_oracle reads daily engine state and intervenes to keep "
            "water/nutrient/biotic pressure non-limiting."
        ),
    )
    parser.add_argument("--fit-observed-harvest-state", action="store_true")
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=None,
        help="Write oracle summary rows (one per weather source if --weather-source both).",
    )
    parser.add_argument(
        "--trace-ridge-id",
        type=int,
        default=None,
        help="Run the scenario day by day and record this ridge's engine state after each tick.",
    )
    parser.add_argument(
        "--trace-output-csv",
        type=Path,
        default=None,
        help="Write daily ridge trace rows to CSV.",
    )
    parser.add_argument(
        "--trace-output-json",
        type=Path,
        default=None,
        help="Write the full daily ridge trace result to JSON.",
    )
    args = parser.parse_args()
    if args.trace_ridge_id is not None:
        weather_sources = (
            WEATHER_SOURCES if args.weather_source == "both" else (args.weather_source,)
        )
        management_paths = (
            MANAGEMENT_PATHS
            if args.management_path == MANAGEMENT_PATH_BOTH
            else (args.management_path,)
        )
        if len(weather_sources) > 1 or len(management_paths) > 1:
            result = {
                f"{source}:{management_path}": run_daily_trace(
                    workbook_path=args.workbook,
                    plot_id=args.plot_id,
                    weather_source=source,
                    weather_seed=args.weather_seed,
                    calibration_profile=args.calibration_profile,
                    management_path=management_path,
                    ridge_id=args.trace_ridge_id,
                    fit_observed_harvest_state=args.fit_observed_harvest_state,
                )
                for source in weather_sources
                for management_path in management_paths
            }
        else:
            result = run_daily_trace(
                workbook_path=args.workbook,
                plot_id=args.plot_id,
                weather_source=args.weather_source,
                weather_seed=args.weather_seed,
                calibration_profile=args.calibration_profile,
                management_path=args.management_path,
                ridge_id=args.trace_ridge_id,
                fit_observed_harvest_state=args.fit_observed_harvest_state,
            )
        trace_rows: list[dict[str, Any]] = []
        if isinstance(result, dict) and result and all(
            isinstance(value, dict) and "trace_rows" in value
            for value in result.values()
        ):
            for value in result.values():
                trace_rows.extend(value.get("trace_rows", []))
        elif isinstance(result, dict):
            trace_rows = result.get("trace_rows", [])
        if args.trace_output_csv is not None:
            write_tangyan5_trace_csv(args.trace_output_csv, trace_rows)
        if args.trace_output_json is not None:
            args.trace_output_json.parent.mkdir(parents=True, exist_ok=True)
            args.trace_output_json.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        summary = {
            "trace_ridge_id": args.trace_ridge_id,
            "trace_rows": len(trace_rows),
            "trace_output_csv": str(args.trace_output_csv) if args.trace_output_csv else None,
            "trace_output_json": str(args.trace_output_json) if args.trace_output_json else None,
            "results": tangyan5_trace_summaries(result),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.weather_source == "both":
        result = {
            source: run_oracle_diagnostics(
                workbook_path=args.workbook,
                plot_id=args.plot_id,
                weather_source=source,
                weather_seed=args.weather_seed,
                calibration_profile=args.calibration_profile,
                fit_observed_harvest_state=args.fit_observed_harvest_state,
            )
            for source in WEATHER_SOURCES
        }
    else:
        result = run_oracle_diagnostics(
            workbook_path=args.workbook,
            plot_id=args.plot_id,
            weather_source=args.weather_source,
            weather_seed=args.weather_seed,
            calibration_profile=args.calibration_profile,
            fit_observed_harvest_state=args.fit_observed_harvest_state,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if args.output_csv is not None:
        write_tangyan5_oracle_csv(args.output_csv, tangyan5_oracle_rows_for_csv(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
