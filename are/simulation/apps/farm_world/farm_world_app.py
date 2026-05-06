"""
FarmWorldApp — central ridge-state store.

All other apps write ridge data via @env_tool methods.
The agent reads via @app_tool methods.

Farm layout: 268 m × 71 m, 64 ridges (ID 0-63), ridge width 1.1 m. [PDF-p1]
"""
from __future__ import annotations

import logging
from typing import Any

from are.simulation.apps.app import App
from are.simulation.apps.farm_world.models import (
    GrowthStage,
    InventoryState,
    RidgeState,
    SeasonPhase,
)
from are.simulation.tool_utils import OperationType, app_tool, data_tool, env_tool
from are.simulation.types import EventType, event_registered
from are.simulation.utils.type_utils import type_check

logger = logging.getLogger(__name__)

# Number of ridges in the farm [PDF-p1]
DEFAULT_NUM_RIDGES = 64

# Farm physical dimensions [PDF-p1]
FIELD_LENGTH_M = 268.0   # ridge length (y-axis)
FIELD_WIDTH_M  = 71.0    # total field width (x-axis)
DEFAULT_RIDGE_WIDTH_M  = 1.1     # single ridge width
ROWS_PER_RIDGE = 2       # two soybean rows per ridge [PDF-p6]
ROW_SPACING_M  = 0.4     # spacing between the two planted rows within a ridge [PDF-p1]

# Grain yield per ridge (kg) — midpoint of 200-290 kg range [PDF-p11]
GRAIN_KG_PER_RIDGE = 245.0

# Pesticide consumed per ridge per spray pass (L) [设计, based on 300-800L / 5-10 ridges]
PESTICIDE_L_PER_RIDGE = 8.0

# --------------------------------------------------------------------------
# Post-spray pest/disease pressure trajectory [设计, based on PDF-p9 "药效
# 并非即时显现，而是随病虫害种群数量减少而逐步显现"]
#
#   Phase 1 (0 - 2d): continued worsening — residual pests still feeding,
#                     eggs hatching; pressure rises linearly to base + 0.10.
#   Phase 2 (2 - 4d): plateau — kill-rate ≈ birth-rate, pressure held at
#                     base + 0.10.
#   Phase 3 (>= 4d):  recovery — net population decay at 0.18 / day down to 0.
# --------------------------------------------------------------------------
_SPRAY_WORSEN_DAYS  = 2.0
_SPRAY_PLATEAU_DAYS = 4.0
_SPRAY_WORSEN_RATE  = 0.05   # per day, during worsening phase
_SPRAY_PLATEAU_BUMP = 0.10   # height of the plateau above base
_SPRAY_DECAY_RATE   = 0.18   # per day, during recovery phase
_SECONDS_PER_DAY    = 86400.0


def _effective_pressure(base: float, last_spray_t: float | None, now_t: float) -> float:
    """Three-phase post-spray trajectory (worsen → plateau → recover)."""
    if last_spray_t is None:
        return max(0.0, min(1.0, base))
    days = max(0.0, (now_t - last_spray_t) / _SECONDS_PER_DAY)
    if days < _SPRAY_WORSEN_DAYS:
        value = base + _SPRAY_WORSEN_RATE * days
    elif days < _SPRAY_PLATEAU_DAYS:
        value = base + _SPRAY_PLATEAU_BUMP
    else:
        value = base + _SPRAY_PLATEAU_BUMP - _SPRAY_DECAY_RATE * (days - _SPRAY_PLATEAU_DAYS)
    return max(0.0, min(1.0, value))

# Growth stage progression: days_since_planted thresholds [设计, based on PDF-p6 stage descriptions]
# Maps (seed_type, days) → growth_stage
_STAGE_THRESHOLDS: list[tuple[int, str]] = [
    (0,   GrowthStage.VE.value),
    (10,  GrowthStage.V1.value),
    (20,  GrowthStage.V2.value),
    (30,  GrowthStage.V3.value),
    (40,  GrowthStage.V4.value),
    (50,  GrowthStage.V5.value),
    (60,  GrowthStage.V6.value),
    (70,  GrowthStage.R1.value),
    (80,  GrowthStage.R2.value),
    (90,  GrowthStage.R3.value),
    (100, GrowthStage.R4.value),
    (105, GrowthStage.R5.value),
    (110, GrowthStage.R6.value),
    (115, GrowthStage.R7.value),
    (120, GrowthStage.R8.value),
]


def _stage_for_days(days: int) -> str:
    """Return the growth stage string for a given days_since_planted value."""
    stage = GrowthStage.VE.value
    for threshold, s in _STAGE_THRESHOLDS:
        if days >= threshold:
            stage = s
    return stage


