"""Tangyan5 soil profile derived from lab texture, with reference fallback."""

from are.simulation.scenarios.scenario_farm_world_fullseason.scenario_tangyan5_base_full_season import (
    heilongjiang_reference_soil_hydrology,
    tangyan5_soil_hydrology_from_lab_rows,
)


def test_empty_rows_yield_heilongjiang_reference():
    h = tangyan5_soil_hydrology_from_lab_rows([])
    assert h is not None
    assert h.source_rows == "heilongjiang_reference"
    assert h.wilting_point_vwc < h.field_capacity_vwc < h.saturation_vwc
    assert 0.20 <= h.initial_planting_soil_vwc <= 0.30


def test_texture_present_derives_physics_override():
    rows = [
        {
            "0-2um/%": "7.5",
            "2-20um/%": "41.86",
            "20-2000um/%": "50.64",
            "生育期": "播种",
        }
    ]
    h = tangyan5_soil_hydrology_from_lab_rows(rows)
    assert h is not None
    assert h.source_rows == "lab_texture_播种_mean"
    assert h.clay_pct == 7.5
    assert h.silt_pct == 41.86
    assert h.sand_pct == 50.64
    assert h.wilting_point_vwc == 0.132
    assert h.field_capacity_vwc == 0.3145
    assert h.saturation_vwc == 0.454
    assert h.initial_planting_soil_vwc == 0.2579


def test_heilongjiang_reference_export():
    h = heilongjiang_reference_soil_hydrology()
    assert h.clay_pct + h.silt_pct + h.sand_pct == 100.0
