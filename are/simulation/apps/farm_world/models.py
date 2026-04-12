"""
Farm-World data models.

All dataclasses used across the farm_world app package.
Source references:
  [PDF-pN] = PDF page N
  [设计]   = design decision, no direct PDF source
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SeedType(str, Enum):
    """Soybean seed types available for planting. [PDF-p5]"""
    EARLY_COLD     = "EARLY_COLD"      # 早熟耐寒, 100-110 days, tolerates <10°C
    STANDARD       = "STANDARD"        # 标准型,   110-115 days, typical window
    HIGH_DENSITY   = "HIGH_DENSITY"    # 高密度紧凑型, sensitive to uneven moisture
    STRESS_TOLERANT = "STRESS_TOLERANT" # 抗逆型, tolerates frost/drought/excess water


class GrowthStage(str, Enum):
    """Soybean growth stage sequence. [PDF-p6]"""
    BARE = "bare"   # pre-emergence / pre-planting
    VE   = "VE"     # emergence
    V1   = "V1"
    V2   = "V2"
    V3   = "V3"
    V4   = "V4"
    V5   = "V5"
    V6   = "V6"
    V7   = "V7"
    V8   = "V8"
    R1   = "R1"
    R2   = "R2"
    R3   = "R3"
    R4   = "R4"
    R5   = "R5"
    R6   = "R6"
    R7   = "R7"
    R8   = "R8"


class SeasonPhase(str, Enum):
    """High-level season phase used for overview display. [设计]"""
    PREP      = "prep"
    PLANTING  = "planting"
    GROWING   = "growing"
    HARVEST   = "harvest"


# ---------------------------------------------------------------------------
# RidgeState
# ---------------------------------------------------------------------------

@dataclass
class RidgeState:
    """
    State of a single ridge (垄). The farm has 64 ridges, ID 0-63. [PDF-p1]

    Coordinates: x = ridge_id (0-63), y = length direction (0-268 m). [PDF-p2]
    """
    ridge_id: int                    # 0-63 [PDF-p1]
    soil_vwc: float                  # volumetric water content 0.0-1.0 [PDF-p3]
    soil_temp_c: float               # soil temperature at 5 cm depth (°C) [PDF-p3]
    growth_stage: str                # GrowthStage value [PDF-p6]
    ndvi: float                      # 0.0-1.0; -1 = not yet observed [PDF-p7]
    canopy_temp_c: float             # canopy temperature (°C); -1 = not observed [PDF-p7]
    pest_pressure: float             # 0.0-1.0 [PDF-p7]
    disease_pressure: float          # 0.0-1.0 [PDF-p7]
    planted: bool                    # whether seeds have been sown
    seed_type: str | None            # SeedType value or None [PDF-p5]
    seed_spacing_cm: float | None    # in-row seed spacing (cm); density control parameter [PDF-p6]
    seeds_planted: int               # realized plant count for this ridge at sowing [PDF-p6]
    days_since_planted: int          # days elapsed since planting
    grain_moisture_pct: float        # grain moisture %; 13-18% is harvest window [PDF-p10]
    yield_potential: float           # 0.0-1.0 relative yield potential [PDF-p6]
    irrigation_pending: bool         # True = irrigation queued, takes effect next advance_day [PDF-p10]
    pesticide_applied_days_ago: int  # days since last pesticide application; -1 = never [PDF-p9]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ridge_id": self.ridge_id,
            "soil_vwc": round(self.soil_vwc, 4),
            "soil_temp_c": round(self.soil_temp_c, 2),
            "growth_stage": self.growth_stage,
            "ndvi": round(self.ndvi, 3),
            "canopy_temp_c": round(self.canopy_temp_c, 2),
            "pest_pressure": round(self.pest_pressure, 3),
            "disease_pressure": round(self.disease_pressure, 3),
            "planted": self.planted,
            "seed_type": self.seed_type,
            "seed_spacing_cm": (
                round(self.seed_spacing_cm, 2)
                if self.seed_spacing_cm is not None
                else None
            ),
            "seeds_planted": self.seeds_planted,
            "days_since_planted": self.days_since_planted,
            "grain_moisture_pct": round(self.grain_moisture_pct, 2),
            "yield_potential": round(self.yield_potential, 3),
            "irrigation_pending": self.irrigation_pending,
            "pesticide_applied_days_ago": self.pesticide_applied_days_ago,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RidgeState":
        return cls(
            ridge_id=d["ridge_id"],
            soil_vwc=d["soil_vwc"],
            soil_temp_c=d["soil_temp_c"],
            growth_stage=d["growth_stage"],
            ndvi=d["ndvi"],
            canopy_temp_c=d["canopy_temp_c"],
            pest_pressure=d["pest_pressure"],
            disease_pressure=d["disease_pressure"],
            planted=d["planted"],
            seed_type=d["seed_type"],
            seed_spacing_cm=d.get("seed_spacing_cm"),
            seeds_planted=d.get("seeds_planted", 0),
            days_since_planted=d["days_since_planted"],
            grain_moisture_pct=d["grain_moisture_pct"],
            yield_potential=d["yield_potential"],
            irrigation_pending=d["irrigation_pending"],
            pesticide_applied_days_ago=d["pesticide_applied_days_ago"],
        )

    @classmethod
    def default(cls, ridge_id: int) -> "RidgeState":
        """Create a bare, unplanted ridge with typical spring soil conditions."""
        return cls(
            ridge_id=ridge_id,
            soil_vwc=0.22,
            soil_temp_c=10.0,
            growth_stage=GrowthStage.BARE.value,
            ndvi=-1.0,
            canopy_temp_c=-1.0,
            pest_pressure=0.0,
            disease_pressure=0.0,
            planted=False,
            seed_type=None,
            seed_spacing_cm=None,
            seeds_planted=0,
            days_since_planted=0,
            grain_moisture_pct=0.0,
            yield_potential=1.0,
            irrigation_pending=False,
            pesticide_applied_days_ago=-1,
        )


# ---------------------------------------------------------------------------
# WeatherState
# ---------------------------------------------------------------------------

@dataclass
class WeatherState:
    """Current weather and 7-day forecast. Sourced from WX-CQ10 station. [PDF-p3]"""
    date: str              # ISO date string, e.g. "2026-05-12"
    temp_c: float          # air temperature (°C)
    humidity_pct: float    # relative humidity (%)
    wind_speed_ms: float   # wind speed (m/s)
    rainfall_mm: float     # daily precipitation (mm)
    solar_radiation: float # solar radiation (W/m²)
    forecast: list[dict]   # next 7 days, each dict has same keys minus forecast

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "temp_c": round(self.temp_c, 2),
            "humidity_pct": round(self.humidity_pct, 1),
            "wind_speed_ms": round(self.wind_speed_ms, 2),
            "rainfall_mm": round(self.rainfall_mm, 2),
            "solar_radiation": round(self.solar_radiation, 1),
            "forecast": self.forecast,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WeatherState":
        return cls(
            date=d["date"],
            temp_c=d["temp_c"],
            humidity_pct=d["humidity_pct"],
            wind_speed_ms=d["wind_speed_ms"],
            rainfall_mm=d["rainfall_mm"],
            solar_radiation=d["solar_radiation"],
            forecast=d.get("forecast", []),
        )

    @classmethod
    def default(cls, date: str = "2026-04-25") -> "WeatherState":
        """Typical late-April Harbin weather for field prep phase."""
        return cls(
            date=date,
            temp_c=15.0,
            humidity_pct=55.0,
            wind_speed_ms=2.0,
            rainfall_mm=0.0,
            solar_radiation=380.0,
            forecast=[],
        )


# ---------------------------------------------------------------------------
# InventoryState
# ---------------------------------------------------------------------------

@dataclass
class InventoryState:
    """
    Farm warehouse inventory. Devices load from here before operating.

    Seed stock: ~4500-7500 plants/ridge × 64 ridges → 500 000 plants initial. [PDF-p6]
    Pesticide warehouse: initial 2000 L. [PDF-p9]
    Fertilizer warehouse: initial 2000 kg. [PDF-p9]
    Fuel warehouse: initial 1000 L. [设计]
    """
    seed_stock: dict[str, int]  # SeedType.value -> plant count
    pesticide_liters: float     # warehouse pesticide stock (L)
    fertilizer_kg: float        # warehouse fertilizer stock (kg)
    fuel_liters: float          # warehouse fuel stock (L)
    harvest_grain_kg: float     # accumulated harvested grain (kg)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_stock": dict(self.seed_stock),
            "pesticide_liters": round(self.pesticide_liters, 2),
            "fertilizer_kg": round(self.fertilizer_kg, 2),
            "fuel_liters": round(self.fuel_liters, 2),
            "harvest_grain_kg": round(self.harvest_grain_kg, 2),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "InventoryState":
        return cls(
            seed_stock=d["seed_stock"],
            pesticide_liters=d["pesticide_liters"],
            fertilizer_kg=d["fertilizer_kg"],
            fuel_liters=d.get("fuel_liters", 1000.0),
            harvest_grain_kg=d["harvest_grain_kg"],
        )

    @classmethod
    def default(cls) -> "InventoryState":
        return cls(
            seed_stock={
                SeedType.STANDARD.value:        1000000,
                SeedType.EARLY_COLD.value:       1000000,
                SeedType.HIGH_DENSITY.value:     1000000,
                SeedType.STRESS_TOLERANT.value:  1000000,
            },
            pesticide_liters=2000.0,
            fertilizer_kg=2000.0,
            fuel_liters=1000.0,
            harvest_grain_kg=0.0,
        )
