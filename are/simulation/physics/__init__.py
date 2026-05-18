"""
Farm-ARE physics engines.

Public interface for the seven daily-update engines plus the observation model
described in are/simulation/scenarios/scenario_farm_world_physics/physics_action_tick_integration_guide.md.

Each engine is independently constructable and exposes update_day(...). The
orchestrator under are/simulation/apps/farm_world/physics_orchestrator.py is the
single integration site that wires their outputs together.

Several engines re-declare lookalike GrowthStage/SeedType enums to remain
standalone. The canonical values are SoybeanStage and SeedType from the
phenology engine; the duplicated enums in other modules carry the same string
values and are interchangeable at the value level. This package re-exports only
the canonical names to avoid ambiguity at the call site.
"""

from are.simulation.physics.weather_engine import (
    MonthlyClimate,
    WeatherDay,
    WeatherEvent,
    WeatherGenerator,
    WeatherGeneratorConfig,
)
from are.simulation.physics.soil_engine import (
    RidgeSoilState,
    SoilDayResult,
    SoilEngine,
    SoilHydraulicModifier,
    SoilParameters,
    WeatherInput as SoilWeatherInput,
)
from are.simulation.physics.phenology_engine import (
    PhenologyDayResult,
    PhenologyParameters,
    PhenologySoilInput,
    PhenologyState,
    PhenologyWeatherInput,
    PlantingConfig,
    SeedType,
    SeedTypeParameters,
    SoybeanStage,
    ThermalTimePhenologyEngine,
)
from are.simulation.physics.canopy_biomass_engine import (
    CanopyBiomassDayResult,
    CanopyBiomassGrowthEngine,
    CanopyBiomassParameters,
    CanopyBiomassState,
    GrowthSoilInput,
    GrowthWeatherInput,
    ManagementStressInput,
    PhenologyInput as CanopyPhenologyInput,
    SeedGrowthParameters,
)
from are.simulation.physics.biotic_pressure_engine import (
    BioticCropInput,
    BioticPressureDayResult,
    BioticPressureEngine,
    BioticPressureParameters,
    BioticPressureState,
    BioticSoilInput,
    BioticWeatherInput,
    TreatmentApplication,
    TreatmentType,
)
from are.simulation.physics.management_effect_engine import (
    ManagementAction,
    ManagementActionType,
    ManagementCropInput,
    ManagementEffectDayResult,
    ManagementEffectEngine,
    ManagementEffectParameters,
    ManagementEffectState,
    ManagementSoilInput,
    ManagementWeatherInput,
)
from are.simulation.physics.yield_recovery_engine import (
    HarvestAction,
    YieldGrowthInput,
    YieldPhenologyInput,
    YieldRecoveryDayResult,
    YieldRecoveryEngine,
    YieldRecoveryParameters,
    YieldRecoveryState,
    YieldStressInput,
    YieldWeatherInput,
)
from are.simulation.physics.observation_model import (
    HiddenRidgeTruth,
    ObservationModality,
    ObservationModel,
    ObservationModelParameters,
    ObservationProduct,
    ObservationProductType,
    SensorAsset,
)

__all__ = [
    # Weather
    "MonthlyClimate",
    "WeatherDay",
    "WeatherEvent",
    "WeatherGenerator",
    "WeatherGeneratorConfig",
    # Soil
    "RidgeSoilState",
    "SoilDayResult",
    "SoilEngine",
    "SoilHydraulicModifier",
    "SoilParameters",
    "SoilWeatherInput",
    # Phenology (canonical SoybeanStage + SeedType)
    "PhenologyDayResult",
    "PhenologyParameters",
    "PhenologySoilInput",
    "PhenologyState",
    "PhenologyWeatherInput",
    "PlantingConfig",
    "SeedType",
    "SeedTypeParameters",
    "SoybeanStage",
    "ThermalTimePhenologyEngine",
    # Canopy / biomass
    "CanopyBiomassDayResult",
    "CanopyBiomassGrowthEngine",
    "CanopyBiomassParameters",
    "CanopyBiomassState",
    "CanopyPhenologyInput",
    "GrowthSoilInput",
    "GrowthWeatherInput",
    "ManagementStressInput",
    "SeedGrowthParameters",
    # Biotic pressure
    "BioticCropInput",
    "BioticPressureDayResult",
    "BioticPressureEngine",
    "BioticPressureParameters",
    "BioticPressureState",
    "BioticSoilInput",
    "BioticWeatherInput",
    "TreatmentApplication",
    "TreatmentType",
    # Management effect
    "ManagementAction",
    "ManagementActionType",
    "ManagementCropInput",
    "ManagementEffectDayResult",
    "ManagementEffectEngine",
    "ManagementEffectParameters",
    "ManagementEffectState",
    "ManagementSoilInput",
    "ManagementWeatherInput",
    # Yield recovery
    "HarvestAction",
    "YieldGrowthInput",
    "YieldPhenologyInput",
    "YieldRecoveryDayResult",
    "YieldRecoveryEngine",
    "YieldRecoveryParameters",
    "YieldRecoveryState",
    "YieldStressInput",
    "YieldWeatherInput",
    # Observation model
    "HiddenRidgeTruth",
    "ObservationModality",
    "ObservationModel",
    "ObservationModelParameters",
    "ObservationProduct",
    "ObservationProductType",
    "SensorAsset",
]
