# Farm D-CORE scenarios

This package contains the farm-only distributed benchmark adapters and the
internal transaction semantic fixture. Public D-CORE runs use the three native
L3 FarmARE scenarios catalogued in `farm_catalog.py`; no farm mechanics are
duplicated here.

The `specs/` manifests record the required human review state for each compiled
Petri oracle. Use `are-dcore validate-spec` to generate and validate JSON, DOT,
and PNML forms.

Each review manifest is also the preregistration surface for
`transition_weights`, per-argument `numeric_tolerances` keyed as
`TRANSITION_ID.ARGUMENT`, relative-day `time_windows`, complete
`guard_overrides`, and `exogenous_branches`. These fields remain explicitly
`unfrozen` until the two-specifier/adjudicator protocol is complete; engineering
defaults are never silently promoted to paper settings.
