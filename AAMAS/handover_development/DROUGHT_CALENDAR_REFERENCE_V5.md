# Prospective development amendment: authored harvest calendar

Before running this amendment, the larger-pulse/21-day reference screen remains
failed overall (4/5). No previous measurement is revised. This is a new reference
workflow, not confirmation and not an additional irrigation-dose search.

The authored drought specification already defines harvest as September 1 through
November 2 (exclusive). The previous reference starts harvest September 11 and
exhausts 21 waiting days October 2. World 0 becomes mature only during that
interval, leaving insufficient dry-down time. Thus the experimental reference
can stop while the prespecified domain still permits harvest. This discrepancy
was found by inspecting the recorded rejection dates, not by trying later caps.

Test `authored_harvest_calendar_v5`, bounded by the **existing specification's
November 2 deadline**, on every development world 0–4. Keep drought_pulse_v4
physics, weather, 25 mm dose, quotas and all other management unchanged. Retry
the exact rejected harvest once per simulated day only for native rain,
immaturity or high-grain-moisture rejection. Retain every rejection; stop before
the deadline. Do not relax the native 18% moisture threshold or change yield
formulas. The acceptance screen remains accepted irrigation, >=50% stressed
target ridges, complete paired outcomes and >=1% omission loss in every pair.

This explicitly replaces the reference's relative waiting cap with the authored
absolute calendar bound. Results must be reported as a separate development
workflow and reviewed as a modeling choice. The earlier statement that v4
included no further cap search remains true for v4; this amendment addresses
its inconsistency with the already authored domain window. No new deadline is
chosen from outcomes. Confirmation worlds remain unused until selection/freeze.

Result: all five development pairs pass, with omission losses 1.4350%, 1.4419%,
1.9025%, 2.6890% and 1.0921%. No native thresholds or dose changes occurred.
The first distributed-reference integration failed because transition-level
windows were absent; the implementation now obtains deadlines from the authored
phase metadata. The corrected world-0 distributed and native references both
complete harvest/storage at 7686.37 kg, with identical exogenous-world hashes
and zero yield difference. Both integration attempts and every native rejection
are preserved. This is development evidence; fresh confirmation is pending.

Before confirmation, run the exact selected process (including its explicit
scripted-reference metadata) on **all reserved development worlds 0–9**. This
prospectively adds worlds 5–9 once, without changing the scenario, deadline,
dose or screening thresholds. Preserve all ten pairs regardless of outcome.
Confirmation worlds 20–24 remain untouched during this development screen.