def _grain_moisture_for_stage(stage: str, days_in_r6_plus: int) -> float:
    """
    Estimate grain moisture % based on growth stage.
    Grain moisture starts ~35% at R6 and drops ~1.5%/day toward 13-15%. [PDF-p10]
    """
    pre_r6 = {
        GrowthStage.BARE.value, GrowthStage.VE.value,
        GrowthStage.V1.value, GrowthStage.V2.value, GrowthStage.V3.value,
        GrowthStage.V4.value, GrowthStage.V5.value, GrowthStage.V6.value,
        GrowthStage.R1.value, GrowthStage.R2.value, GrowthStage.R3.value,
        GrowthStage.R4.value, GrowthStage.R5.value,
    }
    if stage in pre_r6:
        return 0.0
    # R6 onward: start at 35%, drop ~1.5%/day, floor at 13%
    moisture = max(13.0, 35.0 - days_in_r6_plus * 1.5)
    return round(moisture, 2)


def plants_per_ridge_from_spacing(seed_spacing_cm: float) -> int:
    """
    Convert in-row seed spacing to the realized plant count for one ridge.

    Each ridge contains two soybean rows across the fixed 268 m ridge length. [PDF-p6]
    """
    return int(round(FIELD_LENGTH_M * ROWS_PER_RIDGE * 100.0 / float(seed_spacing_cm)))


