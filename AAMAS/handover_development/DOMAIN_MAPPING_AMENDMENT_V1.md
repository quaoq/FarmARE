# Prospective observation-mapping repair

This development amendment applies to newly generated artifacts only. Original
pilot traces and their recorded fact values remain unchanged. Source hashes
distinguish the mapping versions. Professor approval and specification freeze
are still pending.

| Mapping | Executable meaning and source | Remaining interpretation limit |
| --- | --- | --- |
| `crop:grain_moisture` | Maximum reported moisture, in percent, over a completely covered requested ridge interval. Authoritative mapping uses the same maximum. `TractorApp.harvest` rejects any requested ridge above 18%. | Readings can expire; a low moisture value alone does not establish maturity, trafficability, fuel or bin capacity. |
| `crop:mature` | Every requested ridge has a maturity/harvest state; incomplete reports yield no aggregate fact. | A historical harvested flag is not permission to repeat a harvest. Native acceptance remains authoritative. |
| `soil:mean_vwc` | Probe-zone means weighted by their inclusive ridge counts when coverage is known. | Native probes are noisy top-layer measurements; they do not observe root-zone moisture. |
| `planting:soil_suitable` | Inferred moisture prerequisite: observed mean VWC within 0.20–0.35, inclusive. Authoritative truth uses the requested ridges' native mean. `TractorApp.plant_seeds` enforces these limits. | Zone means can differ from a smaller action batch. This fact does not include cultivar temperature, planting-window, equipment or inventory prerequisites. |
| Native irrigation scope | `start`/`end` are recognized alongside `start_ridge`/`end_ridge`; both endpoints are inclusive. | A scoped accepted irrigation receipt does not establish root-zone stress reduction or yield benefit. |

The native planting error message now reports its actual existing upper bound
(0.35); the simulator predicate and rewards are unchanged. The prior message
incorrectly said 0.30.

Regression counterexamples are in
`are/simulation/tests/distributed/test_domain_fact_boundaries.py`: a 13%/21%
batch whose mean conceals a wet ridge, partial crop reports, both inclusive
planting boundaries, unequal sensor zones, and both irrigation target regions.
All nine cases passed, alongside complete native scripted seasons for all three
scenarios. No empirical effect or independent validation is inferred from these
implementation checks.

The runtime lifecycle repair has three separate tests in `test_native_wait.py`:
requested scheduler delay, early wake from a new handoff, and permanent finish.
Scheduler waiting does not call the native farm-time advance operation.
