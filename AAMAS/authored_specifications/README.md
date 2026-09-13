# Author-defined executable specifications

Regenerate with `python AAMAS/build_authored_specs.py`. The inventory binds three
primary processes, three 12-hour freshness alternatives, the 2-/3-/4-agent teams,
two role refinements and two refined Wet-June processes. These are author-defined
contracts, never independently confirmed. Schema freezing records exact choices;
it does not attest that scientific or live-execution gates passed. Genuine
professor approval must bind these artifacts and the protocol after validation.

## Scientific choices and their basis

| Choice | Executable definition / source | Interpretation and limitation |
| --- | --- | --- |
| Native work | `farm_catalog.compile_native_petri_net`; original occurrence IDs, tool arguments, ownership and precedence | Preserve native requested work. Reads, clock changes, refills and configuration earn no crop-progress credit. |
| Planting | Regional mean surface VWC in [0.20, 0.35], inclusive; native tractor check | Native mechanic. A sensor estimate may disagree with actual field readiness. |
| Disease | Any pressure >=0.20 in the declared exact region; crop-health/ridge observations | Native diagnostic threshold. A positive whole-field aggregate cannot establish disease in a smaller treatment region. |
| Traffic and spraying | Native traffic flag; current rain ==0 and wind <8 m/s | Native mechanics; local soil probes provide a conservative traffic proxy. No forecast is silently substituted for current conditions. |
| Drought | Observed regional surface mean <0.20; evaluator comparator >=50% roots below0.18 | Explicit imperfect proxy, not an agronomic validation claim. Native root values are hidden from agents. Scenario calibration remains failed. |
| Harvest | All requested ridges mature, maximum grain moisture <=18%, dry weather and trafficable field | Native acceptance plus declared evidence obligation. Unknown/incomplete ridge coverage remains unknown. Numeric observations retain units. |
| Calendar phases | UTC windows in each JSON; establishment, monitoring, disease, reproduction, harvest | Author-defined calendar partition informed by the scripted development workflow. It is not an oracle for crop stage. Three cultivar harvest and storage share each cultivar's window. |
| Branch | Authoritative spray availability at disease-phase entry, with explicit complement | Both branches preserve the eventual treatment. A closed window requires reobservation/deferral under policy, not an invented yield-causation branch. |
| Policy | Exhaustive/disjoint ternary tables, one operations policy per high-impact phase | Execution requires every prerequisite true and deadline open. Unknowns never become permission through a scalar score. |
| Acceptance | Exact requested arguments and region; request-bound native receipt | An accepted action need not be beneficial or information-supported. Failed attempts remain recorded. |
| Negative obligations | Out-of-window extra spraying classified under the native weather predicate | Other unmatched actions remain unclassified unless an explicit obligation covers them. The list is not claimed exhaustive for agronomy. |
| Causal paths | Field intelligence to operations; refined legal routes for larger teams | Information provenance, not physical causation. Repeat unchanged snapshots do not alone supersede evidence; an actual same-region value change does. |
| Faults | Frozen phase/route/send ordinal; mixed requires sends 1–3, reorder sends 1–2 | No messages are manufactured. Validity delays are 12/48 hours after the actual send, compared to actual selected-evidence expiry. Missing expiry is unassessable; absent qualifying sends are inactive ITT assignments. |
| Weights | Equal required-write shares within a phase, equal phase budgets | Author modeling choice. Empty phases have unavailable denominators, not perfect scores. Scalar alpha 0.25/0.5/0.75 remains secondary to the components. |

The JSON fact registry names simulator and observation sources; runtime facts
retain source event, scope, observation time, learning time, expiry and version.
No knowledge graph or graph-recall component is introduced.

## Prespecified sensitivity and validation boundary

Each `.freshness12h.process.json` changes only the maximum admissible evidence
age from 24 to 12 hours and records that choice. Observation values, timestamps,
native expiry, scope, outcomes and weights are unchanged. These alternatives are
frozen before confirmation and require the same genuine review as the primaries.
Saved-trace evaluation must retain their exact digests in the annotation plan.

Native reference execution is a mechanics/trace check; it need not achieve perfect
causal conformance when its broad or superseded evidence fails this stricter
contract. Investigate the recorded component failures. Do not tune the normative
specification to force the reference to score 1. All development outcomes remain
exploratory. Calibration worlds 20–24 and live worlds 30–31 remain unused until the
release candidate and alternatives have passed development checks.
