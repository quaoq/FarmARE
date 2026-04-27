from __future__ import annotations

import re
from dataclasses import dataclass

from are.simulation.agents.are_simulation_agent_config import ResearchAgentProfileConfig


@dataclass
class StrategyLogSnapshot:
    tool_calls: list[str]
    observations: list[str]
    llm_outputs: list[str]
    errors: list[str]


@dataclass
class _TreeCandidate:
    plan: str
    score: float


class ResearchStrategyCoordinator:
    def __init__(self, family_config: ResearchAgentProfileConfig):
        self.family_config = family_config
        self.telemetry: dict[str, object] = {}
        self._rewoo_plan: list[str] = []
        self._rewoo_step_index = 0
        self._rewoo_evidence: dict[str, str] = {}
        self._rewoo_replans = 0
        self._tree_candidates: list[_TreeCandidate] = []
        self._tree_expansions = 0
        self._tree_error_signal = False
        self._graph_nodes: list[tuple[str, str]] = []
        self._graph_edges: set[tuple[str, str]] = set()
        self._graph_next_id = 1

    @staticmethod
    def parse_rewoo_plan(task: str, max_plan_steps: int) -> list[str]:
        numbered_steps = re.findall(
            r"^\s*(?:\d+[\).\-\:])\s*(.+?)\s*$", task, flags=re.MULTILINE
        )
        if not numbered_steps:
            sentence_candidates = re.split(r"[\n\.]+", task)
            numbered_steps = [chunk.strip() for chunk in sentence_candidates if chunk]
        cleaned_steps: list[str] = []
        for step in numbered_steps:
            normalized_step = step.strip()
            if len(normalized_step) < 4:
                continue
            if normalized_step in cleaned_steps:
                continue
            cleaned_steps.append(normalized_step)
            if len(cleaned_steps) >= max(1, max_plan_steps):
                break
        return cleaned_steps

    def reset(self, telemetry: dict[str, object]) -> None:
        self.telemetry = telemetry
        self._rewoo_plan = []
        self._rewoo_step_index = 0
        self._rewoo_evidence = {}
        self._rewoo_replans = 0
        self._tree_candidates = []
        self._tree_expansions = 0
        self._tree_error_signal = False
        self._graph_nodes = []
        self._graph_edges = set()
        self._graph_next_id = 1
        self.telemetry.setdefault("planner_calls", 0)
        self.telemetry.setdefault("planned_steps", 0)
        self.telemetry.setdefault("executed_steps", 0)
        self.telemetry.setdefault("plan_parse_failures", 0)
        self.telemetry.setdefault("branches_generated", 0)
        self.telemetry.setdefault("branches_scored", 0)
        self.telemetry.setdefault("backtracks", 0)
        self.telemetry.setdefault("critic_cycles", 0)
        self.telemetry.setdefault("revision_cycles", 0)
        self.telemetry.setdefault("blocked_actions", 0)
        self.telemetry.setdefault("graph_nodes", 0)
        self.telemetry.setdefault("graph_edges", 0)
        self.telemetry.setdefault("graph_retrieval_hits", 0)
        self.telemetry.setdefault("contradiction_alerts", 0)

    def consume_snapshot(self, snapshot: StrategyLogSnapshot) -> None:
        if len(snapshot.errors) > 0:
            self._tree_error_signal = True
        for observation in snapshot.observations:
            evidence_key = f"#E{len(self._rewoo_evidence) + 1}"
            self._rewoo_evidence[evidence_key] = observation
        if self.family_config.graph_memory.enabled:
            for tool_name in snapshot.tool_calls:
                self._append_graph_node("action", f"tool:{tool_name}")
            for observation in snapshot.observations:
                self._append_graph_node("outcome", observation)
            for error in snapshot.errors:
                self._append_graph_node("outcome", f"error:{error}")
            self._update_graph_metrics()

    def build_context(self, task: str) -> str:
        segments: list[str] = []
        if self.family_config.rewoo.enabled:
            segments.append(self._build_rewoo_context(task))
        if self.family_config.tree_search.enabled:
            segments.append(self._build_tree_search_context(task))
        if self.family_config.critic.enabled:
            segments.append(self._build_critic_context(task))
        if self.family_config.graph_memory.enabled:
            segments.append(self._build_graph_memory_context(task))
        return "\n\n".join(segment for segment in segments if segment.strip())

    def _build_rewoo_context(self, task: str) -> str:
        self.telemetry["planner_calls"] = int(self.telemetry["planner_calls"]) + 1
        if len(self._rewoo_plan) == 0 or (
            self._tree_error_signal
            and self._rewoo_replans < max(0, self.family_config.rewoo.max_replans)
        ):
            candidate_steps = self.parse_rewoo_plan(
                task, max_plan_steps=self.family_config.rewoo.max_plan_steps
            )
            if len(candidate_steps) == 0:
                self.telemetry["plan_parse_failures"] = int(
                    self.telemetry["plan_parse_failures"]
                ) + 1
                return (
                    "ReWOO plan fallback: unable to parse structured steps from the task. "
                    "Proceed with normal ReAct execution while preserving explicit plan-work-solve structure."
                )
            self._rewoo_plan = candidate_steps
            self._rewoo_step_index = 0
            if self._tree_error_signal:
                self._rewoo_replans += 1
            self.telemetry["planned_steps"] = len(self._rewoo_plan)

        current_step = self._rewoo_plan[
            min(self._rewoo_step_index, len(self._rewoo_plan) - 1)
        ]
        resolved_step = current_step
        for evidence_key, evidence_value in self._rewoo_evidence.items():
            resolved_step = resolved_step.replace(evidence_key, evidence_value[:160])
        self._rewoo_step_index = min(self._rewoo_step_index + 1, len(self._rewoo_plan))
        self.telemetry["executed_steps"] = int(self.telemetry["executed_steps"]) + 1
        rendered_steps = "\n".join(
            f"- Step {step_index + 1}: {step}"
            for step_index, step in enumerate(self._rewoo_plan)
        )
        return (
            "ReWOO plan-work-solve:\n"
            f"{rendered_steps}\n"
            f"- Worker now executes: {resolved_step}\n"
            "- Solver should synthesize evidence and status after execution."
        )

    def _build_tree_search_context(self, task: str) -> str:
        if len(self._tree_candidates) == 0 and self._tree_expansions < max(
            1, self.family_config.tree_search.max_expansions
        ):
            self._tree_candidates = self._generate_tree_candidates(task)
            self._tree_expansions += 1
            self.telemetry["branches_generated"] = int(
                self.telemetry["branches_generated"]
            ) + len(self._tree_candidates)
            self.telemetry["branches_scored"] = int(
                self.telemetry["branches_scored"]
            ) + len(self._tree_candidates)

        if len(self._tree_candidates) == 0:
            return ""

        selected_index = 0
        if self._tree_error_signal and len(self._tree_candidates) > 1:
            selected_index = 1
            self.telemetry["backtracks"] = int(self.telemetry["backtracks"]) + 1
        selected_candidate = self._tree_candidates[selected_index]
        self._tree_error_signal = False
        rendered_candidates = "\n".join(
            f"- Candidate {candidate_index + 1} (score={candidate.score:.2f}): {candidate.plan}"
            for candidate_index, candidate in enumerate(self._tree_candidates)
        )
        return (
            "Tree-search strategy:\n"
            f"{rendered_candidates}\n"
            f"- Execute candidate {selected_index + 1} first, keep alternatives for one backtrack."
        )

    def _generate_tree_candidates(self, task: str) -> list[_TreeCandidate]:
        base_steps = self.parse_rewoo_plan(
            task, max_plan_steps=max(2, self.family_config.tree_search.search_depth + 1)
        )
        if len(base_steps) == 0:
            base_steps = [
                "Read current farm state",
                "Check weather and sensors",
                "Execute lowest-risk required action",
            ]
        candidates: list[_TreeCandidate] = []
        for branch_index in range(max(1, self.family_config.tree_search.branch_factor)):
            if branch_index == 0:
                branch_plan = " -> ".join(base_steps[: self.family_config.tree_search.search_depth + 1])
            elif branch_index == 1:
                branch_plan = " -> ".join(
                    [
                        "Validate preconditions first",
                        *base_steps[: self.family_config.tree_search.search_depth],
                    ]
                )
            else:
                branch_plan = " -> ".join(
                    [
                        "Gather additional evidence via sensors",
                        *base_steps[: self.family_config.tree_search.search_depth],
                    ]
                )
            score = self._score_branch(branch_plan)
            candidates.append(_TreeCandidate(plan=branch_plan, score=score))
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        return candidates

    @staticmethod
    def _score_branch(branch_plan: str) -> float:
        lowered = branch_plan.lower()
        score = 1.0
        if "validate preconditions" in lowered:
            score += 0.7
        if "weather" in lowered or "sensor" in lowered:
            score += 0.4
        if "execute lowest-risk" in lowered:
            score += 0.4
        if "immediately" in lowered:
            score -= 0.2
        return score

    def _build_critic_context(self, task: str) -> str:
        revision_cycles = max(
            1,
            min(
                max(1, self.family_config.critic.max_revision_cycles),
                2,
            ),
        )
        self.telemetry["critic_cycles"] = int(self.telemetry["critic_cycles"]) + 1
        self.telemetry["revision_cycles"] = int(self.telemetry["revision_cycles"]) + (
            revision_cycles
        )
        actor_steps = self.parse_rewoo_plan(task, max_plan_steps=3)
        if len(actor_steps) == 0:
            actor_steps = ["Inspect state", "Execute required action", "Report result"]
        actor_plan = " -> ".join(actor_steps)
        irreversible_action_pattern = re.compile(
            r"\b(harvest|spray|apply|plant|fertiliz|pesticide|irrigat)\w*\b",
            flags=re.IGNORECASE,
        )
        requires_precondition = bool(irreversible_action_pattern.search(task))
        has_explicit_check = bool(
            re.search(r"\b(check|verify|confirm|inspect)\b", task, flags=re.IGNORECASE)
        )
        blocked_action = (
            self.family_config.critic.enforce_precondition_checks
            and requires_precondition
            and not has_explicit_check
        )
        if blocked_action:
            self.telemetry["blocked_actions"] = int(self.telemetry["blocked_actions"]) + 1
        return (
            "Critic-refiner protocol:\n"
            f"- Actor proposal: {actor_plan}\n"
            "- Critic checks: weather window, tool readiness, inventory, and safety constraints.\n"
            f"- Revision cycles requested: {revision_cycles}.\n"
            f"- Action blocked until preconditions verified: {'yes' if blocked_action else 'no'}."
        )

    def _build_graph_memory_context(self, task: str) -> str:
        parsed_steps = self.parse_rewoo_plan(task, max_plan_steps=3)
        if len(parsed_steps) == 0:
            parsed_steps = [task[:180]]
        for step in parsed_steps:
            self._append_graph_node("objective", step)
        self._update_graph_metrics()
        retrieved_nodes = self._retrieve_graph_nodes(task)
        self.telemetry["graph_retrieval_hits"] = int(
            self.telemetry["graph_retrieval_hits"]
        ) + len(retrieved_nodes)
        contradiction_found = self._detect_contradiction(retrieved_nodes)
        if contradiction_found:
            self.telemetry["contradiction_alerts"] = int(
                self.telemetry["contradiction_alerts"]
            ) + 1
        rendered_nodes = "\n".join(f"- {node_text}" for node_text in retrieved_nodes)
        contradiction_line = (
            "Contradiction alert: conflicting state evidence detected."
            if contradiction_found
            else "Contradiction alert: none."
        )
        return (
            "Graph-memory context:\n"
            f"{rendered_nodes if rendered_nodes else '- No relevant graph nodes yet.'}\n"
            f"- {contradiction_line}"
        )

    def _append_graph_node(self, kind: str, text: str) -> None:
        normalized_text = text.strip()
        if not normalized_text:
            return
        node_id = f"N{self._graph_next_id}"
        self._graph_next_id += 1
        self._graph_nodes.append((node_id, f"[{kind}] {normalized_text}"))
        if len(self._graph_nodes) > 1:
            previous_node_id = self._graph_nodes[-2][0]
            self._graph_edges.add((previous_node_id, node_id))
        max_nodes = max(8, self.family_config.graph_memory.max_nodes)
        while len(self._graph_nodes) > max_nodes:
            removed_node_id, _ = self._graph_nodes.pop(0)
            self._graph_edges = {
                edge
                for edge in self._graph_edges
                if edge[0] != removed_node_id and edge[1] != removed_node_id
            }

    def _retrieve_graph_nodes(self, task: str) -> list[str]:
        tokens = set(re.findall(r"\w+", task.lower()))
        scored_nodes: list[tuple[int, str]] = []
        for _, node_text in self._graph_nodes:
            node_tokens = set(re.findall(r"\w+", node_text.lower()))
            overlap = len(tokens.intersection(node_tokens))
            scored_nodes.append((overlap, node_text))
        scored_nodes.sort(key=lambda row: row[0], reverse=True)
        top_k = max(1, self.family_config.graph_memory.retrieval_top_k)
        return [text for score, text in scored_nodes[:top_k] if score > 0]

    def _detect_contradiction(self, node_texts: list[str]) -> bool:
        if not self.family_config.graph_memory.contradiction_check:
            return False
        merged = " ".join(node_texts).lower()
        contradiction_pairs: list[tuple[str, str]] = [
            (r"(?<!no )\brain\b", r"\bno rain\b"),
            (r"\bdry\b", r"\bwet\b"),
            (r"\bsuccess\b", r"\bfailed\b"),
            (r"\bavailable\b", r"\bunavailable\b"),
        ]
        for positive_pattern, negative_pattern in contradiction_pairs:
            if re.search(positive_pattern, merged) and re.search(
                negative_pattern, merged
            ):
                return True
        return False

    def _update_graph_metrics(self) -> None:
        self.telemetry["graph_nodes"] = len(self._graph_nodes)
        self.telemetry["graph_edges"] = len(self._graph_edges)
