"""Exact, prospectively authored binding for held-out drought confirmation.

A binding declares a test; it does not assert that the test passed or supply
professor approval. Historical same-world sensitivity reports keep their route.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DroughtConfirmationBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["dcore_drought_confirmation_binding_v1"] = (
        "dcore_drought_confirmation_binding_v1"
    )
    scenario_id: Literal["farm_disease_drought"] = "farm_disease_drought"
    scenario_revision: (
        Literal[
            "drought_rootzone_v2",
            "drought_rootzone_v3",
            "drought_pulse_v4",
            "drought_water_balance_v5",
        ]
        | None
    ) = None
    calibration_candidate: bool = False
    harvest_retry_days: int = Field(default=0, ge=0, le=21)
    retry_immaturity: bool = False
    retry_wet_grain: bool = False
    retry_wet_soil: bool = False
    harvest_deadline_world_time: float | None = Field(default=None, gt=0)
    harvest_opening_world_time: float | None = Field(default=None, gt=0)
    development_worlds: list[int]
    confirmation_worlds: list[int]
    live_smoke_worlds: list[int]
    study_worlds: list[int]
    process_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    min_shortfall: float = Field(default=0.01, ge=0.01, le=1)
    min_stressed_fraction: float = Field(default=0.5, ge=0.5, le=1)

    @model_validator(mode="after")
    def validate_design(self):
        cohorts = [
            self.development_worlds,
            self.confirmation_worlds,
            self.live_smoke_worlds,
            self.study_worlds,
        ]
        if any(not c or len(c) != len(set(c)) for c in cohorts):
            raise ValueError("cohorts must be nonempty and contain unique worlds")
        if len(self.confirmation_worlds) < 5:
            raise ValueError("confirmation requires at least five worlds")
        if sum(map(len, cohorts)) != len(set().union(*map(set, cohorts))):
            raise ValueError(
                "development, confirmation, live smoke and study cohorts must be disjoint"
            )
        if (
            self.scenario_revision in {"drought_pulse_v4", "drought_water_balance_v5"}
            and not self.calibration_candidate
        ):
            raise ValueError(
                f"{self.scenario_revision} requires the declared candidate weather"
            )
        cap = (
            21
            if self.retry_immaturity and self.retry_wet_grain
            else (14 if self.retry_immaturity or self.retry_wet_grain else 7)
        )
        if self.harvest_retry_days > cap:
            raise ValueError("reference workflow exceeds the declared harvest cap")
        if self.harvest_deadline_world_time is not None and (
            self.harvest_retry_days
            or not (self.retry_immaturity and self.retry_wet_grain)
        ):
            raise ValueError(
                "calendar reference requires no relative cap and all declared harvest rejection types"
            )
        if self.harvest_opening_world_time is not None and (
            self.harvest_deadline_world_time is None
            or self.harvest_opening_world_time >= self.harvest_deadline_world_time
            or not self.retry_wet_soil
        ):
            raise ValueError(
                "harvest opening requires wet-soil recovery and a later bounded deadline"
            )
        return self

    def verify_current_source(self):
        from are.simulation.distributed.experiments import execution_source_digest

        if self.execution_source_digest != execution_source_digest():
            raise ValueError(
                "confirmation execution source mismatch; a revision needs a new freeze and unused cohort"
            )
        protocol = Path(__file__).with_name("EXPERIMENT_PROTOCOL.md")
        if self.protocol_digest != hashlib.sha256(protocol.read_bytes()).hexdigest():
            raise ValueError("confirmation protocol digest mismatch")

    def verify_plan(self, plan):
        expected = {
            "scenario_id": self.scenario_id,
            "scenario_revision": self.scenario_revision,
            "candidate": self.calibration_candidate,
            "harvest_retry_days": self.harvest_retry_days,
            "retry_immaturity": self.retry_immaturity,
            "retry_wet_grain": self.retry_wet_grain,
            "retry_wet_soil": self.retry_wet_soil,
            "world_seeds": self.confirmation_worlds,
            "min_shortfall": self.min_shortfall,
            "min_stressed_fraction": self.min_stressed_fraction,
            "harvest_deadline_world_time": self.harvest_deadline_world_time,
            "harvest_opening_world_time": self.harvest_opening_world_time,
            "reference_process_digest": self.process_digest
            if self.harvest_deadline_world_time is not None
            else None,
        }
        defaults = {
            "harvest_retry_days": 0,
            "retry_immaturity": False,
            "retry_wet_grain": False,
            "retry_wet_soil": False,
        }
        if any(plan.get(k, defaults.get(k)) != v for k, v in expected.items()):
            raise ValueError(
                "confirmation plan does not match the prospectively frozen design"
            )

    def verify_native_scenario(self, settings):
        if settings != {
            "scenario_revision": self.scenario_revision,
            "calibration_candidate": self.calibration_candidate,
        }:
            raise ValueError("confirmation native scenario variant mismatch")
