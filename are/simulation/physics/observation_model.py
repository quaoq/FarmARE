from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from math import exp, sqrt
from typing import Any, Mapping, Sequence
import random


class ObservationModality(str, Enum):
    """
    Observation modalities represented in Farm-ARE.

    These correspond to the sensing stack discussed in the farm specification:
      - fixed soil probes
      - fixed canopy index sensors
      - weather station / radiation sensors handled by weather module
      - satellite vegetation-index products
      - multispectral UAV products
      - thermal UAV products
      - LiDAR products
      - ground robot / robot dog inspection
      - manual SPAD measurements
    """
    SOIL_SENSOR = "SOIL_SENSOR"
    CANOPY_INDEX_SENSOR = "CANOPY_INDEX_SENSOR"
    SATELLITE_NDVI = "SATELLITE_NDVI"
    UAV_MULTISPECTRAL = "UAV_MULTISPECTRAL"
    UAV_THERMAL = "UAV_THERMAL"
    UAV_LIDAR = "UAV_LIDAR"
    GROUND_INSPECTION_RGB = "GROUND_INSPECTION_RGB"
    GROUND_INSPECTION_LIDAR = "GROUND_INSPECTION_LIDAR"
    MANUAL_SPAD = "MANUAL_SPAD"


class ObservationProductType(str, Enum):
    """
    Product types emitted by the observation model.

    The model emits derived observations rather than exposing hidden world state
    directly. Each product has spatial coverage, timestamp, uncertainty, and
    optional detection outputs.
    """
    SOIL_MOISTURE_POINT = "SOIL_MOISTURE_POINT"
    SOIL_TEMPERATURE_POINT = "SOIL_TEMPERATURE_POINT"
    CANOPY_INDEX_POINT = "CANOPY_INDEX_POINT"
    NDVI_MAP = "NDVI_MAP"
    CANOPY_TEMP_MAP = "CANOPY_TEMP_MAP"
    CANOPY_HEIGHT_MAP = "CANOPY_HEIGHT_MAP"
    SPAD_POINT = "SPAD_POINT"
    PEST_DETECTION = "PEST_DETECTION"
    DISEASE_DETECTION = "DISEASE_DETECTION"
    ANOMALY_MAP = "ANOMALY_MAP"


@dataclass
class ObservationModelParameters:
    """
    Parameters for the Farm-ARE observation model.

    Modeling basis:
        The observation model follows a state-space / partially observable
        system view. The physics engines maintain hidden truth states. Sensors
        provide noisy, delayed, spatially incomplete measurements of selected
        variables. Agent tools should consume these observations rather than
        hidden truth.

    Remote-sensing simplification:
        Multispectral and satellite products observe vegetation indices derived
        from canopy/NDVI proxies. Thermal products observe canopy temperature
        anomalies related to water stress or disease. LiDAR products observe
        canopy height/structure proxies. Ground inspection provides higher
        resolution classification/detection signals but limited coverage.

    Engineering simplification:
        This module does not implement radiative transfer, camera geometry,
        image formation, object detection networks, or georeferencing. It
        produces ridge-level observation products with configurable noise,
        coverage, latency, and detection error rates.
    """

    # Noise levels for fixed sensors.
    soil_vwc_noise_std: float = 0.015
    soil_temp_noise_std_c: float = 0.6
    canopy_index_noise_std: float = 0.025
    spad_noise_std: float = 2.0

    # Remote-sensing noise.
    satellite_ndvi_noise_std: float = 0.050
    uav_ndvi_noise_std: float = 0.025
    thermal_noise_std_c: float = 1.0
    lidar_height_noise_std_m: float = 0.05

    # Observation latency.
    fixed_sensor_latency_days: int = 0
    satellite_latency_days: int = 1
    uav_latency_days: int = 0
    ground_inspection_latency_days: int = 0
    manual_spad_latency_days: int = 0

    # Coverage / availability.
    satellite_revisit_days: int = 5
    satellite_min_ridges_per_pixel: int = 8
    uav_default_resolution_ridges: int = 1
    ground_inspection_max_ridges: int = 4

    # Anomaly detection thresholds.
    ndvi_anomaly_drop_threshold: float = 0.08
    thermal_anomaly_threshold_c: float = 2.5
    canopy_height_anomaly_threshold_m: float = 0.12

    # Ground classification / detection quality.
    # These are used when hidden pressure exists; observation may still miss it.
    pest_detection_sensitivity: float = 0.85
    pest_detection_specificity: float = 0.90
    disease_detection_sensitivity: float = 0.80
    disease_detection_specificity: float = 0.88

    # Detection thresholds mapping hidden pressure to "present".
    pest_presence_pressure_threshold: float = 0.35
    disease_presence_pressure_threshold: float = 0.30

    # SPAD proxy mapping.
    spad_min: float = 25.0
    spad_max: float = 50.0
    spad_ndvi_weight: float = 0.65
    spad_nutrient_weight: float = 0.35

    # Canopy height proxy.
    max_canopy_height_m: float = 1.0
    canopy_height_lai_saturation_coeff: float = 0.45

    # Reproducibility.
    random_seed: int = 0