class FarmWorldApp(App):
    """
    Central ridge-state store for the Farm-World simulation.

    Maintains the state of all 64 ridges and the farm inventory.
    Other device apps write ridge data via env_tool calls.
    The agent reads via app_tool calls.
    """

    def __init__(self) -> None:
        super().__init__(name="FarmWorldApp")
        self.ridge_width_m = DEFAULT_RIDGE_WIDTH_M
        self.num_ridges = DEFAULT_NUM_RIDGES
        self._ridges: list[RidgeState] = [RidgeState.default(i) for i in range(self.num_ridges)]
        self._inventory: InventoryState = InventoryState.default()
        self._sim_date: str = "2026-04-25"
        self._season_phase: str = SeasonPhase.PREP.value
        self._pending_irrigation: dict[int, list[dict[str, float]]] = {}
        # Physics state is created lazily on first physics-aware activity.
        # Until then, FarmPhysicsState.engines_active stays False and the
        # legacy in-tool helpers (_effective_pressure, _stage_for_days,
        # _grain_moisture_for_stage, _refresh_ridge_dynamics) remain in charge.
        self._physics: "FarmPhysicsState | None" = None
        # Forwarded by FieldOpsApp/TractorApp/DroneApp/RobotApp __init__
        # so the orchestrator can read daily weather without env discovery.
        self._weather_app: "WeatherApp | None" = None

    # ------------------------------------------------------------------
    # App interface
    # ------------------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        self._apply_due_irrigation()
        return {
            "app_name": self.name,
            "sim_date": self._sim_date,
            "season_phase": self._season_phase,
            "ridges": [r.to_dict() for r in self._ridges],
            "inventory": self._inventory.to_dict(),
            "pending_irrigation": {
                str(ridge_id): [dict(item) for item in entries]
                for ridge_id, entries in self._pending_irrigation.items()
            },
        }

    def load_state(self, state_dict: dict[str, Any]) -> None:
        self._sim_date = state_dict["sim_date"]
        self._season_phase = state_dict["season_phase"]
        self._ridges = [RidgeState.from_dict(d) for d in state_dict["ridges"]]
        self._inventory = InventoryState.from_dict(state_dict["inventory"])
        pending_irrigation = state_dict.get("pending_irrigation", {})
        self._pending_irrigation = {
            int(ridge_id): [dict(item) for item in entries]
            for ridge_id, entries in pending_irrigation.items()
        }

    def reset(self) -> None:
        super().reset()
        self._ridges = [RidgeState.default(i) for i in range(self.num_ridges)]
        self._inventory = InventoryState.default()
        self._sim_date = "2026-04-25"
        self._season_phase = SeasonPhase.PREP.value
        self._pending_irrigation = {}

    # ------------------------------------------------------------------
    # Agent tools (@app_tool) — read-only
    # ------------------------------------------------------------------

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_farm_overview(self) -> dict[str, Any]:
        """
        Return a concise farm summary: current date, season phase, inventory,
        and a per-ridge overview (key fields only, not full detail).

        season_phase indicates the current stage of the agricultural calendar:
          "prep"     — pre-planting field preparation
          "planting" — active sowing window
          "growing"  — crop vegetative and reproductive growth
          "harvest"  — grain maturity and harvesting

        Use get_ridge_state() or get_ridge_range_state() for full ridge detail.
        """
        self.advance_physics_time()
        overview = []
        for r in self._ridges:
            overview.append({
                "ridge_id": r.ridge_id,
                "planted": r.planted,
                "growth_stage": r.growth_stage,
                "grain_moisture_pct": round(r.grain_moisture_pct, 1),
            })
        return {
            "sim_date": self._sim_date,
            "season_phase": self._season_phase,
            "inventory": self._inventory.to_dict(),
            "ridges_overview": overview,
        }

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_ridge_state(self, ridge_id: int) -> dict[str, Any]:
        """
        Return the full state of a single ridge.

        Args:
            ridge_id: Ridge identifier, 0-63.
        """
        if not 0 <= ridge_id < self.num_ridges:
            return {"error": f"ridge_id must be 0-{self.num_ridges - 1}, got {ridge_id}"}
        self.advance_physics_time()
        return self._agent_ridge_dict(self._ridges[ridge_id])

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_ridge_range_state(self, start: int, end: int) -> dict[str, Any]:
        """
        Return the full state of ridges from start to end (inclusive).

        Args:
            start: First ridge ID
            end:   Last ridge ID , must be >= start.

        Use this instead of repeated get_ridge_state() calls when you need
        to inspect a contiguous block of ridges at once.
        """
        if not 0 <= start <= end < self.num_ridges:
            return {"error": f"Invalid range [{start}, {end}]. Must be within 0-{self.num_ridges - 1}."}
        self.advance_physics_time()
        return {
            "ridges": [self._agent_ridge_dict(self._ridges[i]) for i in range(start, end + 1)]
        }

    @type_check
    @app_tool()
    @data_tool()
    @event_registered(operation_type=OperationType.READ)
    def get_inventory(self) -> dict[str, Any]:
        """Return the current farm inventory (seeds, pesticide, fertilizer, fuel, grain)."""
        return self._inventory.to_dict()

    # ------------------------------------------------------------------
    # Environment tools (@env_tool) — called by scenario events / device apps
    # ------------------------------------------------------------------



    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def trigger_pest_outbreak(
        self, ridge_ids: list[int], severity: float
    ) -> dict[str, Any]:
        """
        Trigger a localised pest outbreak on the specified ridges. [PDF-p7]

        Args:
            ridge_ids: List of ridge IDs affected.
            severity:  Pest pressure level to set (0.0-1.0).
        """
        for rid in ridge_ids:
            if 0 <= rid < self.num_ridges:
                r = self._ridges[rid]
                r.pest_pressure_base = max(r.pest_pressure_base, float(severity))
                r.last_spray_sim_time = None
                self._refresh_ridge_dynamics(r)
        self.is_state_modified = True
        return {"status": "ok", "affected_ridges": ridge_ids}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def trigger_rainfall(self, mm: float) -> dict[str, Any]:
        """
        Apply an immediate rainfall event, updating soil VWC for all ridges. [PDF-p6]

        Args:
            mm: Rainfall amount in millimetres.
        """
        for r in self._ridges:
            r.soil_vwc = min(0.45, r.soil_vwc + mm / 100.0)
        self.is_state_modified = True
        return {"status": "ok", "rainfall_mm": mm}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def set_ridge_planted(
        self,
        ridge_id: int,
        seed_type: str,
        seed_spacing_cm: float | None = None,
        seeds_planted: int | None = None,
    ) -> dict[str, Any]:
        """
        Mark a ridge as planted. Called by TractorApp after plant_seeds completes.

        Args:
            ridge_id:         Ridge ID (0-63).
            seed_type:        SeedType value string.
            seed_spacing_cm:  In-row spacing in centimetres.
            seeds_planted:    Realized plant count for the ridge.
        """
        if not 0 <= ridge_id < self.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        r = self._ridges[ridge_id]
        r.planted = True
        r.seed_type = seed_type
        r.seed_spacing_cm = seed_spacing_cm
        r.seeds_planted = seeds_planted or 0
        r.planted_at_sim_time = float(self.time_manager.time())
        r.days_since_planted = 0
        r.growth_stage = GrowthStage.VE.value
        r.grain_moisture_pct = 0.0
        self.is_state_modified = True
        return {
            "status": "ok",
            "ridge_id": ridge_id,
            "seed_type": seed_type,
            "seed_spacing_cm": seed_spacing_cm,
            "seeds_planted": r.seeds_planted,
        }

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def set_ridge_harvested(self, ridge_id: int) -> dict[str, Any]:
        """
        Mark a ridge as harvested. Called by TractorApp after harvest completes.
        Grain goes into the combine grain bin (tractor-side); it is only moved
        into warehouse inventory when `tractor.unload_grain()` is called. [PDF-p11]

        Args:
            ridge_id: Ridge ID (0-63).
        """
        if not 0 <= ridge_id < self.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        r = self._ridges[ridge_id]
        # Yield scales with yield_potential [设计]
        grain = round(GRAIN_KG_PER_RIDGE * r.yield_potential, 2)
        r.planted = False
        r.seed_type = None
        r.seed_spacing_cm = None
        r.seeds_planted = 0
        r.growth_stage = GrowthStage.BARE.value
        r.days_since_planted = 0
        r.grain_moisture_pct = 0.0
        r.ndvi = -1.0
        r.canopy_temp_c = -1.0
        r.planted_at_sim_time = None
        self.is_state_modified = True
        return {"status": "ok", "ridge_id": ridge_id, "grain_kg_added": grain}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def update_ridge_ndvi(self, ridge_id: int, ndvi: float) -> dict[str, Any]:
        """
        Update NDVI observation for a ridge. Called by DroneApp after survey. [PDF-p7]

        Args:
            ridge_id: Ridge ID (0-63).
            ndvi:     NDVI value (0.0-1.0).
        """
        if not 0 <= ridge_id < self.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        self._ridges[ridge_id].ndvi = round(float(ndvi), 4)
        self.is_state_modified = True
        return {"status": "ok"}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def update_ridge_canopy_temp(self, ridge_id: int, temp_c: float) -> dict[str, Any]:
        """
        Update canopy temperature observation for a ridge.
        Called by DroneApp after thermal inspection. [PDF-p7]

        Args:
            ridge_id: Ridge ID (0-63).
            temp_c:   Canopy temperature in °C.
        """
        if not 0 <= ridge_id < self.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        self._ridges[ridge_id].canopy_temp_c = round(float(temp_c), 2)
        self.is_state_modified = True
        return {"status": "ok"}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def update_ridge_pesticide(self, ridge_id: int) -> dict[str, Any]:
        """
        Record that pesticide was applied to a ridge. Called by TractorApp /
        FieldOpsApp after a spray completes. Pest/disease pressure then follows
        a worsen → plateau → recover trajectory driven by simulation time. [PDF-p9]

        Args:
            ridge_id: Ridge ID (0-63).
        """
        if not 0 <= ridge_id < self.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        r = self._ridges[ridge_id]
        now_t = float(self.time_manager.time())
        # Carry current observed pressure forward as the new baseline so the
        # post-spray curve starts from where the agent last saw it (no jump).
        r.pest_pressure_base    = _effective_pressure(
            r.pest_pressure_base, r.last_spray_sim_time, now_t
        )
        r.disease_pressure_base = _effective_pressure(
            r.disease_pressure_base, r.last_spray_sim_time, now_t
        )
        r.last_spray_sim_time = now_t
        self._refresh_ridge_dynamics(r)
        self.is_state_modified = True
        return {"status": "ok"}


    def set_irrigation_pending(
        self, ridge_id: int, add_vwc: float, effect_ready_at: float | None = None
    ) -> dict[str, Any]:
        """
        Queue or apply irrigation effect for a ridge (called by FieldOpsApp).

        Args:
            ridge_id: Ridge ID (0-63).
            add_vwc:  Volumetric water content increment from the irrigation duration.
            effect_ready_at:
                Absolute simulation timestamp when the effect should become visible.
                If omitted or already due, the moisture change is applied immediately.
        """
        if not 0 <= ridge_id < self.num_ridges:
            return {"error": f"Invalid ridge_id {ridge_id}"}
        now_t = float(self.time_manager.time())
        if effect_ready_at is not None and effect_ready_at > now_t:
            pending = self._pending_irrigation.setdefault(ridge_id, [])
            pending.append(
                {
                    "add_vwc": float(add_vwc),
                    "effect_ready_at": float(effect_ready_at),
                }
            )
            pending.sort(key=lambda item: item["effect_ready_at"])
            self.is_state_modified = True
            return {
                "status": "pending",
                "ridge_id": ridge_id,
                "effect_ready_at": float(effect_ready_at),
            }

        self._apply_irrigation_effect(ridge_id, float(add_vwc))
        self.is_state_modified = True
        return {"status": "ok"}

    @type_check
    @env_tool()
    @event_registered(
        operation_type=OperationType.WRITE, event_type=EventType.ENV
    )
    def set_season_phase(self, phase: str) -> dict[str, Any]:
        """
        Update the high-level season phase label. [设计]

        Args:
            phase: SeasonPhase value string ("prep"/"planting"/"growing"/"harvest").
        """
        self._season_phase = phase
        self.is_state_modified = True
        return {"status": "ok", "season_phase": phase}

    # ------------------------------------------------------------------
    # Physics integration (FarmPhysicsState + orchestrator)
    # ------------------------------------------------------------------

    def attach_weather_app(self, weather_app: "WeatherApp") -> None:
        """Forward a WeatherApp reference for the physics orchestrator.

        Idempotent. The first call wins; later calls update the reference if
        a different WeatherApp is provided. Called by other apps' __init__.
        """
        self._weather_app = weather_app

    def attach_system_app(self, system_app) -> None:
        """Bind a SystemApp <-> FarmWorldApp reference so SystemApp.advance_time
        can fire the physics orchestrator after a time jump. Called by scenarios
        in init_and_populate_apps after both apps are constructed.
        """
        try:
            system_app.attach_farm_world_app(self)
        except AttributeError:
            pass  # Older SystemApp without the hook — silent no-op.

    @property
    def physics(self) -> "FarmPhysicsState":
        """Lazily-constructed physics state container.

        First access activates the physics path: from now on, tools that
        record actions or queue management events will route through engine
        updates instead of mutating ridge fields directly.
        """
        if self._physics is None:
            from are.simulation.apps.farm_world.farm_physics_state import FarmPhysicsState
            self._physics = FarmPhysicsState(num_ridges=self.num_ridges)
        return self._physics

    @property
    def physics_active(self) -> bool:
        """True iff physics has been activated by a profile or queued action.

        Used by legacy helpers (_effective_pressure, _refresh_ridge_dynamics)
        to gate themselves out when physics is in charge. When False, the
        old behaviour is preserved verbatim for backward compatibility.
        """
        return self._physics is not None and self._physics.engines_active

    def configure_physics_profile(
        self,
        profile_name: str,
        location: str | None = None,
        scenario_type: str | None = None,
        latitude_deg: float | None = None,
        random_seed: int | None = None,
        **scenario_metadata: Any,
    ) -> dict[str, Any]:
        """Activate physics with scenario-specific configuration.

        Called from scenario `_configure_physics_layers` methods. Idempotent:
        repeat calls update the labels but do not reset engine state. Extra
        keyword arguments (e.g. ``seed_type``) are stored as scenario
        metadata for later inspection without forcing the signature to
        evolve every time a mirror scenario adds a new hint.
        """
        physics = self.physics
        physics.profile_name = profile_name
        if location is not None:
            physics.location = location
        if scenario_type is not None:
            physics.scenario_type = scenario_type
        if latitude_deg is not None:
            physics.latitude_deg = float(latitude_deg)
        if random_seed is not None:
            physics.random_seed = int(random_seed)
        if scenario_metadata:
            # Merge into a freeform metadata bag (created lazily on first use).
            existing = getattr(physics, "scenario_metadata", None)
            if existing is None:
                physics.scenario_metadata = {}  # type: ignore[attr-defined]
                existing = physics.scenario_metadata  # type: ignore[attr-defined]
            existing.update(scenario_metadata)
        # If a registered round-4 PhysicsProfile matches this name, wire its
        # WeatherGenerator + biotic-outbreak schedule into the physics state.
        # Round-3 episodes pass freeform names that don't match the registry —
        # those proceed with the static weather.set_weather pattern.
        from are.simulation.physics.profiles import get_profile

        registered = get_profile(profile_name)
        if registered is not None:
            from are.simulation.physics import WeatherGenerator

            physics.weather_generator = WeatherGenerator(
                config=registered.to_weather_generator_config(),
                seed=registered.rng_seed,
            )
            physics.latitude_deg = registered.latitude_deg
            physics.random_seed = registered.rng_seed
            # Stash profile metadata so the orchestrator can apply weather
            # events and biotic outbreaks during the daily tick.
            physics.profile = registered  # type: ignore[attr-defined]
        physics.engines_active = True
        return {
            "status": "ok",
            "profile_name": profile_name,
            "location": physics.location,
            "scenario_type": physics.scenario_type,
            "profile_registered": registered is not None,
        }

    def record_action(self, action: "FarmActionRecord") -> None:
        """Append a structured FarmActionRecord to the physics audit log."""
        self.physics.record_action(action)
        self.is_state_modified = True

    @type_check
    @app_tool()
    @event_registered(
        operation_type=OperationType.WRITE
    )
    def commit_daily_physics(self) -> dict[str, Any]:
        """
        Force a full daily physics tick at the current simulation time.

        Use this when you've taken several actions and want to advance the
        world by exactly one day so their delayed effects (canopy growth,
        biotic pressure decay, soil dry-down) become visible. Most agents
        should prefer ``SystemApp.advance_time(days=N)`` to advance multiple
        days; this is the single-day version with explicit "commit" semantics.
        """
        if not self.physics_active:
            return {"status": "noop", "reason": "physics_inactive"}
        # Advance the clock by one day, then run the orchestrator.
        self.time_manager.add_offset(86400)
        result = self.advance_physics_time()
        return {"status": "ok", "tick_result": result}

    @type_check
    @app_tool()
    @event_registered(
        operation_type=OperationType.WRITE
    )
    def apply_fertigation(
        self,
        start_ridge: int,
        end_ridge: int,
        nutrient_amount: float,
        water_mm: float,
    ) -> dict[str, Any]:
        """
        Apply combined nutrient + water (fertigation) to a ridge range.

        The nutrient amount is normalized (1.0 = a typical strong dose).
        Water is delivered in millimetres; the soil engine partitions it
        between top and root zones over the next physics tick.

        Args:
            start_ridge:     First ridge (0-63).
            end_ridge:       Last ridge (0-63, inclusive, >= start_ridge).
            nutrient_amount: Normalized nutrient input (>0); typical 0.5-1.5.
            water_mm:        Water input in millimetres (>0).
        """
        if not 0 <= start_ridge <= end_ridge < self.num_ridges:
            return {"error": f"Invalid ridge range [{start_ridge}, {end_ridge}]"}
        if float(nutrient_amount) <= 0:
            return {"error": "nutrient_amount must be > 0"}
        if float(water_mm) <= 0:
            return {"error": "water_mm must be > 0"}

        if self.physics_active:
            from are.simulation.apps.farm_world.farm_action_record import FarmActionRecord
            from are.simulation.physics import ManagementAction, ManagementActionType

            ridge_ids = list(range(start_ridge, end_ridge + 1))
            for ridge_id in ridge_ids:
                self.physics.queue_management_action(
                    ridge_id,
                    ManagementAction(
                        action_type=ManagementActionType.FERTIGATION,
                        amount=float(water_mm),  # water mm
                        quality=1.0,
                        metadata={
                            "nutrient_amount": float(nutrient_amount),
                            "water_mm": float(water_mm),
                        },
                    ),
                )
            import uuid as _uuid

            self.record_action(
                FarmActionRecord(
                    action_id=str(_uuid.uuid4())[:8],
                    timestamp=float(self.time_manager.time()),
                    actor_app=self.name,
                    action_type="fertigation",
                    ridge_ids=ridge_ids,
                    parameters={
                        "nutrient_amount": float(nutrient_amount),
                        "water_mm": float(water_mm),
                    },
                    direct_effect_summary={
                        "nutrient_input_registered": True,
                        "water_input_registered": True,
                    },
                )
            )
            self.advance_physics_time()

        self.is_state_modified = True
        return {
            "status": "ok",
            "fertigated_ridges": list(range(start_ridge, end_ridge + 1)),
            "nutrient_amount": float(nutrient_amount),
            "water_mm": float(water_mm),
        }

    @type_check
    @app_tool()
    @event_registered(
        operation_type=OperationType.WRITE
    )
    def dry_grain(self, target_moisture_pct: float = 13.0) -> dict[str, Any]:
        """
        Dry harvested grain in storage to a target moisture percentage.

        Used post-harvest when grain came in above the safe storage moisture
        window (usually 13-14%). The drying step advances the yield-recovery
        state's grain_moisture_frac for harvested ridges, and flips the
        inventory's `grain_dried` flag so subsequent `store_grain()` knows
        the grain is safe for long-term storage.

        Args:
            target_moisture_pct: Target storage moisture, typically 13.0.
                                 Must be in [11, 16].
        """
        if not 11.0 <= float(target_moisture_pct) <= 16.0:
            return {"error": "target_moisture_pct must be in [11, 16]"}
        target_frac = float(target_moisture_pct) / 100.0
        affected = 0
        if self.physics_active:
            for state in self.physics.yield_recovery.states.values():
                if state.harvested:
                    state.grain_moisture_frac = target_frac
                    state.drying_required = False
                    affected += 1
        self._inventory.grain_dried = True
        self.is_state_modified = True
        return {
            "status": "ok",
            "target_moisture_pct": float(target_moisture_pct),
            "ridges_dried": affected,
            "trailer_grain_kg": round(self._inventory.harvest_grain_kg, 2),
        }

    @type_check
    @app_tool()
    @event_registered(
        operation_type=OperationType.WRITE
    )
    def store_grain(self) -> dict[str, Any]:
        """
        Finalise harvested grain into long-term warehouse storage.

        Moves the trailer's `harvest_grain_kg` into the warehouse's
        `warehouse_grain_kg` and zeroes the trailer. Records an action for
        the workflow validator + FOS gate matchers so the post-harvest
        sequence (harvest → dry → store) is observable end-to-end. If the
        grain wasn't dried first, returns a warning but still moves it.
        """
        moved_kg = round(self._inventory.harvest_grain_kg, 2)
        self._inventory.warehouse_grain_kg = round(
            self._inventory.warehouse_grain_kg + moved_kg, 2
        )
        self._inventory.harvest_grain_kg = 0.0
        warning = None
        if not self._inventory.grain_dried and moved_kg > 0:
            warning = "grain stored without drying — long-term spoilage risk"

        if self.physics_active:
            from are.simulation.apps.farm_world.farm_action_record import FarmActionRecord
            import uuid as _uuid

            self.record_action(
                FarmActionRecord(
                    action_id=str(_uuid.uuid4())[:8],
                    timestamp=float(self.time_manager.time()),
                    actor_app=self.name,
                    action_type="store_grain",
                    ridge_ids=[],
                    parameters={},
                    direct_effect_summary={
                        "warehouse_grain_kg": self._inventory.warehouse_grain_kg,
                        "moved_kg": moved_kg,
                    },
                )
            )
        self.is_state_modified = True
        return {
            "status": "ok",
            "moved_kg": moved_kg,
            "warehouse_grain_kg": self._inventory.warehouse_grain_kg,
            "trailer_grain_kg": self._inventory.harvest_grain_kg,
            **({"warning": warning} if warning else {}),
        }

    def advance_physics_time(self, target_sim_time: float | None = None) -> dict[str, Any]:
        """Run physics forward to the current simulation clock.

        Idempotent: if no time has elapsed since the last call, this is a
        no-op. Day-boundary crossings trigger one daily tick per crossed
        day; sub-daily intervals trigger a partial soil update if any
        irrigation is pending. The orchestrator owns the actual engine call
        sequence.

        Returns a small status dict for tracing.
        """
        if self._physics is None or not self._physics.engines_active:
            return {"status": "skipped", "reason": "physics_inactive"}
        from are.simulation.apps.farm_world.physics_orchestrator import (
            advance_physics_time as _advance,
        )
        current_time = (
            float(target_sim_time)
            if target_sim_time is not None
            else float(self.time_manager.time())
        )
        return _advance(self, current_time)

    # Fields hidden from agent — observable only via SensorApp / DroneApp /
    # RobotApp (or ground-truth driver fields never exposed at all)
    _OBSERVATION_FIELDS = {
        "soil_vwc", "soil_temp_c", "ndvi", "canopy_temp_c",
        "pest_pressure", "disease_pressure",
        "pest_pressure_base", "disease_pressure_base", "last_spray_sim_time",
        "planted_at_sim_time",
    }

    def _agent_ridge_dict(self, ridge: RidgeState) -> dict[str, Any]:
        """Return ridge dict with observation fields stripped for agent tools."""
        self._refresh_ridge_dynamics(ridge)
        return {
            k: v for k, v in ridge.to_dict().items()
            if k not in self._OBSERVATION_FIELDS
        }

    # ------------------------------------------------------------------
    # Internal helpers (used by device apps directly, not via tool system)
    # ------------------------------------------------------------------

    def _refresh_ridge_dynamics(self, ridge: RidgeState) -> None:
        """
        Derive all time-driven ridge fields from stored timestamps and the
        current simulation clock. Called from every read path; no caller ever
        has to "tick" a day.

        Pest / disease pressure    ← _effective_pressure(base, last_spray, now)
        pesticide_applied_days_ago ← floor((now - last_spray) / 86400)
        days_since_planted         ← floor((now - planted_at) / 86400)
        growth_stage               ← _stage_for_days(days_since_planted)
        grain_moisture_pct         ← _grain_moisture_for_stage(stage, days_in_r6_plus)

        Scenarios that bypass `set_ridge_planted` and manually set
        `days_since_planted` / `growth_stage` leave `planted_at_sim_time` as
        None, and the growth fields are not overwritten here.
        """
        now_t = float(self.time_manager.time())
        self._apply_due_irrigation_for_ridge(ridge.ridge_id, now_t)

        # Pest / disease (three-phase curve after spray)
        ridge.pest_pressure = _effective_pressure(
            ridge.pest_pressure_base, ridge.last_spray_sim_time, now_t
        )
        ridge.disease_pressure = _effective_pressure(
            ridge.disease_pressure_base, ridge.last_spray_sim_time, now_t
        )
        if ridge.last_spray_sim_time is None:
            ridge.pesticide_applied_days_ago = -1
        else:
            elapsed = max(0.0, now_t - ridge.last_spray_sim_time)
            ridge.pesticide_applied_days_ago = int(elapsed // _SECONDS_PER_DAY)

        # Growth progression (only when a real `set_ridge_planted` happened)
        if ridge.planted and ridge.planted_at_sim_time is not None:
            elapsed_s = max(0.0, now_t - ridge.planted_at_sim_time)
            days = int(elapsed_s // _SECONDS_PER_DAY)
            ridge.days_since_planted = days
            ridge.growth_stage = _stage_for_days(days)
            r6_threshold = next(
                (t for t, s in _STAGE_THRESHOLDS if s == GrowthStage.R6.value), 110
            )
            days_in_r6_plus = max(0, days - r6_threshold)
            ridge.grain_moisture_pct = _grain_moisture_for_stage(
                ridge.growth_stage, days_in_r6_plus
            )

    def get_ridge(self, ridge_id: int) -> RidgeState:
        """Direct access for device apps that hold a reference to this app."""
        r = self._ridges[ridge_id]
        self._refresh_ridge_dynamics(r)
        return r

    def set_ridges(self, ridges: list[RidgeState]) -> None:
        """Replace the ridge list (called by TractorApp.form_ridges)."""
        self._ridges = ridges
        self.is_state_modified = True

    def get_avg_vwc(self) -> float:
        """Return average soil VWC across all ridges (used by WeatherApp)."""
        self._apply_due_irrigation()
        return round(sum(r.soil_vwc for r in self._ridges) / self.num_ridges, 4)

    def get_pending_irrigation_for_ridges(
        self, ridge_ids: list[int]
    ) -> list[dict[str, Any]]:
        """Return pending irrigation sessions overlapping the requested ridges."""
        self._apply_due_irrigation()
        results = []
        seen_keys: set[tuple[int, float, float]] = set()
        for ridge_id in ridge_ids:
            for item in self._pending_irrigation.get(ridge_id, []):
                key = (
                    ridge_id,
                    float(item["effect_ready_at"]),
                    float(item["add_vwc"]),
                )
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                results.append(
                    {
                        "ridge_id": ridge_id,
                        "effect_ready_at": float(item["effect_ready_at"]),
                        "add_vwc": float(item["add_vwc"]),
                    }
                )
        results.sort(key=lambda item: (item["effect_ready_at"], item["ridge_id"]))
        return results

    def consume_seeds(self, seed_type: str, count: int) -> bool:
        """Deduct seeds from warehouse. Returns False if insufficient."""
        current = self._inventory.seed_stock.get(seed_type, 0)
        if current < count:
            return False
        self._inventory.seed_stock[seed_type] = current - count
        self.is_state_modified = True
        return True

    def consume_pesticide(self, liters: float) -> bool:
        """Deduct pesticide from warehouse. Returns False if insufficient."""
        if self._inventory.pesticide_liters < liters:
            return False
        self._inventory.pesticide_liters = round(self._inventory.pesticide_liters - liters, 2)
        self.is_state_modified = True
        return True

    def consume_fertilizer(self, kg: float) -> bool:
        """Deduct fertilizer from warehouse. Returns False if insufficient."""
        if self._inventory.fertilizer_kg < kg:
            return False
        self._inventory.fertilizer_kg = round(self._inventory.fertilizer_kg - kg, 2)
        self.is_state_modified = True
        return True

    def consume_fuel(self, liters: float) -> bool:
        """Deduct fuel from warehouse. Returns False if insufficient."""
        if self._inventory.fuel_liters < liters:
            return False
        self._inventory.fuel_liters = round(self._inventory.fuel_liters - liters, 2)
        self.is_state_modified = True
        return True

    def add_grain_to_inventory(self, kg: float) -> None:
        """Deposit grain into warehouse inventory (called by tractor.unload_grain)."""
        self._inventory.harvest_grain_kg = round(
            self._inventory.harvest_grain_kg + float(kg), 2
        )
        self.is_state_modified = True

    def _apply_irrigation_effect(self, ridge_id: int, add_vwc: float) -> None:
        ridge = self._ridges[ridge_id]
        ridge.soil_vwc = min(0.45, ridge.soil_vwc + add_vwc)

    def _apply_due_irrigation(self) -> None:
        now_t = float(self.time_manager.time())
        for ridge_id in list(self._pending_irrigation):
            self._apply_due_irrigation_for_ridge(ridge_id, now_t)

    def _apply_due_irrigation_for_ridge(self, ridge_id: int, now_t: float) -> None:
        pending = self._pending_irrigation.get(ridge_id)
        if not pending:
            return

        remaining = []
        applied = False
        for item in pending:
            if float(item["effect_ready_at"]) <= now_t:
                self._apply_irrigation_effect(ridge_id, float(item["add_vwc"]))
                applied = True
            else:
                remaining.append(item)

        if remaining:
            self._pending_irrigation[ridge_id] = remaining
        else:
            del self._pending_irrigation[ridge_id]

        if applied:
            self.is_state_modified = True
