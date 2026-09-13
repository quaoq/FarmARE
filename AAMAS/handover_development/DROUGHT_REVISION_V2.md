# Prospective development revision: drought_rootzone_v2

Development worlds only; no release or confirmation status.

Daily native telemetry on world 0 shows a 1.45 m effective root layer on the
nominal fast-draining ridges 20–43. Original pre-intervention root VWC is 0.2561;
a 38-day rain-free candidate still gives 0.2400, above the unchanged native
0.18 stress threshold. Daily synchronization and mm / m3/m3 conversions agree.
Canopy cumulative stress includes disease and must not be called drought stress.

This separately named candidate represents a restricted 0.4 m effective root
zone on ridges 20–43, using the soil engine's existing default depth. This is an
explicit modeling assumption, not a site measurement. The fixed R5 reference
irrigation is 3.2 hours (16 mm on 24/64 ridges = 6 field-average mm, the existing
quota). All water still enters native infiltration and redistribution; all
native action checks remain active. Weather, yield rewards and stress thresholds
are unchanged. Seven-day bounded rainy-harvest handling remains a separately
labeled workflow variant. Neither modification validates the historical scenario.

Screen on development world 0 first. Retain a failed result. Do not consume
confirmation worlds 20–24 until this revision and all protocol alternatives are
frozen. Acceptance remains complete paired outcomes, accepted intervention,
>=50% target-ridge stress and >=1% omission loss in every confirmation pair.

Development result: V2 produces 100% stressed ridges at the target, but both
arms remain R7 at the scripted harvest date. This candidate fails completeness.
Next declared development alternative, `drought_rootzone_v3`, uses a 0.6 m
restricted root zone to test whether less severe depletion preserves the native
maturity horizon. Dose, quota, native thresholds and reward remain unchanged.

### Prospective development amendment: bounded maturity handling

The v2/v3 development pairs retain every native rejection. Both fail complete
harvest because target ridges remain at R7 on the scripted harvest date. The
next development check uses the same v2 soil, irrigation, rewards and stress
thresholds, with a separately named `bounded_rain_and_maturity_retry_v2`
reference workflow. It permits at most 14 one-day waits across the whole season,
and only after native rain or immature-ridge rejection; every attempt is saved.
This is a workflow sensitivity check on development world 0, not a released
scenario or a confirmation run. No confirmation world has been used. A passing
pair would still require a versioned runtime specification, native equivalence,
five fresh confirmation pairs and genuine professor approval.

### Prospective development amendment: grain-moisture rejection

The bounded maturity/rain workflow reached R8 after 5–6 waiting days, then native
harvest rejected grain moisture above 18%. A third reference-workflow variant
adds this specific rejection to the permitted waiting conditions, retaining the
same whole-season 14-day maximum. It does not change the native 18% acceptance
threshold, crop rewards, soil parameters or irrigation dose. Run on development
world 0 before drawing any conclusion; preserve the earlier incomplete pairs.

### Prospective development amendment: three-week harvest sensitivity

The 14-day moisture-aware check ended with target grain moisture about 23.12%
(control) and 24.78% (omission); both remain incomplete. Native R8 grain starts
at 30%, then loses moisture daily through the existing solar/wind drydown model
and gains moisture during rain. The prior maturity delay used 5–6 days of that
same 14-day budget. To measure a complete paired outcome if the native window
allows it, declare **one 21-day whole-season cap** on development world 0,
`bounded_three_week_harvest_retry_v4`, before execution. This is an author-defined
workflow sensitivity, not a measured agronomic recommendation.

Use exactly `drought_rootzone_v2`, its 3.2-hour intervention and existing
6 field-mm seasonal quota; keep weather, soil, stress thresholds, native
18% harvest limit and all yield calculations unchanged. Retry only rain,
immaturity and excessive grain moisture; preserve all failures. Do not extend
this cap again to rescue this development pair. A completed pair with less than
1% omission loss still fails screening. This amendment consumes no confirmation
worlds, establishes no release eligibility, and does not repair earlier results.

World 0 result on `6fd60f7`: both arms completed native harvest/storage, with
19 waiting days for control and 21 for omission. Yields were 7444.14 and 7364.80
kg respectively (1.0658% omission loss), with accepted irrigation and 100%
target-ridge stress. The pair passes the unchanged per-world screen, but one
development pair does not satisfy the multi-world engineering or release gate.

Next, prospectively screen **development worlds 1–4** using these exact settings,
including the 21-day cap. Do not rerun world 0 or select a subset of successful
worlds. Combine the five existing/new pairs only as a clearly derived development
summary with source hashes. Fresh confirmation worlds remain unused. Failure in
any world remains a failure; no parameter or cap amendment is authorized by this
screening declaration.

**Five-world result: failed (1/5 passing pairs).** Worlds 1–4 completed both
harvest/storage outcomes but had 0% target-ridge stress. Omission losses were
-0.0834%, -0.4234%, 0%, and -0.0262%, respectively. World 3 also had an inactive
intervention. The world 0 result therefore does not generalize across the
declared development cohort. The candidate remains unreleased. No confirmation
world was consumed and no larger harvest cap is introduced. The combined
development screen is regenerated from both unchanged source reports by
`AAMAS/analyze_handover_development.py`.