@dataclass
class HiddenRidgeTruth:
    """
    Hidden ridge-level truth consumed by the observation model.

    These values come from the soil, phenology, canopy/biomass, biotic-pressure,
    and management-effect modules. The agent should not receive this object
    directly.
    """
    ridge_id: int

    # Soil truth.
    top_vwc: float = 0.25
    root_vwc: float = 0.25
    top_temp_c: float = 10.0

    # Canopy/growth truth.
    ndvi_proxy: float = 0.20
    lai: float = 0.0
    canopy_cover: float = 0.0
    canopy_temp_c: float = 20.0
    biomass_g_m2: float = 0.0

    # Biotic truth.
    weed_pressure: float = 0.0
    insect_pressure: float = 0.0
    disease_pressure: float = 0.0

    # Management truth.
    nutrient_index: float = 1.0

    # Optional late-season structural truth.
    lodging_severity: float = 0.0


@dataclass
class SensorAsset:
    """
    Sensor/platform asset description.

    fixed_ridge_id:
        For fixed sensors, the ridge where the asset is installed.

    support_radius_ridges:
        How many neighboring ridges are represented by the sensor reading.
        Example: support_radius_ridges=1 means the reading can be treated as
        local evidence for ridge_id-1, ridge_id, ridge_id+1.

    available:
        Allows scenarios to model sensor outage or platform unavailability.
    """
    asset_id: str
    modality: ObservationModality
    fixed_ridge_id: int | None = None
    support_radius_ridges: int = 0
    available: bool = True


@dataclass
class ObservationProduct:
    """
    Observation product emitted to agent-facing tools.

    observed_day:
        The day the measurement represents.

    available_day:
        The day it becomes available to the agent/tool layer. This captures
        latency from satellite revisit, map processing, or manual entry.

    ridge_ids:
        Ridges covered by the product.

    values:
        Product payload. Examples:
          - {ridge_id: ndvi_value}
          - {ridge_id: canopy_temp_c}
          - {ridge_id: {"pest_present": bool, "confidence": float}}
    """
    product_id: str
    product_type: ObservationProductType
    modality: ObservationModality
    asset_id: str
    observed_day: date
    available_day: date
    ridge_ids: list[int]
    values: dict[int, Any]
    uncertainty: dict[str, float]
    tags: list[str] = field(default_factory=list)


