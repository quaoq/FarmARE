"""Curated Tangyan5 base full-season inputs.

Edit this file when the Tangyan5 actual-management scenario needs updated
field facts. Runtime scenario code should not depend on a local Excel file.
"""
from __future__ import annotations


TANGYAN5_BASE_WEATHER = {
    "source": "weather_engine",
    "seed": 5,
    "note": (
        "Default scenario forcing uses WeatherGenerator/default_harbin_soybean_config. "
        "The former Excel weather table is no longer a runtime dependency."
    ),
}


TANGYAN5_BASE_PLOTS = {
    "Nor_HH43": {
        "plot_id": "Nor_HH43",
        "variety": "黑河43",
        "seed_type": "HEIHE43",
        "planting_date": "2025-05-19",
        "emergence_date": "2025-05-28",
        "maturity_date": "2025-08-18",
        "harvest_date": "2025-09-15",
        "plot_area_m2": 35.1,
        "plot_yield_kg": 14.12,
        "seed_density_plants_ha": 224360.0,
        "seed_depth_cm": 4.0,
        "seed_spacing_cm": 8.1,
        "base_fertilizer_name": "高浓度硫酸钾复合肥",
        "base_fertilizer_date": "2025-05-19",
        "base_n_kg_ha": 18.0,
        "base_p_kg_ha": 27.0,
        "base_k_kg_ha": 22.5,
        "topdress_name": "金辉促根",
        "topdress_date": "2025-06-27",
        "topdress_n_kg_ha": 3.45,
        "topdress_p_kg_ha": 3.01125,
        "topdress_k_kg_ha": 2.86395,
        "nutrient_index": 0.75,
    },
}
