"""Precommitted sampling and independent annotation of saved agent episodes.

The public packet is separate from the private source key. Explicit evaluator
and treatment labels are omitted; the communication format may remain inferable.
This tool does not certify that humans were independent or a plan was timely.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from are.simulation.distributed.diagnostic_validation import (
    decision_context,
    flat_evidence_baseline,
    snapshot_facts,
)
from are.simulation.distributed.models import (
    DistributedTrace,
    EventKind,
    FrozenModel,
    stable_digest,
)
from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5
from are.simulation.distributed.trace import validate_trace

LABELS = {
    "observation_gap",
    "handoff_omission",
    "transit_gap",
    "stale_information",
    "unsupported_claim",
    "uptake_error",
    "composition_error",
    "reasoning_error",
    "execution_error",
    "scope_error",
    "unlocalized_failure",
}


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


class StudyPlan(FrozenModel):
    schema_version: Literal["diagnostic_study_plan_v1"] = "diagnostic_study_plan_v1"
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    process_digests: tuple[str, ...]
    sensitivity_families: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    episodes: int = Field(default=60, ge=1, le=200)
    max_per_run: int = Field(default=2, ge=1, le=10)
    seed: int = 2027
    bootstrap_replicates: int = Field(default=2000, ge=100, le=10000)
    selection: Literal["balanced_scenario_condition_execution"] = (
        "balanced_scenario_condition_execution"
    )
    fixture_only: bool = False

    @model_validator(mode="after")
    def valid_digests(self):
        for values in (self.process_digests, *self.sensitivity_families.values()):
            if len(values) != len(set(values)) or any(
                len(v) != 64 or any(c not in "0123456789abcdef" for c in v)
                for v in values
            ):
                raise ValueError("process digests must be unique SHA-256 values")
        if not self.process_digests:
            raise ValueError("at least one frozen process digest is required")
        return self


def freeze_plan(manifest, processes, output, *, alternatives=(), **settings):
    specs = [FarmProcessSpecV5.model_validate_json(p.read_text()) for p in processes]
    if len({p.process_id for p in specs}) != len(specs):
        raise ValueError("provide exactly one base specification per process")
    alternative_specs = [
        FarmProcessSpecV5.model_validate_json(p.read_text()) for p in alternatives
    ]
    from are.simulation.distributed.review_policy import frozen_specification

    if not settings.get("fixture_only", False) and any(
        not frozen_specification(p) for p in (*specs, *alternative_specs)
    ):
        raise ValueError(
            "study plans require complete frozen specifications; paper episodes additionally require review approval"
        )
    plan = StudyPlan(
        manifest_sha256=file_digest(manifest),
        process_digests=tuple(p.digest for p in specs),
        sensitivity_families={
            p.process_id: tuple(
                a.digest for a in alternative_specs if a.process_id == p.process_id
            )
            for p in specs
        },
        **settings,
    )
    if any(a.process_id not in plan.sensitivity_families for a in alternative_specs):
        raise ValueError("an alternative specification has no planned base process")
    write_json(
        output,
        {
            "plan": plan.model_dump(mode="json"),
            "plan_digest": stable_digest(plan.model_dump(mode="json")),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "independent_expert_review_verified": False,
        },
    )
    return plan


def load_plan(path):
    envelope = json.loads(Path(path).read_text())
    plan = StudyPlan.model_validate(envelope["plan"])
    if envelope.get("plan_digest") != stable_digest(plan.model_dump(mode="json")):
        raise ValueError("study plan digest mismatch")
    return plan


def _public_episode(trace, process, decision, episode_id):
    target, policy = decision_context(process, trace, decision)
    relevant_keys = {
        key
        for g in policy.requirements
        for key in (g.fact_key, g.world_fact_key)
        if key
    }
    actions = [
        e
        for e in trace.events
        if e.kind == EventKind.ACTION and e.decision_context_id == decision.decision_id
    ]
    cutoff = max((e.logical_time for e in actions), default=target.logical_time)
    event_ids = {e.event_id: f"e{i:05d}" for i, e in enumerate(trace.events)}
    fact_ids = {f.version_id: f"f{i:05d}" for i, f in enumerate(trace.fact_versions)}
    messages = {
        m: f"m{i:05d}"
        for i, m in enumerate(
            dict.fromkeys(e.message_id for e in trace.events if e.message_id)
        )
    }
    aliases = {**event_ids, **fact_ids, **messages}
    actors = {actor: f"agent_{i + 1}" for i, actor in enumerate(trace.actors)}
    actors["world"] = "world"
    decisions = {d.decision_id: d for d in trace.decisions}
    event_times = {e.event_id: e.logical_time for e in trace.events}

    def clean(value):
        if isinstance(value, str):
            return actors.get(value, aliases.get(value, value))
        if isinstance(value, dict):
            return {clean(k): clean(v) for k, v in value.items()}
        if isinstance(value, (tuple, list)):
            return [clean(v) for v in value]
        return value

    observed_events = []
    allowed = {
        EventKind.OBSERVATION,
        EventKind.ACTION,
        EventKind.MESSAGE_SEND,
        EventKind.MESSAGE_RECEIVE,
        EventKind.WORLD_EFFECT,
    }
    for e in trace.events:
        if e.logical_time > cutoff or e.kind not in allowed:
            continue
        row = {
            "id": event_ids[e.event_id],
            "kind": e.kind.value,
            "actor": actors.get(e.actor_id, "world"),
            "world_time": e.world_time,
            "logical_time": e.logical_time,
            "action": e.action
            if e.kind in {EventKind.ACTION, EventKind.OBSERVATION}
            else None,
            "args": clean(e.args),
            "status": e.status if e.kind == EventKind.ACTION else None,
            "message_id": messages.get(e.message_id),
        }
        if e.kind in {EventKind.OBSERVATION, EventKind.ACTION}:
            row["evidence"] = clean(
                {
                    k: e.payload[k]
                    for k in (
                        "fact_key",
                        "value",
                        "scope",
                        "valid_until",
                        "result",
                        "error",
                    )
                    if k in e.payload
                }
            )
        if e.kind == EventKind.MESSAGE_SEND:
            proposal = next(
                (
                    decisions[p].proposed_intent
                    for p in e.causal_parents
                    if p in decisions
                ),
                None,
            )
            row["recipient"] = actors.get(e.payload.get("recipient"))
            row["text"] = proposal.text if proposal else None
            row["claims"] = clean(
                {
                    k: e.payload[k]
                    for k in (
                        "fact_keys",
                        "fact_versions",
                        "claim_values",
                        "claim_scopes",
                    )
                    if k in e.payload
                }
            )
        observed_events.append(row)
    local_ids = {f.version_id for f in snapshot_facts(trace, decision)}
    facts = [
        {
            "id": fact_ids[f.version_id],
            "fact_key": f.fact_key,
            "value": clean(f.value),
            "scope": clean(f.scope),
            "observed_at": f.world_time,
            "learned_at": f.learned_time,
            "valid_until": f.valid_until,
            "authoritative": f.authoritative,
            "available_to_deciding_actor": f.version_id in local_ids,
            "visible_to": clean(f.visible_to),
            "source_event": event_ids.get(f.source_event_id),
            "origin": fact_ids.get(f.origin_version_id),
        }
        for f in trace.fact_versions
        if f.fact_key in relevant_keys
        and f.world_time <= target.world_time
        and (f.learned_time is None or f.learned_time <= target.world_time)
        and event_times.get(f.source_event_id, float("inf")) <= target.logical_time
    ]
    return {
        "episode_id": episode_id,
        "decision": {
            "actor": actors[decision.actor_id],
            "world_time": target.world_time,
            "logical_time": target.logical_time,
            "action": decision.proposed_intent.action,
            "args": clean(decision.proposed_intent.args),
            "scope": clean(decision.proposed_intent.scope),
            "action_event_ids": [event_ids[e.event_id] for e in actions],
        },
        "reviewed_prerequisites": [
            {
                k: clean(v)
                for k, v in g.model_dump(mode="json").items()
                if k != "guard_id"
            }
            for g in policy.requirements
        ],
        "reviewed_action_constraints": [
            {
                "action": transition.action,
                "phase": transition.phase,
                "scope": clean(transition.scope),
                **{
                    k: clean(v)
                    for k, v in acceptance.model_dump(
                        mode="json", exclude={"arguments": {"__all__": {"weight"}}}
                    ).items()
                    if k != "transition_id"
                },
            }
            for transition in process.occurrence_net.transitions
            if transition.actor_id == decision.actor_id
            and transition.phase in policy.phases
            and fnmatch.fnmatchcase(
                decision.proposed_intent.action or "", transition.action
            )
            for acceptance in process.acceptance
            if acceptance.transition_id == transition.transition_id
        ],
        "events": observed_events,
        "facts": facts,
        "annotation": {
            "assessment": None,
            "failure_labels": [],
            "prerequisites_satisfied": None,
            "evidence_ids": [],
            "message_mappings": [],
            "rationale": "",
        },
    }


def sample_episodes(plan, results_dir, output):
    candidates, inventory, sources, seen = [], [], {}, set()
    for path in sorted(Path(results_dir).rglob("trace.dcore_trace_v5.json")):
        trace = DistributedTrace.model_validate_json(path.read_text())
        validate_trace(trace)
        digest = file_digest(path)
        if digest in seen:
            raise ValueError("duplicate trace bytes in the sampling pool")
        seen.add(digest)
        process_path = path.with_name("farm_process_spec_v5.json")
        process = FarmProcessSpecV5.model_validate_json(process_path.read_text())
        if process.digest not in plan.process_digests:
            raise ValueError(f"unplanned process in sampling pool: {path}")
        if trace.task_id not in {process.process_id, process.occurrence_net.net_id}:
            raise ValueError("trace/process mismatch")
        real_model = trace.configuration.get("controller_mode") == "llm"
        engineering_pilot = trace.configuration.get("engineering_llm_pilot", False)
        eligible = real_model if not plan.fixture_only else not real_model
        eligible = eligible and not engineering_pilot
        inventory.append(
            {
                "trace_sha256": digest,
                "path": str(path.resolve()),
                "eligible_controller": eligible,
                "candidate_decisions": 0,
            }
        )
        if not eligible:
            continue
        config = trace.configuration
        condition = tuple(
            str(config.get(k, "unknown"))
            for k in ("visibility_mode", "handoff_mode", "enforcement_mode", "fault")
        )
        cluster = f"{process.scenario_id}:{config.get('world_seed', 'unknown')}"
        sources[digest] = (trace, process, path, process_path, cluster)
        for decision in trace.decisions:
            _, policy = decision_context(process, trace, decision)
            if policy is None:
                continue
            writes = [
                e
                for e in trace.events
                if e.kind == EventKind.ACTION
                and e.decision_context_id == decision.decision_id
            ]
            status = (
                "error"
                if any(e.status == "error" for e in writes)
                else (
                    "accepted"
                    if any(e.status == "ok" and e.farmare_event_id for e in writes)
                    else "not_executed"
                )
            )
            rank = stable_digest((plan.seed, digest, decision.decision_id))
            candidates.append(
                ((process.scenario_id, condition, status), rank, digest, decision)
            )
            inventory[-1]["candidate_decisions"] += 1
    buckets = defaultdict(list)
    for row in sorted(candidates, key=lambda r: r[1]):
        buckets[row[0]].append(row)
    selected, per_run = [], Counter()
    keys = sorted(buckets, key=lambda key: stable_digest((plan.seed, key)))
    while len(selected) < plan.episodes:
        before = len(selected)
        for key in keys:
            while buckets[key] and per_run[buckets[key][0][2]] >= plan.max_per_run:
                buckets[key].pop(0)
            if buckets[key] and len(selected) < plan.episodes:
                row = buckets[key].pop(0)
                selected.append(row)
                per_run[row[2]] += 1
        if len(selected) == before:
            break
    if not selected:
        raise ValueError("sampling pool contains no eligible policy-action episodes")
    public, private = [], []
    for i, (_, _, digest, decision) in enumerate(sorted(selected, key=lambda r: r[1])):
        episode_id = f"episode_{i + 1:03d}"
        trace, process, path, process_path, cluster = sources[digest]
        public.append(_public_episode(trace, process, decision, episode_id))
        private.append(
            {
                "episode_id": episode_id,
                "trace": str(path.resolve()),
                "trace_sha256": digest,
                "process": str(process_path.resolve()),
                "process_sha256": file_digest(process_path),
                "decision_id": decision.decision_id,
                "cluster": cluster,
            }
        )
    packet = {
        "schema_version": "diagnostic_annotation_packet_v1",
        "episodes": public,
        "instructions": "Independently assess recorded evidence and reviewed prerequisites. Do not infer an agent's internal reasoning. Use insufficient_evidence when needed; multiple labels are allowed. Annotate received free-text claims with exact quotation and supporting source-fact IDs. Do not consult evaluator outputs, fault assignments, final yields, or another annotator. Format and execution behavior may reveal condition indirectly.",
    }
    packet_digest = stable_digest(packet)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "public").mkdir()
    (output / "private").mkdir()
    write_json(output / "public/episodes.json", packet)
    for annotator in ("annotator_a", "annotator_b", "adjudicator"):
        write_json(
            output / f"public/{annotator}.json",
            {
                "packet_digest": packet_digest,
                "annotator_id": annotator,
                "episodes": [
                    {"episode_id": e["episode_id"], **e["annotation"]} for e in public
                ],
            },
        )
    write_json(
        output / "private/manifest.json",
        {
            "plan": plan.model_dump(mode="json"),
            "plan_digest": stable_digest(plan.model_dump(mode="json")),
            "packet_digest": packet_digest,
            "pool_digest": stable_digest(inventory),
            "inventory": inventory,
            "selected": private,
            "requested": plan.episodes,
            "sampled": len(selected),
            "shortfall": plan.episodes - len(selected),
            "population_prevalence_estimable": False,
            "paper_eligible": False,
        },
    )
    return {
        "sampled": len(selected),
        "requested": plan.episodes,
        "shortfall": plan.episodes - len(selected),
        "packet_digest": packet_digest,
    }


class Annotation(FrozenModel):
    episode_id: str
    assessment: Literal["failure", "no_failure", "insufficient_evidence"]
    failure_labels: tuple[str, ...] = ()
    prerequisites_satisfied: bool | None = None
    evidence_ids: tuple[str, ...] = ()
    message_mappings: tuple[dict, ...] = ()
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def labels_consistent(self):
        if not set(self.failure_labels) <= LABELS or len(self.failure_labels) != len(
            set(self.failure_labels)
        ):
            raise ValueError("unknown or duplicate failure label")
        if (self.assessment == "failure") != bool(self.failure_labels):
            raise ValueError(
                "failure requires labels; other assessments must not contain labels"
            )
        return self


def load_annotations(path, packet):
    data = json.loads(Path(path).read_text())
    if data.get("packet_digest") != stable_digest(packet) or not data.get(
        "annotator_id"
    ):
        raise ValueError(
            "annotation packet digest or annotator identity missing/mismatched"
        )
    rows = [Annotation.model_validate(r) for r in data["episodes"]]
    expected = {e["episode_id"]: e for e in packet["episodes"]}
    if len(rows) != len(expected) or {r.episode_id for r in rows} != set(expected):
        raise ValueError("annotation must contain every episode exactly once")
    for row in rows:
        episode = expected[row.episode_id]
        events = {e["id"]: e for e in episode["events"]}
        facts = {f["id"]: f for f in episode["facts"]}
        if not set(row.evidence_ids) <= set(events) | set(facts):
            raise ValueError("annotation references unavailable evidence")
        for mapping in row.message_mappings:
            send = next(
                (
                    e
                    for e in events.values()
                    if e["kind"] == "message_send"
                    and e["message_id"] == mapping.get("message_id")
                ),
                None,
            )
            receives = [
                e
                for e in events.values()
                if e["kind"] == "message_receive"
                and e["message_id"] == mapping.get("message_id")
                and e["actor"] == episode["decision"]["actor"]
                and e["logical_time"] <= episode["decision"]["logical_time"]
            ]
            source = facts.get(mapping.get("source_fact_id"))
            quote = mapping.get("quote")
            if (
                send is None
                or not receives
                or source is None
                or source["authoritative"]
                or not quote
                or quote not in (send.get("text") or "")
            ):
                raise ValueError(
                    "free-text mapping requires received text, an exact quote, and observed source evidence"
                )
            if (
                source["observed_at"] > send["world_time"]
                or (
                    source["learned_at"] is not None
                    and source["learned_at"] > send["world_time"]
                )
                or send["actor"] not in source["visible_to"]
                or source.get("source_event") not in events
                or events[source["source_event"]]["logical_time"] > send["logical_time"]
            ):
                raise ValueError("mapped source was not available to the sender")
    return data["annotator_id"], {r.episode_id: r for r in rows}


def agreement(packet, left_path, right_path):
    left_id, left = load_annotations(left_path, packet)
    right_id, right = load_annotations(right_path, packet)
    if left_id == right_id:
        raise ValueError("two distinct annotator identities are required")
    n = len(left)
    observed = sum(left[k].assessment == right[k].assessment for k in left) / n
    a, b = (
        Counter(r.assessment for r in left.values()),
        Counter(r.assessment for r in right.values()),
    )
    expected = sum(a[k] * b[k] for k in a.keys() | b.keys()) / n**2
    return {
        "schema_version": "diagnostic_annotation_agreement_v1",
        "episodes": n,
        "assessment_agreement": observed,
        "cohen_kappa": (observed - expected) / (1 - expected) if expected < 1 else None,
        "label_set_agreement": sum(
            set(left[k].failure_labels) == set(right[k].failure_labels) for k in left
        )
        / n,
        "disagreements": [
            k
            for k in left
            if (
                left[k].assessment,
                set(left[k].failure_labels),
                left[k].prerequisites_satisfied,
                left[k].message_mappings,
            )
            != (
                right[k].assessment,
                set(right[k].failure_labels),
                right[k].prerequisites_satisfied,
                right[k].message_mappings,
            )
        ],
        "interpretation": "Agreement precedes adjudication; identities and procedural independence require human verification.",
    }


def predict_packet(packet_dir):
    from are.simulation.distributed.evaluator_v5 import evaluate_farm_dcore_v5

    root = Path(packet_dir)
    packet = json.loads((root / "public/episodes.json").read_text())
    manifest = json.loads((root / "private/manifest.json").read_text())
    if manifest["packet_digest"] != stable_digest(packet):
        raise ValueError("packet digest mismatch")
    cache, methods = {}, {"dcore": [], "flat_evidence": []}
    for source in manifest["selected"]:
        if (
            file_digest(source["trace"]) != source["trace_sha256"]
            or file_digest(source["process"]) != source["process_sha256"]
        ):
            raise ValueError("sampled source has changed")
        key = source["trace_sha256"]
        if key not in cache:
            trace = DistributedTrace.model_validate_json(
                Path(source["trace"]).read_text()
            )
            process = FarmProcessSpecV5.model_validate_json(
                Path(source["process"]).read_text()
            )
            cache[key] = (
                trace,
                evaluate_farm_dcore_v5(process, trace),
                flat_evidence_baseline(process, trace),
            )
        trace, report, baseline = cache[key]
        action_ids = {
            e.event_id
            for e in trace.events
            if e.decision_context_id == source["decision_id"]
            and e.kind == EventKind.ACTION
        }
        raw_labels = {
            r["primary"]
            for r in report["attribution"]
            if r.get("target_event_id") in action_ids and r.get("primary")
        }
        unverifiable = "unverifiable_handoff" in raw_labels
        labels = sorted(
            {
                r["primary"]
                for r in report["attribution"]
                if r.get("target_event_id") in action_ids and r.get("primary") in LABELS
            }
        )
        if any(e.status == "error" for e in trace.events if e.event_id in action_ids):
            labels = sorted(set(labels) | {"execution_error"})
        policy = next(
            (
                r
                for r in report["information_policy_conformance"]["details"]
                if r["decision_id"] == source["decision_id"]
            ),
            None,
        )
        known = policy is not None and all(
            v != "unknown" for v in policy["global_verdicts"].values()
        )
        if (
            known
            and not policy["global_conforming"]
            and not labels
            and not unverifiable
        ):
            labels = ["unlocalized_failure"]
        assessment = (
            "failure"
            if labels
            else (
                "no_failure"
                if known and policy["global_conforming"] and not unverifiable
                else "insufficient_evidence"
            )
        )
        methods["dcore"].append(
            {
                "episode_id": source["episode_id"],
                "assessment": assessment,
                "failure_labels": labels,
                "unverifiable_handoff": unverifiable,
            }
        )
        simple = next(
            r
            for r in baseline["decisions"]
            if r["decision_id"] == source["decision_id"]
        )
        error = any(r["status"] == "error" for r in simple["native_execution"])
        verdict = simple["prerequisites_satisfied"]
        methods["flat_evidence"].append(
            {
                "episode_id": source["episode_id"],
                "assessment": "failure"
                if verdict is False or error
                else ("no_failure" if verdict is True else "insufficient_evidence"),
                "failure_labels": None,
                "prerequisites_satisfied": verdict,
            }
        )
    return {
        "packet_digest": stable_digest(packet),
        "methods": methods,
        "interpretation": "Native free-text uncertainty remains abstention. Fine-grained localization is unavailable for the flat checker.",
    }


def _cluster_interval(grouped, plan):
    keys = sorted(grouped)
    if len(keys) < 2:
        return None
    rng, draws = random.Random(plan.seed), []
    for _ in range(plan.bootstrap_replicates):
        sample = [v for c in rng.choices(keys, k=len(keys)) for v in grouped[c]]
        draws.append(sum(sample) / len(sample))
    draws.sort()
    return [draws[int(0.025 * (len(draws) - 1))], draws[int(0.975 * (len(draws) - 1))]]


def score_predictions(
    packet_dir, adjudication_path, predictions_path, left_path, right_path
):
    root = Path(packet_dir)
    packet = json.loads((root / "public/episodes.json").read_text())
    manifest = json.loads((root / "private/manifest.json").read_text())
    adjudicator, truth = load_annotations(adjudication_path, packet)
    left_id, _ = load_annotations(left_path, packet)
    right_id, _ = load_annotations(right_path, packet)
    if len({adjudicator, left_id, right_id}) != 3:
        raise ValueError(
            "independent annotators and a distinct adjudicator are required"
        )
    predictions = json.loads(Path(predictions_path).read_text())
    if predictions["packet_digest"] != stable_digest(packet) or manifest[
        "packet_digest"
    ] != stable_digest(packet):
        raise ValueError("scoring packet digest mismatch")
    plan = StudyPlan.model_validate(manifest["plan"])
    if manifest["plan_digest"] != stable_digest(plan.model_dump(mode="json")):
        raise ValueError("sampling plan changed")
    clusters = {r["episode_id"]: r["cluster"] for r in manifest["selected"]}
    results = {}
    if not predictions.get("methods"):
        raise ValueError("at least one prediction method is required")
    for method, values in predictions["methods"].items():
        rows = {r["episode_id"]: r for r in values}
        if (
            len(values) != len(truth)
            or set(rows) != set(truth)
            or any(
                r["assessment"]
                not in {"failure", "no_failure", "insufficient_evidence"}
                for r in values
            )
        ):
            raise ValueError("predictions must cover every episode exactly once")
        if any(
            r.get("failure_labels") is not None
            and not set(r["failure_labels"]) <= LABELS
            for r in values
        ):
            raise ValueError("prediction contains an unknown failure label")
        comparable = [
            k for k in truth if truth[k].assessment != "insufficient_evidence"
        ]
        judged = [
            k for k in comparable if rows[k]["assessment"] != "insufficient_evidence"
        ]
        grouped = defaultdict(list)
        for k in comparable:
            grouped[clusters[k]].append(
                int(rows[k]["assessment"] == truth[k].assessment)
            )
        label_rows = None
        if all(r.get("failure_labels") is not None for r in values):
            label_rows = {}
            for label in sorted(LABELS):
                tp = sum(
                    label in rows[k]["failure_labels"]
                    and label in truth[k].failure_labels
                    for k in comparable
                )
                fp = sum(
                    label in rows[k]["failure_labels"]
                    and label not in truth[k].failure_labels
                    for k in comparable
                )
                fn = sum(
                    label not in rows[k]["failure_labels"]
                    and label in truth[k].failure_labels
                    for k in comparable
                )
                label_rows[label] = {
                    "tp": tp,
                    "fp": fp,
                    "fn": fn,
                    "precision": tp / (tp + fp) if tp + fp else None,
                    "recall": tp / (tp + fn) if tp + fn else None,
                }
        results[method] = {
            "human_determinate": len(comparable),
            "method_determinate": len(judged),
            "coverage": len(judged) / len(comparable) if comparable else None,
            "selective_accuracy": sum(
                rows[k]["assessment"] == truth[k].assessment for k in judged
            )
            / len(judged)
            if judged
            else None,
            "accuracy_counting_abstentions_as_unresolved": sum(
                rows[k]["assessment"] == truth[k].assessment for k in comparable
            )
            / len(comparable)
            if comparable
            else None,
            "world_cluster_bootstrap_95_interval": _cluster_interval(grouped, plan),
            "world_clusters": len(grouped),
            "assessment_confusion": {
                actual: dict(
                    Counter(
                        rows[k]["assessment"]
                        for k in truth
                        if truth[k].assessment == actual
                    )
                )
                for actual in ("failure", "no_failure", "insufficient_evidence")
            },
            "per_label": label_rows,
        }
    paired = None
    if {"dcore", "flat_evidence"} <= predictions["methods"].keys():
        full, simple = (
            {r["episode_id"]: r for r in predictions["methods"][method]}
            for method in ("dcore", "flat_evidence")
        )
        grouped = defaultdict(list)
        for k, gold in truth.items():
            if gold.assessment != "insufficient_evidence":
                grouped[clusters[k]].append(
                    int(full[k]["assessment"] == gold.assessment)
                    - int(simple[k]["assessment"] == gold.assessment)
                )
        differences = [v for values in grouped.values() for v in values]
        paired = {
            "dcore_minus_flat_accuracy": sum(differences) / len(differences)
            if differences
            else None,
            "world_cluster_bootstrap_95_interval": _cluster_interval(grouped, plan),
            "world_clusters": len(grouped),
            "abstentions_count_as_unresolved": True,
        }
    text_messages = {
        (e["episode_id"], send["message_id"])
        for e in packet["episodes"]
        for send in e["events"]
        if send["kind"] == "message_send"
        and send.get("text")
        and not send.get("claims", {}).get("fact_versions")
        and any(
            receive["kind"] == "message_receive"
            and receive["message_id"] == send["message_id"]
            and receive["actor"] == e["decision"]["actor"]
            and receive["logical_time"] <= e["decision"]["logical_time"]
            for receive in e["events"]
        )
    }
    mapped = {
        (r.episode_id, m["message_id"])
        for r in truth.values()
        for m in r.message_mappings
    }
    return {
        "schema_version": "diagnostic_annotation_score_v1",
        "packet_digest": stable_digest(packet),
        "methods": results,
        "paired_comparison": paired,
        "adjudication_sha256": file_digest(adjudication_path),
        "predictions_sha256": file_digest(predictions_path),
        "received_free_text_messages": len(text_messages),
        "human_free_text_mapping_coverage": len(mapped & text_messages)
        / len(text_messages)
        if text_messages
        else None,
        "human_insufficient_evidence": sum(
            r.assessment == "insufficient_evidence" for r in truth.values()
        ),
        "human_mapped_messages": len(
            {
                (r.episode_id, m["message_id"])
                for r in truth.values()
                for m in r.message_mappings
            }
        ),
        "interpretation": "Balanced diagnostic sample, not a prevalence estimate. Bootstrap resamples scenario/world clusters. Abstention is reported separately from a wrong diagnosis; human uncertainty is retained. Human message mappings are an independent coverage audit and never silently injected into native scores.",
    }
