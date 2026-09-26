# Adding an ARE scenario to D-CORE

D-CORE is an analysis and intervention layer over a native ARE scenario. Keep
the native mechanics in the normal ARE scenario package. This package contains
only the catalogue entry, observation mapping, team design, authored process
specification, and validation needed to make those mechanics diagnosable.

This guide answers the collaborator questions about `world`, `scenario`, the
intermediate layer, and how tools are divided among agents.

## The conversion in one diagram

```text
native ARE scenario
  tools + initial state + time + physics + horizon + outcome
            |
            v
catalogue entry and FarmScenarioAdapter
  native receipts -> versioned facts with actual scope and validity
            |
            v
team specification + authored process specification
  owners/routes       obligations/prerequisites/windows/order
            |
            v
D-CORE runtime
  local prompts -> decisions -> receipts -> diagnostic packets
            |
            v
verified checkpoint -> untreated/repaired continuations -> reports
```

The authored specification does not perform a second simulation and must not
reimplement the scenario. It states what evidence a responsible actor needs at
a public decision and how that decision maps to an existing native tool.

## Vocabulary

- A **scenario** is the named task: its mechanics, registered tools, initial
  state, time model, horizon, and outcome.
- A **scenario revision** identifies an explicit mechanics or calibration
  version. Scientific changes require a new revision.
- A **world** is one seeded exogenous realization of that scenario. The same
  world is paired across treatments and is the statistical cluster.
- A **team** maps native tools and season-long responsibilities to actors and
  defines legal communication routes.
- An **authored transition** is a public decision obligation, such as treating
  an exact ridge region after confirmed disease or storing harvested grain.
- A **prerequisite** is the precise fact value, scope, freshness, recipient, and
  timing required for one authored transition occurrence.
- A **condition** changes the controller, communication, fault, or verification
  policy while retaining the scenario/world contract.
- A **checkpoint** is a hash-verified predecision state used for matched suffix
  execution.

## Recommended collaborator workflow

### 1. Build the scenario in ordinary ARE style

It is valid—and preferable—for collaborators to start with an original
FarmARE/ARE-style scenario. D-CORE binding can follow after the native scenario
works independently.

The native scenario must provide:

1. deterministic construction from a world seed;
2. stable tool names and typed arguments;
3. real execution receipts, including failures;
4. logical duration and resource effects for observations and management;
5. returned measurement coverage, not only the requested range;
6. a fixed horizon and a measurable outcome at that horizon; and
7. a scripted or otherwise inspectable reference workflow for engineering
   validation.

For a non-farm domain such as a smart office, these are still the right
interfaces: rooms and devices replace ridges and equipment, but scope, time,
ownership, communication, deadlines, and outcomes remain explicit.

Do not add D-CORE concepts inside the native physics merely to make diagnosis
easy. A native tool should expose facts a real actor could observe.

### 2. Add a catalogue entry

Register creation, horizon, native outcome extraction, and stable scenario
revision in `farm_catalog.py` or an equivalent domain catalogue. The catalogue
must be able to create a fresh environment from the manifest seeds.

For domains outside farming, add a domain adapter that implements the same
boundary rather than adding smart-office assumptions to `FarmScenarioAdapter`.

### 3. Map native receipts to facts

Extend the appropriate adapter with explicit mappings:

```text
native tool + returned fields
    -> fact key
    -> fact value
    -> actual measured scope
    -> acquisition time and validity
    -> source actor/version
```

Map from the receipt. If a robot was asked for rooms 1--10 but returned rooms
1--4, the evidence scope is 1--4. If a tool cannot observe `disease:confirmed`,
do not list it as a producer for that fact.

When one native call cannot cover a declared region, declare the fact's coverage
aggregation in the fact registry and execute the necessary native passes. D-CORE
may compose the regional version only after the returned component scopes cover
the target. Every pass, charge, elapsed-time effect and component version stays
in the trace and resource accounting; requested arguments never substitute for
measured coverage.

Opaque sensor IDs are resolved by native configuration. A sensor name alone is
not evidence that it covers the target region.

### 4. Define the team

For each native tool, declare exactly one legal owner. Facts may have several
declared observers, but executable tool ownership is exclusive. Then assign every
consequential obligation to exactly one responsible actor at decision time.

The default farm team uses:

