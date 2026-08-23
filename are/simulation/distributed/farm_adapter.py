"""Scenario-facing fact extraction for distributed FarmARE seasons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from are.simulation.apps.farm_world import FarmWorldApp, WeatherApp
from are.simulation.distributed.models import (
    EpistemicStatus,
    FactVersionRecord,
    stable_digest,
)
from are.simulation.scenarios.scenario import Scenario


@dataclass(frozen=True)
class ExtractedFact:
    key: str
    value: Any
    scope: tuple[int, int] | str | None
    valid_for: float | None
    status: EpistemicStatus = EpistemicStatus.OBSERVED


def scope_from_args(args: dict[str, Any]) -> tuple[int, int] | None:
    for start_key, end_key in (
        ("start_ridge", "end_ridge"),
        ("start", "end"),
        ("ridge_start", "ridge_end"),
    ):
        if start_key in args and end_key in args:
            return int(args[start_key]), int(args[end_key])
    if "ridge_id" in args:
        start = int(args["ridge_id"])
        return start, start + int(args.get("ridge_count", 1)) - 1
    return None


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


class FarmScenarioAdapter:
    """Maps native tool evidence and world truth into versioned farm facts."""

    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.farm_world = scenario.get_typed_app(FarmWorldApp)
        self.weather = scenario.get_typed_app(WeatherApp)

    def phase(self, action: str, world_time: float) -> str:
        state = self.farm_world.get_state()
        farm_phase = str(state.get("season_phase") or "").lower()
        planted_ridges = [
            item for item in state.get("ridges", []) if item.get("planted")
        ]
        ridge_stages = {
            str(item.get("growth_stage", "")).upper() for item in planted_ridges
        }
        if action.endswith(("dry_grain", "store_grain")):
            return "storage"
        if "harvest" in action or action.endswith("unload_grain"):
            return "harvest"
        if ridge_stages and ridge_stages <= {
            "R8",
            "R8_FULL_MATURITY",
            "MATURE",
            "HARVESTED",
        }:
            return "harvest"
        if farm_phase in {"maturity", "harvest", "r8", "r8_full_maturity"}:
            return "harvest"
        if farm_phase in {"r5", "r6", "reproductive"}:
            return "r5"
        if farm_phase in {"r1", "r2", "flowering"}:
            return "r1"
        if farm_phase in {"emergence", "vegetative"}:
            return "emergence"
        if "plant" in action:
            return "planting"
        if not planted_ridges:
            return "field_prep"
        # FarmWorld's coarse phase does not distinguish the two monitoring
        # checkpoints; date/action-derived traces may refine this later.
        return "midseason"

    def extract_observed(
        self,
        *,
        action: str,
        args: dict[str, Any],
        result: Any,
        phase: str,
    ) -> tuple[ExtractedFact, ...]:
        scope = scope_from_args(args) or (0, 63)
        facts: list[ExtractedFact] = [
            ExtractedFact(f"phase_evidence:{phase}", True, scope, 3 * 86400),
            ExtractedFact(f"tool_observation:{action}", result, scope, 3 * 86400),
        ]
        if action == "WeatherApp__get_current_weather" and isinstance(result, dict):
            sprayable = (
                float(result.get("rainfall_mm", 0.0)) == 0.0
                and float(result.get("wind_speed_ms", 999.0)) < 8.0
            )
            facts.extend(
                (
                    ExtractedFact(
                        "planting:weather_suitable",
                        float(result.get("rainfall_mm", 0.0)) < 5.0,
                        (0, 63),
                        86400,
                    ),
                    ExtractedFact(
                        "weather:spray_window_open", sprayable, (0, 63), 86400
                    ),
                    ExtractedFact(
                        "weather:harvest_window_open",
                        float(result.get("rainfall_mm", 0.0)) == 0.0,
                        (0, 63),
                        86400,
                    ),
                    ExtractedFact("weather:current", result, (0, 63), 86400),
                )
            )
        if action == "WeatherApp__get_forecast" and isinstance(result, dict):
            forecast = [
                item for item in result.get("forecast", []) if isinstance(item, dict)
            ]
            if forecast:
                sprayable = all(
                    float(item.get("rainfall_mm", 0.0)) == 0.0
                    and float(item.get("wind_speed_ms", 999.0)) < 8.0
                    for item in forecast
                )
                facts.append(
                    ExtractedFact(
                        "weather:spray_window_open",
                        sprayable,
                        (0, 63),
                        (max(1, len(forecast)) + 1) * 86400,
                    )
                )
        if action.startswith("SensorApp__read_soil"):
            readings = []
            if isinstance(result, dict):
                readings = result.get("soil_sensors", [result])
            values = [
                float(item["vwc"])
                for item in readings
                if isinstance(item, dict) and isinstance(item.get("vwc"), (int, float))
            ]
            if values:
                facts.extend(
                    (
                        ExtractedFact(
                            "soil:mean_vwc", sum(values) / len(values), scope, 2 * 86400
                        ),
                        ExtractedFact(
                            "soil:trafficable", max(values) < 0.40, scope, 2 * 86400
                        ),
                        ExtractedFact(
                            "planting:soil_suitable",
                            0.10 <= sum(values) / len(values) < 0.40,
                            scope,
                            2 * 86400,
                        ),
                    )
                )
        disease_values: list[bool] = []
        disease_pressures: list[float] = []
        affected_ridges: list[int] = []
        pest_values: list[bool] = []
        ridge_records: list[dict[str, Any]] = []
        for record in _walk(result):
            for key in ("disease_detected", "disease_present"):
                if key in record:
                    disease_values.append(bool(record[key]))
            if isinstance(record.get("disease_pressure"), (int, float)):
                pressure = float(record["disease_pressure"])
                disease_pressures.append(pressure)
                disease_values.append(pressure >= 0.2)
                if pressure >= 0.2 and isinstance(record.get("ridge_id"), int):
                    affected_ridges.append(int(record["ridge_id"]))
            elif any(
                bool(record.get(key)) for key in ("disease_detected", "disease_present")
            ):
                if isinstance(record.get("ridge_id"), int):
                    affected_ridges.append(int(record["ridge_id"]))
            for key in ("pest_detected", "pest_present"):
                if key in record:
                    pest_values.append(bool(record[key]))
            if isinstance(record.get("pest_pressure"), (int, float)):
                pest_values.append(float(record["pest_pressure"]) >= 0.2)
            if "ridge_id" in record:
                ridge_records.append(record)
        if disease_values:
            facts.extend(
                (
                    ExtractedFact(
                        "disease:confirmed",
                        any(disease_values),
                        scope,
                        3 * 86400,
                        EpistemicStatus.INFERRED,
                    ),
                    ExtractedFact(
                        "disease:severity",
                        max(disease_pressures, default=0.0),
                        scope,
                        3 * 86400,
                        EpistemicStatus.INFERRED,
                    ),
                    ExtractedFact(
                        "disease:affected_scope",
                        (
                            (min(affected_ridges), max(affected_ridges))
                            if affected_ridges
                            else None
                        ),
                        scope,
                        3 * 86400,
                        EpistemicStatus.INFERRED,
                    ),
                )
            )
        if pest_values:
            facts.append(
                ExtractedFact(
                    "pest:confirmed",
                    any(pest_values),
                    scope,
                    3 * 86400,
                    EpistemicStatus.INFERRED,
                )
            )
        if ridge_records:
            stages = [str(item.get("growth_stage", "")) for item in ridge_records]
            moisture = [
                float(item.get("grain_moisture", item.get("grain_moisture_pct")))
                for item in ridge_records
                if isinstance(
                    item.get("grain_moisture", item.get("grain_moisture_pct")),
                    (int, float),
                )
            ]
            if stages:
                facts.append(
                    ExtractedFact(
                        "crop:mature",
                        all(
                            bool(record.get("harvested"))
                            or stage.upper()
                            in {"R8", "R8_FULL_MATURITY", "MATURE", "HARVESTED"}
                            for stage, record in zip(stages, ridge_records, strict=True)
                        ),
                        scope,
                        3 * 86400,
                    )
                )
            if moisture:
                facts.append(
                    ExtractedFact(
                        "crop:grain_moisture",
                        sum(moisture) / len(moisture),
                        scope,
                        3 * 86400,
                    )
                )
        if action == "FarmWorldApp__get_inventory" and isinstance(result, dict):
            facts.append(ExtractedFact("resources:inventory", result, (0, 63), 86400))
            seeds = result.get("seed_stock") or {}
            market = result.get("postharvest_market") or {}
            storage_capacity = market.get("storage_capacity_kg")
            drying_capacity = market.get("drying_capacity_kg_per_day")
            facts.extend(
                (
                    ExtractedFact(
                        "inventory:seed_sufficient",
                        any(float(value or 0) > 0 for value in seeds.values()),
                        "seed",
                        86400,
                    ),
                    ExtractedFact(
                        "inventory:fertilizer_sufficient",
                        float(result.get("fertilizer_kg", 0.0) or 0.0) > 0,
                        "fertilizer",
                        86400,
                    ),
                    ExtractedFact(
                        "inventory:fuel_sufficient",
                        float(result.get("fuel_liters", 0.0) or 0.0) > 0,
                        "fuel",
                        86400,
                    ),
                    ExtractedFact(
                        "inventory:fungicide_sufficient",
                        float(result.get("pesticide_liters", 0.0) or 0.0) > 0,
                        "fungicide",
                        86400,
                    ),
                    ExtractedFact(
                        "equipment:dryer_ready",
                        drying_capacity is None or float(drying_capacity) > 0,
                        "dryer",
                        86400,
                    ),
                    ExtractedFact(
                        "storage:capacity_available",
                        storage_capacity is None
                        or float(result.get("warehouse_grain_kg", 0.0) or 0.0)
                        < float(storage_capacity),
                        "warehouse",
                        86400,
                    ),
                )
            )
        if action.endswith("__get_status") and isinstance(result, dict):
            facts.append(ExtractedFact(f"equipment:{action}:ready", True, scope, 86400))
            if action == "TractorApp__get_status":
                implements = set(result.get("available_implements") or ())
                facts.extend(
                    (
                        ExtractedFact(
                            "equipment:tractor_ready", True, "tractor", 86400
                        ),
                        ExtractedFact(
                            "equipment:planter_ready",
                            "error" not in result,
                            "planter",
                            86400,
                        ),
                        ExtractedFact(
                            "equipment:sprayer_ready",
                            bool({"sprayer", "fungicide_sprayer"} & implements),
                            "sprayer",
                            86400,
                        ),
                        ExtractedFact(
                            "equipment:harvester_ready",
                            "harvester" in implements,
                            "harvester",
                            86400,
                        ),
                    )
                )
            if action == "Mavic3M__check_status":
                facts.append(
                    ExtractedFact(
                        "equipment:drone_ready",
                        float(result.get("battery_pct", 0.0) or 0.0) >= 20.0,
                        "drone",
                        86400,
                    )
                )
            if action == "Robot0__check_status":
                facts.append(
                    ExtractedFact(
                        "equipment:robot_ready",
                        float(result.get("battery_pct", 0.0) or 0.0) >= 15.0,
                        "robot",
                        86400,
                    )
                )
        # Keep only one version per semantic key for a single observation.
        return tuple({fact.key: fact for fact in facts}.values())

    def authoritative_snapshot(
        self,
        *,
        source_event_id: str,
        farmare_event_id: str | None,
        action: str,
        args: dict[str, Any],
        world_time: float,
        phase: str,
    ) -> tuple[FactVersionRecord, ...]:
        scope = scope_from_args(args) or (0, 63)
        state = self.farm_world.get_state()
        ridges = state.get("ridges", [])
        selected = [
            ridge
            for ridge in ridges
            if not isinstance(scope, tuple)
            or scope[0] <= int(ridge.get("ridge_id", -1)) <= scope[1]
        ]
        truth: dict[str, Any] = {
            "weather:spray_window_open": bool(self.weather.is_sprayable),
            "weather:harvest_window_open": self.weather.rainfall_mm == 0.0,
            "soil:trafficable": bool(self.weather.is_trafficable),
        }
        inventory = state.get("inventory", {})
        seed_stock = inventory.get("seed_stock") or {}
        truth.update(
            {
                "inventory:seed_sufficient": any(
                    float(value or 0) > 0 for value in seed_stock.values()
                ),
                "inventory:fertilizer_sufficient": float(
                    inventory.get("fertilizer_kg", 0.0) or 0.0
                )
                > 0,
                "inventory:fuel_sufficient": float(
                    inventory.get("fuel_liters", 0.0) or 0.0
                )
                > 0,
                "inventory:fungicide_sufficient": float(
                    inventory.get("pesticide_liters", 0.0) or 0.0
                )
                > 0,
            }
        )
        if selected:
            disease_pressure = {
                int(ridge.get("ridge_id", -1)): float(
                    ridge.get("disease_pressure", 0.0) or 0.0
                )
                for ridge in selected
            }
            affected = [
                ridge_id
                for ridge_id, pressure in disease_pressure.items()
                if pressure >= 0.2
            ]
            truth["disease:confirmed"] = bool(affected)
            truth["disease:severity"] = max(disease_pressure.values(), default=0.0)
            truth["disease:affected_scope"] = (
                (min(affected), max(affected)) if affected else None
            )
            truth["pest:confirmed"] = any(
                float(ridge.get("pest_pressure", 0.0) or 0.0) >= 0.2
                for ridge in selected
            )
            truth["crop:mature"] = all(
                bool(ridge.get("harvested"))
                or str(ridge.get("growth_stage", "")).upper()
                in {"R8", "R8_FULL_MATURITY", "MATURE", "HARVESTED"}
                for ridge in selected
            )
            moisture_values = [
                float(ridge.get("grain_moisture_pct", 0.0) or 0.0)
                for ridge in selected
                if float(ridge.get("grain_moisture_pct", 0.0) or 0.0) > 0.0
            ]
            if moisture_values:
                truth["crop:grain_moisture"] = sum(moisture_values) / len(
                    moisture_values
                )
        records = []
        for key, value in truth.items():
            version_id = (
                f"world:{stable_digest((source_event_id, key, scope, world_time))[:20]}"
            )
            records.append(
                FactVersionRecord(
                    version_id=version_id,
                    fact_key=key,
                    value=value,
                    scope=scope,
                    source_event_id=source_event_id,
                    farmare_event_id=farmare_event_id,
                    world_time=world_time,
                    learned_time=None,
                    visible_to=(),
                    season_phase=phase,
                    authoritative=True,
                )
            )
        return tuple(records)
