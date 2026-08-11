# Skill Library: hb_wetjune_disease_recheck_after_fungicide

Source L3: `scenario_full_season_hb_wetjune_disease_recheck_after_fungicide`

## establishment_planting
- source_l2: `scenario_l2_hb_wetjune_recheck_establishment_planting`
- farming_group: `establishment`
- task_type: `planting`
- belief: Start from field opening state; verify weather, soil, inventory and equipment before field prep and planting.
- evidence_chain: ['weather', 'forecast', 'soil sensors', 'inventory', 'tractor status', 'post-planting overview']
- constraints: ['complete prep/base fertilizer/ridge formation before planting', 'use visible seed plan and spacing']
- success_checks: ['planted ridges visible', 'inventory and field state updated']

## mid_fungicide_20_43
- source_l2: `scenario_l2_hb_wetjune_recheck_mid_fungicide_20_43`
- farming_group: `management`
- task_type: `fungicide`
- belief: Use observation and ground evidence before disease-control fungicide application; do not treat only from NDVI or a guessed target.
- evidence_chain: ['weather', 'forecast', 'soil sensors', 'canopy sensors', 'range state', 'drone survey', 'ground confirmation', 'inventory/status']
- constraints: ['target source cause must match action type', 'wait/recheck before action when the window is not immediately ready', 'do not blindly copy ridges/rates to another L3']
- success_checks: ['target range treated', 'post-action range state rechecked', 'resource use visible']

## r5_fungicide_20_43
- source_l2: `scenario_l2_hb_wetjune_recheck_r5_fungicide_20_43`
- farming_group: `management`
- task_type: `fungicide`
- belief: Use observation and ground evidence before disease-control fungicide application; do not treat only from NDVI or a guessed target.
- evidence_chain: ['weather', 'forecast', 'soil sensors', 'canopy sensors', 'range state', 'drone survey', 'ground confirmation', 'inventory/status']
- constraints: ['target source cause must match action type', 'wait/recheck before action when the window is not immediately ready', 'do not blindly copy ridges/rates to another L3']
- success_checks: ['target range treated', 'post-action range state rechecked', 'resource use visible']

## harvest_window
- source_l2: `scenario_l2_hb_wetjune_recheck_harvest_window`
- farming_group: `harvest`
- task_type: `harvest_postharvest`
- belief: After R8, harvest timing should be based on maturity, grain moisture, weather, trafficability and postharvest resources.
- evidence_chain: ['weather', 'forecast', 'farm overview', 'soil sensors', 'range state', 'grain moisture', 'storage/dryer state']
- constraints: ['do not harvest before maturity/window', 'do not store wet grain without drying when drying is required', 'harvest -> unload -> dry/store order']
- success_checks: ['harvested ridges visible', 'grain unloaded', 'grain stored or dried then stored']
