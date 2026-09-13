# Prospective development design: drought_pulse_v4

The combined V2 restricted-root / 38-day dry-spell screen failed all five worlds:
one incomplete pair and four complete omission losses of 0.77–0.93%. These
results remain unchanged. The native irrigation mechanism adds 5 mm per hour to
the requested ridges; water first enters the surface layer, then redistributes.
A 16 mm pulse therefore does not imply a 16 mm increase in root-zone storage.

This next, separately named candidate changes only the reference irrigation
resource/dose relative to that combined candidate: **25 mm on ridges 20–43**,
implemented as five hours and a **9.375 field-mm seasonal quota** (25 × 24/64).
The 0.4 m restricted root depth and 38-day July-10 dry spell remain explicit
author-defined environmental assumptions. The pulse is an author-defined
experimental treatment, not a claimed site measurement or a recommended rate.
No yield formula, stress threshold, harvest threshold, or 21-day retry cap changes.
All non-irrigation management limits and usage counters are retained.

Rationale: evaluate a larger, finite supplemental water allocation through the
existing infiltration/redistribution mechanism, which telemetry shows separates
surface and root moisture. This is exploratory scenario development informed by
the failed screen; it is not a prespecified confirmation or independent validation.
Screen every development world 0–4 with complete paired outcomes, accepted
intervention, >=50% target-ridge stress and >=1% omission loss. Preserve every
failure. No further dose search or harvest-cap extension is part of this design.
Confirmation worlds remain unused until an exact scenario and sensitivity
alternatives have been frozen. The runtime default remains the historical scenario.

Result: **failed overall (4/5 pairs pass)**. Worlds1–4 omission losses are
1.4419%,1.9025%,2.6890%,1.0921%, with complete paired outcomes and 100% target
stress. World0 has accepted irrigation and 100% stress, but both arms remain
incomplete; target grain moisture is21.711% after the fixed 21-day cap. Its
partial 5270.84 kg harvest is not a final yield. The candidate stays unreleased;
no further dose or harvest-cap search follows from this design.