class ObservationModel:
    """
    Reduced observation model for Farm-ARE.

    Purpose:
        Convert hidden ridge-level world state into noisy, delayed, and spatially
        incomplete observation products.

    Scope:
        Ridge-level synthetic observations for fixed sensors, satellite/UAV maps,
        ground inspection, LiDAR, and manual SPAD.

    Non-scope:
        Image simulation, camera calibration, radiative transfer, georeferencing,
        SLAM, object detector training, or raw data product generation.
    """

    def __init__(
        self,
        params: ObservationModelParameters | None = None,
        assets: Sequence[SensorAsset] | None = None,
    ) -> None:
        self.params = params or ObservationModelParameters()
        self.assets: dict[str, SensorAsset] = {a.asset_id: a for a in (assets or [])}
        self.rng = random.Random(self.params.random_seed)
        self._product_counter = 0

    def add_asset(self, asset: SensorAsset) -> None:
        self.assets[asset.asset_id] = asset

    def observe_fixed_sensors(
        self,
        day: date,
        truth_by_ridge: Mapping[int, HiddenRidgeTruth],
    ) -> list[ObservationProduct]:
        """
        Generate observations from all available fixed sensors.

        Fixed sensors only observe their installed ridge directly. The support
        radius is recorded in the asset but is not used to overwrite neighboring
        ridges; downstream tools may use it for interpolation.
        """
        products: list[ObservationProduct] = []
        for asset in self.assets.values():
            if not asset.available or asset.fixed_ridge_id is None:
                continue

            truth = truth_by_ridge.get(asset.fixed_ridge_id)
            if truth is None:
                continue

            if asset.modality == ObservationModality.SOIL_SENSOR:
                products.append(self._soil_sensor_product(day, asset, truth))
            elif asset.modality == ObservationModality.CANOPY_INDEX_SENSOR:
                products.append(self._canopy_index_product(day, asset, truth))
            elif asset.modality == ObservationModality.MANUAL_SPAD:
                # Manual SPAD is not emitted automatically; use observe_spad.
                continue

        return products

    def observe_satellite_ndvi(
        self,
        day: date,
        truth_by_ridge: Mapping[int, HiddenRidgeTruth],
        start_ridge: int,
        end_ridge: int,
        asset_id: str = "satellite_ndvi",
    ) -> ObservationProduct:
        """
        Generate a coarse satellite NDVI product.

        The product groups ridges into coarse blocks. This represents that
        satellite pixels are much wider than a 1.1 m ridge and therefore cannot
        resolve individual ridges.
        """
        p = self.params
        ridges = list(range(start_ridge, end_ridge + 1))
        values: dict[int, float] = {}

        block_size = max(1, p.satellite_min_ridges_per_pixel)
        for block_start in range(start_ridge, end_ridge + 1, block_size):
            block = list(range(block_start, min(end_ridge + 1, block_start + block_size)))
            block_truth = [truth_by_ridge[r] for r in block if r in truth_by_ridge]
            if not block_truth:
                continue
            mean_ndvi = sum(t.ndvi_proxy for t in block_truth) / len(block_truth)
            obs = self._clip(mean_ndvi + self._normal(0.0, p.satellite_ndvi_noise_std), 0.0, 1.0)
            for r in block:
                values[r] = round(obs, 3)

        return self._product(
            product_type=ObservationProductType.NDVI_MAP,
            modality=ObservationModality.SATELLITE_NDVI,
            asset_id=asset_id,
            observed_day=day,
            available_day=day + timedelta(days=p.satellite_latency_days),
            ridge_ids=ridges,
            values=values,
            uncertainty={"ndvi_std": p.satellite_ndvi_noise_std, "coarse_block_ridges": float(block_size)},
            tags=["coarse_resolution"],
        )

    def observe_uav_multispectral(
        self,
        day: date,
        truth_by_ridge: Mapping[int, HiddenRidgeTruth],
        ridge_ids: Sequence[int],
        asset_id: str = "mavic3m",
    ) -> ObservationProduct:
        """
        Generate a UAV multispectral NDVI product at ridge-level resolution.

        This product approximates an orthomosaic/vegetation-index map after
        radiometric calibration. It is still noisy and should not be treated as
        hidden truth.
        """
        p = self.params
        values: dict[int, float] = {}
        for r in ridge_ids:
            truth = truth_by_ridge.get(r)
            if truth is None:
                continue
            obs = self._clip(truth.ndvi_proxy + self._normal(0.0, p.uav_ndvi_noise_std), 0.0, 1.0)
            values[r] = round(obs, 3)

        tags = self._ndvi_anomaly_tags(values)

        return self._product(
            product_type=ObservationProductType.NDVI_MAP,
            modality=ObservationModality.UAV_MULTISPECTRAL,
            asset_id=asset_id,
            observed_day=day,
            available_day=day + timedelta(days=p.uav_latency_days),
            ridge_ids=list(ridge_ids),
            values=values,
            uncertainty={"ndvi_std": p.uav_ndvi_noise_std, "resolution_ridges": float(p.uav_default_resolution_ridges)},
            tags=tags,
        )

    def observe_uav_thermal(
        self,
        day: date,
        truth_by_ridge: Mapping[int, HiddenRidgeTruth],
        ridge_ids: Sequence[int],
        asset_id: str = "matrice4t",
    ) -> ObservationProduct:
        """
        Generate a UAV thermal canopy-temperature product.

        Thermal products are useful for water-stress or disease/anomaly
        detection but do not identify cause by themselves.
        """
        p = self.params
        values: dict[int, float] = {}
        for r in ridge_ids:
            truth = truth_by_ridge.get(r)
            if truth is None:
                continue
            obs = truth.canopy_temp_c + self._normal(0.0, p.thermal_noise_std_c)
            values[r] = round(obs, 2)

        tags = self._thermal_anomaly_tags(values)

        return self._product(
            product_type=ObservationProductType.CANOPY_TEMP_MAP,
            modality=ObservationModality.UAV_THERMAL,
            asset_id=asset_id,
            observed_day=day,
            available_day=day + timedelta(days=p.uav_latency_days),
            ridge_ids=list(ridge_ids),
            values=values,
            uncertainty={"thermal_std_c": p.thermal_noise_std_c},
            tags=tags,
        )

    def observe_uav_lidar(
        self,
        day: date,
        truth_by_ridge: Mapping[int, HiddenRidgeTruth],
        ridge_ids: Sequence[int],
        asset_id: str = "zenmuse_l2",
    ) -> ObservationProduct:
        """
        Generate a UAV LiDAR canopy-height product.

        Canopy height is approximated from LAI and lodging severity. This is a
        structural observation proxy, not a point-cloud simulation.
        """
        p = self.params
        values: dict[int, float] = {}
        for r in ridge_ids:
            truth = truth_by_ridge.get(r)
            if truth is None:
                continue
            height = p.max_canopy_height_m * (1.0 - exp(-p.canopy_height_lai_saturation_coeff * max(0.0, truth.lai)))
            height *= (1.0 - 0.45 * self._clip(truth.lodging_severity, 0.0, 1.0))
            obs = max(0.0, height + self._normal(0.0, p.lidar_height_noise_std_m))
            values[r] = round(obs, 3)

        return self._product(
            product_type=ObservationProductType.CANOPY_HEIGHT_MAP,
            modality=ObservationModality.UAV_LIDAR,
            asset_id=asset_id,
            observed_day=day,
            available_day=day + timedelta(days=p.uav_latency_days),
            ridge_ids=list(ridge_ids),
            values=values,
            uncertainty={"height_std_m": p.lidar_height_noise_std_m},
            tags=[],
        )

    def observe_ground_inspection(
        self,
        day: date,
        truth_by_ridge: Mapping[int, HiddenRidgeTruth],
        ridge_ids: Sequence[int],
        asset_id: str = "robot_dog",
    ) -> list[ObservationProduct]:
        """
        Generate ground-inspection pest and disease detection products.

        Ground inspection has limited spatial coverage but higher diagnostic
        value than aerial observations. It can produce false positives and false
        negatives.
        """
        p = self.params
        ridge_ids = list(ridge_ids)
        if len(ridge_ids) > p.ground_inspection_max_ridges:
            ridge_ids = ridge_ids[: p.ground_inspection_max_ridges]

        pest_values: dict[int, dict[str, float | bool]] = {}
        disease_values: dict[int, dict[str, float | bool]] = {}

        for r in ridge_ids:
            truth = truth_by_ridge.get(r)
            if truth is None:
                continue

            pest_present_truth = truth.insect_pressure >= p.pest_presence_pressure_threshold
            disease_present_truth = truth.disease_pressure >= p.disease_presence_pressure_threshold

            pest_detected, pest_conf = self._binary_detection(
                present=pest_present_truth,
                sensitivity=p.pest_detection_sensitivity,
                specificity=p.pest_detection_specificity,
            )
            disease_detected, disease_conf = self._binary_detection(
                present=disease_present_truth,
                sensitivity=p.disease_detection_sensitivity,
                specificity=p.disease_detection_specificity,
            )

            pest_values[r] = {
                "pest_present": pest_detected,
                "confidence": round(pest_conf, 3),
            }
            disease_values[r] = {
                "disease_present": disease_detected,
                "confidence": round(disease_conf, 3),
            }

        products = [
            self._product(
                product_type=ObservationProductType.PEST_DETECTION,
                modality=ObservationModality.GROUND_INSPECTION_RGB,
                asset_id=asset_id,
                observed_day=day,
                available_day=day + timedelta(days=p.ground_inspection_latency_days),
                ridge_ids=ridge_ids,
                values=pest_values,
                uncertainty={
                    "sensitivity": p.pest_detection_sensitivity,
                    "specificity": p.pest_detection_specificity,
                },
                tags=[],
            ),
            self._product(
                product_type=ObservationProductType.DISEASE_DETECTION,
                modality=ObservationModality.GROUND_INSPECTION_RGB,
                asset_id=asset_id,
                observed_day=day,
                available_day=day + timedelta(days=p.ground_inspection_latency_days),
                ridge_ids=ridge_ids,
                values=disease_values,
                uncertainty={
                    "sensitivity": p.disease_detection_sensitivity,
                    "specificity": p.disease_detection_specificity,
                },
                tags=[],
            ),
        ]
        return products

    def observe_spad(
        self,
        day: date,
        truth_by_ridge: Mapping[int, HiddenRidgeTruth],
        ridge_ids: Sequence[int],
        asset_id: str = "spad_meter",
    ) -> ObservationProduct:
        """
        Generate sparse manual SPAD measurements.

        SPAD is represented as a high-confidence but sparse point measurement
        related to canopy vigor and nutrient state.
        """
        p = self.params
        values: dict[int, float] = {}
        for r in ridge_ids:
            truth = truth_by_ridge.get(r)
            if truth is None:
                continue
            ndvi_term = self._clip((truth.ndvi_proxy - 0.2) / 0.65, 0.0, 1.0)
            nutrient_term = self._clip(truth.nutrient_index, 0.0, 1.0)
            spad_norm = p.spad_ndvi_weight * ndvi_term + p.spad_nutrient_weight * nutrient_term
            spad = p.spad_min + (p.spad_max - p.spad_min) * spad_norm
            spad += self._normal(0.0, p.spad_noise_std)
            values[r] = round(self._clip(spad, p.spad_min, p.spad_max), 1)

        return self._product(
            product_type=ObservationProductType.SPAD_POINT,
            modality=ObservationModality.MANUAL_SPAD,
            asset_id=asset_id,
            observed_day=day,
            available_day=day + timedelta(days=p.manual_spad_latency_days),
            ridge_ids=list(ridge_ids),
            values=values,
            uncertainty={"spad_std": p.spad_noise_std},
            tags=["manual_sparse_observation"],
        )

    def _soil_sensor_product(
        self,
        day: date,
        asset: SensorAsset,
        truth: HiddenRidgeTruth,
    ) -> ObservationProduct:
        p = self.params
        r = truth.ridge_id
        vwc = self._clip(truth.top_vwc + self._normal(0.0, p.soil_vwc_noise_std), 0.0, 0.6)
        temp = truth.top_temp_c + self._normal(0.0, p.soil_temp_noise_std_c)

        return self._product(
            product_type=ObservationProductType.SOIL_MOISTURE_POINT,
            modality=ObservationModality.SOIL_SENSOR,
            asset_id=asset.asset_id,
            observed_day=day,
            available_day=day + timedelta(days=p.fixed_sensor_latency_days),
            ridge_ids=[r],
            values={
                r: {
                    "top_vwc": round(vwc, 4),
                    "top_temp_c": round(temp, 2),
                }
            },
            uncertainty={
                "vwc_std": p.soil_vwc_noise_std,
                "temp_std_c": p.soil_temp_noise_std_c,
                "support_radius_ridges": float(asset.support_radius_ridges),
            },
            tags=["fixed_sensor"],
        )

    def _canopy_index_product(
        self,
        day: date,
        asset: SensorAsset,
        truth: HiddenRidgeTruth,
    ) -> ObservationProduct:
        p = self.params
        r = truth.ridge_id
        obs = self._clip(truth.ndvi_proxy + self._normal(0.0, p.canopy_index_noise_std), 0.0, 1.0)

        return self._product(
            product_type=ObservationProductType.CANOPY_INDEX_POINT,
            modality=ObservationModality.CANOPY_INDEX_SENSOR,
            asset_id=asset.asset_id,
            observed_day=day,
            available_day=day + timedelta(days=p.fixed_sensor_latency_days),
            ridge_ids=[r],
            values={r: round(obs, 3)},
            uncertainty={
                "index_std": p.canopy_index_noise_std,
                "support_radius_ridges": float(asset.support_radius_ridges),
            },
            tags=["fixed_sensor"],
        )

    def _ndvi_anomaly_tags(self, values: Mapping[int, float]) -> list[str]:
        if not values:
            return []
        mean_val = sum(values.values()) / len(values)
        low = [r for r, v in values.items() if mean_val - v >= self.params.ndvi_anomaly_drop_threshold]
        return ["ndvi_anomaly_detected"] if low else []

    def _thermal_anomaly_tags(self, values: Mapping[int, float]) -> list[str]:
        if not values:
            return []
        mean_val = sum(values.values()) / len(values)
        high = [r for r, v in values.items() if v - mean_val >= self.params.thermal_anomaly_threshold_c]
        return ["thermal_anomaly_detected"] if high else []

    def _binary_detection(
        self,
        present: bool,
        sensitivity: float,
        specificity: float,
    ) -> tuple[bool, float]:
        if present:
            detected = self.rng.random() < sensitivity
            confidence = sensitivity if detected else (1.0 - sensitivity)
        else:
            detected = self.rng.random() > specificity
            confidence = (1.0 - specificity) if detected else specificity

        # Add small jitter to avoid constant confidence values.
        confidence = self._clip(confidence + self._normal(0.0, 0.04), 0.0, 1.0)
        return detected, confidence

    def _product(
        self,
        product_type: ObservationProductType,
        modality: ObservationModality,
        asset_id: str,
        observed_day: date,
        available_day: date,
        ridge_ids: list[int],
        values: dict[int, Any],
        uncertainty: dict[str, float],
        tags: list[str],
    ) -> ObservationProduct:
        self._product_counter += 1
        product_id = f"obs-{self._product_counter:06d}"
        return ObservationProduct(
            product_id=product_id,
            product_type=product_type,
            modality=modality,
            asset_id=asset_id,
            observed_day=observed_day,
            available_day=available_day,
            ridge_ids=ridge_ids,
            values=values,
            uncertainty=uncertainty,
            tags=tags,
        )

    def _normal(self, mean: float, std: float) -> float:
        return self.rng.gauss(mean, std)

    @staticmethod
    def _clip(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))


