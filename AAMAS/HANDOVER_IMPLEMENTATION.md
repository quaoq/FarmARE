# Professor handover implementation record

Prospective scope approved by the author: all three scenarios; author-defined
specifications with actual professor sign-off; full manuscript with results
pending; at most $100 additional API usage ($40 development, $60 confirmation).
The full paper experiment suite and independent annotation are delegated.

Development worlds are 0–9, calibration confirmation worlds 20–24, final live
smoke worlds 30–31, and primary study worlds 100–109 (secondary 100–104).
Freeze each scenario revision before its confirmation cohort. Preserve failures
and old pilots. No unsuccessful scenario is silently removed or relabelled.

Implementation order: provider-request accounting and agent context/recovery;
authored specifications and explicit review route; scenario calibration and
fault/assignment accounting; offline checks; development runs; frozen
confirmation smoke; manuscript and portable package verification.

Acceptance is the gate table in the user's approved implementation plan:
complete final-revision regressions, native reference completion, unchanged
drought screening on all five confirmation worlds, policy coverage on both
live smoke worlds per scenario and one complete harvest/storage per scenario,
controller/fault compatibility, and a clean-checkout reproduction. A failed
gate is a blocker, not permission to change its threshold. Review packages and
experiment-authorized releases are distinct. Professor sign-off cannot be
manufactured by the implementation agent.

The first held-out attempt and prospective live revision are recorded in
`confirmation_v1/` and `confirmation_v2/`. Calibration 20–24 failed the all-pairs
gate. Six live assignments on 30–31 failed before provider invocation; the
repaired runtime uses newly declared 32–33. These amendments preserve the
original scope and failures rather than silently reusing confirmation worlds.
