"""
FarmPhysicsState — central container for the 7 physics engines, the observation
model, and the per-tick action queues consumed by the orchestrator.

Held by FarmWorldApp. Constructed lazily on first physics-aware activity
(record_action / configure_physics_profile / queue_*). Until that point,
``engines_active`` is False and FarmWorldApp / its tools fall back to their
pre-physics behaviour, preserving backward compatibility for the parallel
metrics run on `main`.

This is a state container with no side effects beyond engine state. The
orchestrator under physics_orchestrator.py is the only writer of cross-engine
state transitions; it advances each engine forward in the order prescribed by
scenario_farm_world_physics/physics_action_tick_integration_guide.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from are.simulation.apps.farm_world.farm_action_record import FarmActionRecord
from are.simulation.physics import (
    BioticPressureEngine,
    CanopyBiomassGrowthEngine,
    HarvestAction,
    ManagementAction,
    ManagementEffectEngine,
    ObservationModel,
    ObservationModelParameters,
    SensorAsset,
    SoilEngine,
    ThermalTimePhenologyEngine,
    TreatmentApplication,
    WeatherGenerator,
    YieldRecoveryEngine,
)


# Default fixed soil/canopy sensor placement matches the existing SensorApp
# zone layout (S1..S6 / C1..C6 installed at ridges 5, 15, 25, 38, 48, 58).
# Each sensor represents a contiguous zone of ridges, mirrored in the
# zone-aggregation logic in sensor_app.read_soil_sensors / read_canopy_sensors.
_DEFAULT_SOIL_SENSOR_RIDGES: tuple[int, ...] = (5, 15, 25, 38, 48, 58)
_DEFAULT_CANOPY_SENSOR_RIDGES: tuple[int, ...] = (5, 15, 25, 38, 48, 58)


@dataclass
class FarmPhysicsState:
    """
    Engine bundle + queues. One instance per FarmWorldApp.

    All `pending_*` queues are consumed by the next call to
    ``physics_orchestrator.advance_physics_time`` and then cleared.
    """

    num_ridges: int = 64

    # Scenario configuration. Set via FarmWorldApp.configure_physics_profile().
    profile_name: str | None = None
    location: str | None = None
    scenario_type: str | None = None
    latitude_deg: float = 45.7  # Harbin/Heilongjiang default
    random_seed: int = 0

    # Engines. Constructed in __post_init__ so they exist for type-checking but
    # remain unused until the first physics-aware activity flips engines_active.
    soil: SoilEngine = field(init=False)
    phenology: ThermalTimePhenologyEngine = field(init=False)
    canopy: CanopyBiomassGrowthEngine = field(init=False)
    biotic: BioticPressureEngine = field(init=False)
    management: ManagementEffectEngine = field(init=False)
    yield_recovery: YieldRecoveryEngine = field(init=False)
    weather_generator: WeatherGenerator | None = field(init=False, default=None)
    observation_model: ObservationModel = field(init=False)

    # Action history (audit trail) and tick-scoped queues.
    action_log: list[FarmActionRecord] = field(default_factory=list)
    pending_management_actions_by_ridge: dict[int, list[ManagementAction]] = field(
        default_factory=dict
    )
    pending_treatments_by_ridge: dict[int, list[TreatmentApplication]] = field(
        default_factory=dict
    )
    pending_harvest_actions_by_ridge: dict[int, HarvestAction] = field(
        default_factory=dict
    )

    # Simulation-time bookkeeping. last_physics_sim_time is the epoch-seconds
    # boundary up to which engines have been advanced. None on first run.
    last_physics_sim_time: float | None = None

    # Set True when any orchestrator call has executed, or any tool has queued
    # a management/treatment action. Until then, FarmWorldApp's legacy helpers
    # remain in charge.
    engines_active: bool = False

    def __post_init__(self) -> None:
        self.soil = SoilEngine(num_ridges=self.num_ridges)
        self.phenology = ThermalTimePhenologyEngine(num_ridges=self.num_ridges)
        self.canopy = CanopyBiomassGrowthEngine(num_ridges=self.num_ridges)
        self.biotic = BioticPressureEngine(num_ridges=self.num_ridges)
        self.management = ManagementEffectEngine(num_ridges=self.num_ridges)
        self.yield_recovery = YieldRecoveryEngine(num_ridges=self.num_ridges)
        self.observation_model = ObservationModel(
            params=ObservationModelParameters(random_seed=self.random_seed)
        )
        self._install_default_sensor_assets()

    # ------------------------------------------------------------------
    # Sensor asset registration (used by ObservationModel.observe_fixed_sensors)
    # ------------------------------------------------------------------

    def _install_default_sensor_assets(self) -> None:
        from are.simulation.physics import ObservationModality

        for asset_idx, ridge_id in enumerate(_DEFAULT_SOIL_SENSOR_RIDGES):
            self.observation_model.add_asset(
                SensorAsset(
                    asset_id=f"soil_sensor_{asset_idx}",
                    modality=ObservationModality.SOIL_SENSOR,
                    fixed_ridge_id=ridge_id,
                    support_radius_ridges=2,
                )
            )
        for asset_idx, ridge_id in enumerate(_DEFAULT_CANOPY_SENSOR_RIDGES):
            self.observation_model.add_asset(
                SensorAsset(
                    asset_id=f"canopy_sensor_{asset_idx}",
                    modality=ObservationModality.CANOPY_INDEX_SENSOR,
                    fixed_ridge_id=ridge_id,
                    support_radius_ridges=2,
                )
            )

    @property
    def soil_sensor_ridges(self) -> tuple[int, ...]:
        return _DEFAULT_SOIL_SENSOR_RIDGES

    @property
    def canopy_sensor_ridges(self) -> tuple[int, ...]:
        return _DEFAULT_CANOPY_SENSOR_RIDGES

    # ------------------------------------------------------------------
    # Action recording and queue management
    # ------------------------------------------------------------------

    def record_action(self, action: FarmActionRecord) -> None:
        self.action_log.append(action)
        self.engines_active = True

    def queue_management_action(self, ridge_id: int, action: ManagementAction) -> None:
        self.pending_management_actions_by_ridge.setdefault(ridge_id, []).append(action)
        self.engines_active = True

    def queue_treatment(self, ridge_id: int, treatment: TreatmentApplication) -> None:
        self.pending_treatments_by_ridge.setdefault(ridge_id, []).append(treatment)
        self.engines_active = True

    def queue_harvest(self, ridge_id: int, harvest: HarvestAction) -> None:
        # Only the latest harvest action per ridge per tick is kept.
        self.pending_harvest_actions_by_ridge[ridge_id] = harvest
        self.engines_active = True

    def drain_pending_management_actions(self) -> dict[int, list[ManagementAction]]:
        out = self.pending_management_actions_by_ridge
        self.pending_management_actions_by_ridge = {}
        return out

    def drain_pending_treatments(self) -> dict[int, list[TreatmentApplication]]:
        out = self.pending_treatments_by_ridge
        self.pending_treatments_by_ridge = {}
        return out

    def drain_pending_harvest_actions(self) -> dict[int, HarvestAction]:
        out = self.pending_harvest_actions_by_ridge
        self.pending_harvest_actions_by_ridge = {}
        return out

    # ------------------------------------------------------------------
    # Snapshot / debug helpers
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Lightweight read-only view; used in tests and GUI debug overlays."""
        return {
            "engines_active": self.engines_active,
            "last_physics_sim_time": self.last_physics_sim_time,
            "profile_name": self.profile_name,
            "location": self.location,
            "scenario_type": self.scenario_type,
            "num_actions_logged": len(self.action_log),
            "pending_management_actions": {
                rid: len(actions)
                for rid, actions in self.pending_management_actions_by_ridge.items()
            },
            "pending_treatments": {
                rid: len(treatments)
                for rid, treatments in self.pending_treatments_by_ridge.items()
            },
            "pending_harvest_actions": list(
                self.pending_harvest_actions_by_ridge.keys()
            ),
        }