def default_observation_assets() -> list[SensorAsset]:
    """
    Default asset layout matching the planned farm sensing inventory.

    The exact ridge placements are scenario parameters. This function provides
    a simple spread across 64 ridges for initial testing.
    """
    soil_ridges = [4, 14, 24, 34, 44, 54]
    canopy_ridges = [8, 18, 28, 38, 48, 58]

    assets: list[SensorAsset] = []
    for idx, ridge_id in enumerate(soil_ridges, start=1):
        assets.append(
            SensorAsset(
                asset_id=f"soil_sensor_{idx}",
                modality=ObservationModality.SOIL_SENSOR,
                fixed_ridge_id=ridge_id,
                support_radius_ridges=1,
            )
        )

    for idx, ridge_id in enumerate(canopy_ridges, start=1):
        assets.append(
            SensorAsset(
                asset_id=f"canopy_index_sensor_{idx}",
                modality=ObservationModality.CANOPY_INDEX_SENSOR,
                fixed_ridge_id=ridge_id,
                support_radius_ridges=1,
            )
        )

    assets.extend([
        SensorAsset(asset_id="mavic3m", modality=ObservationModality.UAV_MULTISPECTRAL),
        SensorAsset(asset_id="matrice4t", modality=ObservationModality.UAV_THERMAL),
        SensorAsset(asset_id="zenmuse_l2", modality=ObservationModality.UAV_LIDAR),
        SensorAsset(asset_id="robot_dog_1", modality=ObservationModality.GROUND_INSPECTION_RGB),
        SensorAsset(asset_id="spad_meter", modality=ObservationModality.MANUAL_SPAD),
    ])
    return assets


if __name__ == "__main__":
    obs = ObservationModel(assets=default_observation_assets())

    truth = {
        r: HiddenRidgeTruth(
            ridge_id=r,
            top_vwc=0.24,
            root_vwc=0.22,
            top_temp_c=16.0,
            ndvi_proxy=0.75 if r != 10 else 0.58,
            lai=3.2 if r != 10 else 2.0,
            canopy_cover=0.85,
            canopy_temp_c=25.0 if r != 10 else 29.0,
            insect_pressure=0.20 if r != 10 else 0.55,
            disease_pressure=0.10,
            nutrient_index=0.90,
        )
        for r in range(16)
    }

    day = date(2026, 7, 10)
    products = []
    products.extend(obs.observe_fixed_sensors(day, truth))
    products.append(obs.observe_uav_multispectral(day, truth, list(range(16))))
    products.append(obs.observe_uav_thermal(day, truth, list(range(16))))
    products.extend(obs.observe_ground_inspection(day, truth, [8, 9, 10, 11], asset_id="robot_dog_1"))
    products.append(obs.observe_spad(day, truth, [10]))

    for product in products:
        print(product)