| Actor | Owns | Season-long responsibility |
|---|---|---|
| `field_intelligence` | sensors, robot/drone inspection, evidence messages | acquire, refresh, interpret, and route scoped observations |
| `operations` | planting, treatment, irrigation, harvest, unload, dry, store | execute legal crop and postharvest operations and account for grain |

This split is one benchmark design. A valid new decomposition must satisfy:

- aggregate capability coverage;
- exclusive native-tool ownership;
- one responsible decision actor per obligation;
- a legal route when evidence and decision ownership differ;
- enough time for the route before the decision deadline; and
- no additional oracle facts when a role is refined into specialists.

Load the target `AgentTeamSpec` and `RoleRefinementSpec`, then call
`refine_process_for_team` from `are.simulation.distributed.teams`; it rejects
unknown owners, removed normative paths and nonconserving module budgets. Add a
3/4-agent adapter only when the native task has a meaningful specialization;
agent count alone is not a contribution.

### 5. Author the process specification

Add the scenario to `are/simulation/distributed/authored_specs.py`. For every
consequential operation occurrence, define:

- native action and responsible actor;
- argument and inclusive-scope matching;
- phase and acceptance window;
- preceding transition dependencies where the workflow truly requires them;
- information policy and exact prerequisites;
- fact value predicate;
- `scope_match: exact` or `scope_match: covers`;
- evidence validity and recipient;
- causal deadline and permitted response; and
- negative obligations, native failures, and outcome expectations.

Repeated operations require distinct occurrences or a rule that resolves the
occurrence uniquely. An ambiguous proposal stays unresolved and cannot trigger
a repair.

Use `exact` when evidence about a broader area could conceal local variation.
Use `covers` only when a broader measurement scientifically supports the
decision. Apply the same rule in mapping, diagnosis, guard, and evaluation.

### 6. Generate and review artifacts

```bash
UV_CACHE_DIR=/tmp/farmare-uv-cache \
  uv run --frozen python AAMAS/tools/build_authored_specs.py

UV_CACHE_DIR=/tmp/farmare-uv-cache \
  uv run --frozen are-dcore validate-spec \
  AAMAS/authored_specifications/SCENARIO.process.json
```

Do not edit generated JSON, DOT, or PNML to bypass the source. A specification
hash change invalidates an earlier professor approval. Record scientific choices
as simulator mechanics, published support, or explicit modeling assumptions.

### 7. Add tests before live execution

A scenario binding is incomplete until tests show:

1. a scripted reference reaches its key decisions and yields a native outcome;
2. every authored action resolves to the intended occurrence;
3. each observation producer creates the declared fact and actual scope;
4. wrong value, stale, undelivered, prompt-omitted, and wrong-scope evidence are
   distinguished;
5. a valid decision yields no repair;
6. selected repairs are owned, legal, timely, and produce the required version;
7. the no-change continuation verifies and reproduces the checkpoint suffix;
8. failed and partial runs remain in report denominators; and
9. future-only changes cannot affect prefix diagnosis.

Then use an engineering manifest on unused worlds. Never reuse a failed cohort
after code or specification changes.

## Current public farm bindings

The paper implementation uses three native L3 scenarios catalogued in
`farm_catalog.py`:

- Wet-June/recheck: treatment timing, recheck, harvest, and postharvest duties.
- Disease--Drought: regional disease treatment, water evidence and irrigation,
  harvest, and postharvest duties.
- Three-cultivar: cultivar-specific planting/treatment/irrigation, exact regional
  scope, cultivar harvests, and postharvest duties.

The generated process files are under `AAMAS/authored_specifications/`. Their
review manifests are the preregistration surface for transition weights,
numeric tolerances, time windows, guard overrides, and exogenous branches.
Engineering defaults remain unconfirmed until the review protocol is complete.

## What collaborators should send back

For each proposed scenario, send:

- native scenario path and registration name;
- scenario revision and world-seed behavior;
- tool inventory with arguments, owner candidates, duration, resource effects,
  and receipt examples;
- horizon and outcome fields;
- reference workflow;
- proposed decision obligations and responsible actors;
- proposed observation-to-fact mappings with scope and validity; and
- tests demonstrating native execution independent of D-CORE.

This is enough to review the native scenario and convert it to D-CORE without
forcing collaborators to understand replay, repair-study, or reporting code.
