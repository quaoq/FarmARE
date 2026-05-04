# FarmARE Agent Families and Paper Mapping

This repository ships **10 controller families** for comparative experiments in the farm environment.  
Use these as architecture ablations, not as claims of universal SOTA performance.

## Agent families

1. `farm_baseline_react`  
   Plain-language: direct reason-and-act baseline with tool use.  
   Technical: standard ReAct loop over available farm tools.  
   Paper: https://arxiv.org/abs/2210.03629

2. `farm_planner_executor`  
   Plain-language: creates a plan then follows it step by step.  
   Technical: milestone-oriented planning context before action selection.  
   Grounding: planning-augmented ReAct practice.

3. `farm_reflective_memory`  
   Plain-language: learns short lessons from prior steps.  
   Technical: reflection snippets injected into later turns.  
   Paper: https://arxiv.org/abs/2303.11366

4. `farm_skill_rag`  
   Plain-language: consults local skill playbooks.  
   Technical: retrieval over local skill library with top-k insertion.  
   Paper: https://arxiv.org/abs/2005.11401

5. `farm_multi_specialist`  
   Plain-language: simulates experts (weather/sensor/machinery/ops).  
   Technical: specialist decomposition prompt then merged decision.  
   Reference: https://arxiv.org/abs/2308.08155

6. `farm_adaptive_verifier`  
   Plain-language: double-checks risky actions before execution.  
   Technical: uncertainty-triggered verification cues for irreversible tools.  
   Reference: https://arxiv.org/abs/2305.11738

7. `farm_rewoo_modular`  
   Plain-language: splits work into plan/work/solve phases.  
   Technical: ReWOO-like modular decomposition and execution guidance.  
   Paper: https://arxiv.org/abs/2305.18323

8. `farm_tree_search`  
   Plain-language: explores multiple next-step branches.  
   Technical: branch generation, scoring, and limited backtracking.  
   Paper: https://arxiv.org/abs/2310.04406

9. `farm_critic_refiner`  
   Plain-language: proposes an action, critiques it, then refines it.  
   Technical: actor-critic style revision loop with precondition emphasis.  
   Reference: https://arxiv.org/abs/2305.11738

10. `farm_graph_memory`  
    Plain-language: tracks structured facts and dependencies.  
    Technical: graph-style memory context with contradiction checks.  
    Paper: https://arxiv.org/abs/2308.09687

## Agent2Agent (A2A)

- Supported as A2A OFF and A2A ON packs.
- Typed routing policy maps apps to app-expert agents:
  - `WeatherApp` -> `weather_expert_app_agent`
  - `SensorApp` -> `sensor_expert_app_agent`
  - `TractorApp`, `FieldOpsApp` -> `machinery_expert_app_agent`
  - `FarmWorldApp`, `DroneApp`, `RobotApp` -> `operations_expert_app_agent`
- A2A framing: https://developers.googleblog.com/es/a2a-a-new-era-of-agent-interoperability/

## Paper framing (safe)

- Emphasize architecture diversity, reproducibility, and transparent ablations.
- Treat long-horizon failures as useful signal, not system invalidation.
- Avoid overclaiming “best agent”; report comparative behavior and tradeoffs.
