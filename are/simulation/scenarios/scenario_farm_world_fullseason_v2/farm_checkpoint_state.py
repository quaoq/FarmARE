"""Checkpoint export/restore helpers for standalone FARM L2/L1 splits.

The trace CSVs are good human evidence, but they are not enough to resume a
small L2 world: weather RNG/cache, engine state, hidden profile modifiers, and
postharvest buffers can all affect what happens after the checkpoint.  These
helpers keep that state together in a JSON-safe payload.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date
from enum import Enum
from typing import Any, Mapping

from are.simulation.apps.farm_world.farm_action_record import FarmActionRecord
from are.simulation.apps.farm_world.farm_world_app import FarmWorldApp
from are.simulation.apps.farm_world.weather_app import WeatherApp
from are.simulation.physics.biotic_pressure_engine import BioticPressureState
from are.simulation.physics.canopy_biomass_engine import (
    CanopyBiomassState,
    SeedType as CanopySeedType,
)
from are.simulation.physics.management_effect_engine import ManagementEffectState
from are.simulation.physics.phenology_engine import (
    PhenologyState,
    SeedType as PhenologySeedType,
    SoybeanStage,
)
from are.simulation.physics.soil_engine import RidgeSoilState, SoilHydraulicModifier
from are.simulation.physics.weather_engine import WeatherDay
from are.simulation.physics.yield_recovery_engine import YieldRecoveryState


def export_farm_checkpoint_state(
    *,
    farm_world: FarmWorldApp,
    weather_app: WeatherApp | None,
    scenario_id: str,
    checkpoint_label: str,
) -> dict[str, Any]:
    """Return a JSON-safe checkpoint for a physics-backed FarmWorldApp."""

    physics = getattr(farm_world, "_physics", None)
    current_time = float(farm_world.time_manager.time())
    payload: dict[str, Any] = {
        "schema_version": 1,
        "scenario_id": scenario_id,
        "checkpoint_label": checkpoint_label,
        "sim_time": current_time,
        "farm_world_state": _to_jsonable(farm_world.get_state()),
        "weather_app_state": _to_jsonable(weather_app.get_state())
        if weather_app is not None
        else None,
        "physics_state": None,
    }
    if physics is None:
        return payload

    weather_generator = getattr(physics, "weather_generator", None)
    payload["physics_state"] = {
        "profile_name": physics.profile_name,
        "location": physics.location,
        "scenario_type": physics.scenario_type,
        "latitude_deg": physics.latitude_deg,
        "random_seed": physics.random_seed,
        "engines_active": physics.engines_active,
        "last_physics_sim_time": physics.last_physics_sim_time,
        "scenario_metadata": _to_jsonable(getattr(physics, "scenario_metadata", {})),
        "soil_states": _to_jsonable(physics.soil.get_state()),
        "soil_hydraulic_modifiers": _to_jsonable(
            getattr(physics.soil, "hydraulic_modifiers", {})
        ),
        "phenology_states": _to_jsonable(physics.phenology.get_state()),
        "canopy_states": _to_jsonable(physics.canopy.get_state()),
        "biotic_states": _to_jsonable(physics.biotic.get_state()),
        "management_states": _to_jsonable(physics.management.get_state()),
        "yield_recovery_states": _to_jsonable(physics.yield_recovery.get_state()),
        "action_log": _to_jsonable(physics.action_log),
        "weather_generator": _export_weather_generator(weather_generator),
    }
    return payload


def restore_farm_checkpoint_state(
    *,
    farm_world: FarmWorldApp,
    weather_app: WeatherApp | None,
    checkpoint_state: Mapping[str, Any],
    target_sim_time: float | None = None,
) -> dict[str, Any]:
    """Restore a checkpoint into a FarmWorldApp/WeatherApp pair."""

    physics_state = checkpoint_state.get("physics_state") or {}
    profile_name = physics_state.get("profile_name")
    if profile_name:
        farm_world.configure_physics_profile(
            profile_name=str(profile_name),
            location=physics_state.get("location"),
            scenario_type=physics_state.get("scenario_type"),
            latitude_deg=physics_state.get("latitude_deg"),
            random_seed=physics_state.get("random_seed"),
            **dict(physics_state.get("scenario_metadata") or {}),
        )
    farm_world.load_state(dict(checkpoint_state["farm_world_state"]))
    if weather_app is not None and checkpoint_state.get("weather_app_state"):
        weather_app.load_state(dict(checkpoint_state["weather_app_state"]))

    physics = farm_world.physics
    if physics_state:
        physics.profile_name = physics_state.get("profile_name")
        physics.location = physics_state.get("location")
        physics.scenario_type = physics_state.get("scenario_type")
        physics.latitude_deg = float(physics_state.get("latitude_deg", 45.7))
        physics.random_seed = int(physics_state.get("random_seed", 0))
        physics.engines_active = bool(physics_state.get("engines_active", True))
        if physics_state.get("scenario_metadata"):
            physics.scenario_metadata = dict(physics_state["scenario_metadata"])  # type: ignore[attr-defined]

        physics.soil.set_state(
            _state_map(physics_state.get("soil_states", {}), RidgeSoilState)
        )
        physics.soil.hydraulic_modifiers = _state_map(
            physics_state.get("soil_hydraulic_modifiers", {}),
            SoilHydraulicModifier,
        )
        physics.phenology.states = _state_map(
            physics_state.get("phenology_states", {}),
            PhenologyState,
            date_fields=("planting_date", "emergence_date", "maturity_date"),
            enum_fields={"seed_type": PhenologySeedType, "stage": SoybeanStage},
        )
        physics.canopy.states = _state_map(
            physics_state.get("canopy_states", {}),
            CanopyBiomassState,
            enum_fields={"seed_type": CanopySeedType},
        )
        physics.biotic.states = _state_map(
            physics_state.get("biotic_states", {}),
            BioticPressureState,
        )
        physics.management.states = _state_map(
            physics_state.get("management_states", {}),
            ManagementEffectState,
            date_fields=("planting_date", "last_nutrient_decay_day"),
        )
        physics.yield_recovery.states = _state_map(
            physics_state.get("yield_recovery_states", {}),
            YieldRecoveryState,
            date_fields=("maturity_date", "harvest_date"),
        )
        physics.action_log = [
            FarmActionRecord(**dict(record))
            for record in physics_state.get("action_log", [])
        ]
        _restore_weather_generator(
            getattr(physics, "weather_generator", None),
            physics_state.get("weather_generator") or {},
        )
        physics.last_physics_sim_time = (
            float(target_sim_time)
            if target_sim_time is not None
            else _float_or_none(physics_state.get("last_physics_sim_time"))
        )

    from are.simulation.apps.farm_world.physics_orchestrator import (
        sync_compatibility_fields_from_physics,
    )

    sync_compatibility_fields_from_physics(farm_world)
    if weather_app is not None:
        weather_app.set_avg_soil_vwc(farm_world.get_avg_vwc())
    return {
        "status": "ok",
        "scenario_id": checkpoint_state.get("scenario_id"),
        "checkpoint_label": checkpoint_state.get("checkpoint_label"),
        "sim_time": farm_world.time_manager.time(),
        "profile_name": getattr(farm_world.physics, "profile_name", None),
    }


def _export_weather_generator(generator: Any) -> dict[str, Any] | None:
    if generator is None:
        return None
    cache = getattr(generator, "_day_cache", {}) or {}
    return {
        "rng_state": _to_jsonable(generator.rng.bit_generator.state),
        "day_cache": {
            key.isoformat(): _to_jsonable(value) for key, value in cache.items()
        },
    }


def _restore_weather_generator(generator: Any, state: Mapping[str, Any]) -> None:
    if generator is None or not state:
        return
    if state.get("rng_state"):
        generator.rng.bit_generator.state = dict(state["rng_state"])
    generator._day_cache = {  # type: ignore[attr-defined]
        date.fromisoformat(str(day)): _weather_day_from_dict(dict(value))
        for day, value in (state.get("day_cache") or {}).items()
    }


def _weather_day_from_dict(data: dict[str, Any]) -> WeatherDay:
    data = _dataclass_init_data(WeatherDay, data)
    data["day"] = _date_or_none(data.get("day"))
    return WeatherDay(**data)


def _state_map(
    data: Mapping[str, Any] | Mapping[int, Any],
    cls: type[Any],
    *,
    date_fields: tuple[str, ...] = (),
    enum_fields: Mapping[str, type[Enum]] | None = None,
) -> dict[int, Any]:
    restored: dict[int, Any] = {}
    enum_fields = enum_fields or {}
    for key, raw_item in data.items():
        item = _dataclass_init_data(cls, dict(raw_item))
        for field_name in date_fields:
            item[field_name] = _date_or_none(item.get(field_name))
        for field_name, enum_cls in enum_fields.items():
            if item.get(field_name) is not None:
                item[field_name] = enum_cls(item[field_name])
        restored[int(key)] = cls(**item)
    return restored


def _dataclass_init_data(cls: type[Any], data: dict[str, Any]) -> dict[str, Any]:
    allowed = {field.name for field in fields(cls)}
    return {key: value for key, value in data.items() if key in allowed}


def _date_or_none(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _to_jsonable(val) for key, val in vars(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(_to_jsonable(key)): _to_jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        try:
            return value.item()
        except ValueError:
            pass
    return value
