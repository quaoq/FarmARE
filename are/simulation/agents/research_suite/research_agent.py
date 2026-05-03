import logging
from pathlib import Path

from are.simulation.agents.agent_execution_result import AgentExecutionResult
from are.simulation.agents.agent_log import (
    ErrorLog,
    LLMOutputThoughtActionLog,
    ObservationLog,
    ToolCallLog,
)
from are.simulation.agents.are_simulation_agent_config import ResearchAgentProfileConfig
from are.simulation.agents.default_agent.are_simulation_main import ARESimulationAgent
from are.simulation.agents.research_suite.skill_library import DynamicSkillLibrary
from are.simulation.agents.research_suite.research_strategies import (
    ResearchStrategyCoordinator,
    StrategyLogSnapshot,
)
from are.simulation.config import ARE_SIMULATION_ROOT
from are.simulation.notification_system import Message

logger = logging.getLogger(__name__)


class ResearchARESimulationAgent(ARESimulationAgent):
    def __init__(self, family_config: ResearchAgentProfileConfig, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.family_config = family_config
        self.reflection_memory: list[str] = []
        self._processed_log_index = 0
        self._skill_library: DynamicSkillLibrary | None = None
        self.telemetry: dict[str, object] = {}
        self._strategy = ResearchStrategyCoordinator(self.family_config)
        self._initialize_skill_library()

    def _initialize_skill_library(self) -> None:
        if not self.family_config.skills.enabled:
            return
        library_path = self.family_config.skills.library_path
        if not library_path:
            return
        path = Path(library_path)
        if not path.is_absolute():
            path = ARE_SIMULATION_ROOT.parent / path
        self._skill_library = DynamicSkillLibrary(path)

    def _reset_research_state(self) -> None:
        self.reflection_memory = []
        self._processed_log_index = 0
        self.telemetry = {
            "family_id": self.family_config.family_id,
            "planning_mode": self.family_config.planning_mode,
            "tool_calls": 0,
            "llm_calls": 0,
            "replan_signals": 0,
            "reflection_signals": 0,
            "memory_writes": 0,
            "memory_reads": 0,
            "skill_hits": 0,
            "delegation_signals": 0,
            "verification_signals": 0,
            "error_count": 0,
            "retrieved_skill_ids": [],
        }
        self._strategy.reset(self.telemetry)

    def _append_reflection(self, text: str) -> None:
        if not self.family_config.reflection.enabled:
            return
        content = text.strip()
        if not content:
            return
        self.reflection_memory.append(content)
        max_items = max(1, self.family_config.reflection.max_items)
        self.reflection_memory = self.reflection_memory[-max_items:]
        self.telemetry["memory_writes"] = int(self.telemetry["memory_writes"]) + 1

    def _register_retrieved_skill(self, skill_id: str) -> None:
        current = list(self.telemetry["retrieved_skill_ids"])  # type: ignore[arg-type]
        if skill_id not in current:
            current.append(skill_id)
            self.telemetry["retrieved_skill_ids"] = current

    def _consume_new_logs_for_metrics(self) -> None:
        logs = self.react_agent.get_agent_logs()
        if self._processed_log_index >= len(logs):
            return
        new_logs = logs[self._processed_log_index :]
        self._processed_log_index = len(logs)

        latest_tool: str | None = None
        latest_observation: str | None = None
        tool_calls: list[str] = []
        observations: list[str] = []
        llm_outputs: list[str] = []
        errors: list[str] = []

        for log in new_logs:
            if isinstance(log, ToolCallLog):
                self.telemetry["tool_calls"] = int(self.telemetry["tool_calls"]) + 1
                latest_tool = log.tool_name
                tool_calls.append(log.tool_name)
            elif isinstance(log, ObservationLog):
                latest_observation = (log.content or "")[:180]
                observations.append((log.content or "")[:300])
            elif isinstance(log, LLMOutputThoughtActionLog):
                self.telemetry["llm_calls"] = int(self.telemetry["llm_calls"]) + 1
                content = (log.content or "").lower()
                llm_outputs.append(log.content or "")
                if "replan" in content or "updated plan" in content:
                    self.telemetry["replan_signals"] = int(
                        self.telemetry["replan_signals"]
                    ) + 1
                if "reflect" in content or "lesson" in content or "mistake" in content:
                    self.telemetry["reflection_signals"] = int(
                        self.telemetry["reflection_signals"]
                    ) + 1
                if "specialist" in content or "delegate" in content:
                    self.telemetry["delegation_signals"] = int(
                        self.telemetry["delegation_signals"]
                    ) + 1
                if "verify" in content or "double-check" in content:
                    self.telemetry["verification_signals"] = int(
                        self.telemetry["verification_signals"]
                    ) + 1
            elif isinstance(log, ErrorLog):
                self.telemetry["error_count"] = int(self.telemetry["error_count"]) + 1
                errors.append(log.error)
                self._append_reflection(
                    f"Error observed ({log.error}). Use stricter precondition checks and safer retry strategy."
                )

        if latest_tool and latest_observation:
            self._append_reflection(
                f"After calling {latest_tool}, verify outcome before continuing: {latest_observation}"
            )

        self._strategy.consume_snapshot(
            StrategyLogSnapshot(
                tool_calls=tool_calls,
                observations=observations,
                llm_outputs=llm_outputs,
                errors=errors,
            )
        )

    def _build_research_context(self, task: str) -> str:
        segments: list[str] = []

        if self.family_config.planning_mode == "planner_executor":
            segments.append(
                "Planning scaffold: define milestones, execute one milestone at a time, and replan after failed checks."
            )

        if self.family_config.reflection.enabled and len(self.reflection_memory) > 0:
            top_k = max(1, self.family_config.reflection.injection_top_k)
            memory_items = self.reflection_memory[-top_k:]
            self.telemetry["memory_reads"] = int(self.telemetry["memory_reads"]) + 1
            rendered = "\n".join(f"- {item}" for item in memory_items)
            segments.append(f"Reflection memory:\n{rendered}")

        if self.family_config.skills.enabled and self._skill_library is not None:
            skill_results = self._skill_library.retrieve(
                task,
                top_k=self.family_config.skills.top_k,
                min_score=self.family_config.skills.min_score,
            )
            if len(skill_results) > 0:
                rendered_rows = []
                for skill, score in skill_results:
                    self._register_retrieved_skill(skill.skill_id)
                    rendered_rows.append(
                        f"- {skill.skill_id} (score={score:.2f}): {skill.title} — {skill.description}"
                    )
                self.telemetry["skill_hits"] = int(self.telemetry["skill_hits"]) + len(
                    skill_results
                )
                rendered = "\n".join(rendered_rows)
                segments.append(f"Retrieved skills:\n{rendered}")

        if self.family_config.delegation.enabled:
            specialists = ", ".join(self.family_config.delegation.specialists)
            segments.append(
                "Specialist protocol: reason as specialists before acting."
                f" Specialists: {specialists}. Keep one merged action plan."
            )

        if self.family_config.verification.enabled:
            keywords = ", ".join(self.family_config.verification.uncertainty_keywords)
            segments.append(
                "Adaptive verification: if uncertainty cues appear, run an explicit verification check before irreversible actions."
                f" Uncertainty cues: {keywords}."
            )

        strategy_context = self._strategy.build_context(task)
        if strategy_context.strip():
            segments.append(strategy_context)

        if len(segments) == 0:
            return ""
        return "\n\n".join(segments)

    def build_task_from_notifications(self, user_notifications: list[Message]) -> str:
        self._consume_new_logs_for_metrics()
        task = super().build_task_from_notifications(user_notifications)
        context = self._build_research_context(task)
        if not context:
            return task
        if task.strip():
            return f"{task}\n\n{context}"
        return context

    def run_scenario(self, *args, **kwargs) -> AgentExecutionResult:
        self._reset_research_state()
        result = super().run_scenario(*args, **kwargs)
        self._consume_new_logs_for_metrics()
        self.telemetry["reflection_memory_size"] = len(self.reflection_memory)
        if result.metadata is None:
            result.metadata = {}
        if self.family_config.telemetry.emit_to_result_metadata:
            result.metadata["telemetry"] = dict(self.telemetry)
        return result
