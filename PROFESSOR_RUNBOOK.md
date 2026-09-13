# Professor runbook

This guide describes the D-CORE AAMAS study. Paths below are relative to the
accompanying source checkout. In a built review/release packet, `COMMANDS.json`
contains the resolved packet commands; install the matching source revision first,
then run those commands from the packet root using that installed environment.
The packet manifest states whether paper execution is enabled.

**Launch is blocked in the current implementation candidate.** Read
`AAMAS/HANDOVER_STATUS.md` before treating this as an executable handover. Commands
below describe the prepared workflow and its explicit completion requirements.
No approval or successful scientific gate is implied by this file.

1. **Review and sign-off.** Review all three exact executable specifications,
   team/refinement artifacts, calibration evidence, sensitivity alternatives and
   protocol. Follow `AAMAS/REVIEW_ROUTE.md`; author-defined artifacts are not independently
   confirmed. Supply the actual human approval only after reviewing the frozen
   contents. Independent episode annotation remains separate.
2. **Install.** From the portable repository root, use Python 3.12.12 and
   `uv sync --frozen --extra dev`. Set provider credentials in the process environment
   or an excluded local `.env`. The manifests pin the public OpenAI endpoint and
   exact evaluated model snapshot. Credentials must never enter an archive.
3. **Offline checks.** Run `uv run --frozen pytest are/simulation/tests/distributed -q`.
   Use the detailed doctor/preflight commands in `AAMAS/PROFESSOR_03_EXECUTION_GUIDE.md`
   with the exact process, team, gate and protocol files. The current unresolved
   scientific-artifact paths deliberately block launch.
4. **Inspect assignments.** For each of the four `farm_dcore_*.yaml` study manifests,
   run `are-dcore matrix MANIFEST --output-dir results/STUDY --dry-run`. Counts must
   be 480, 450, 270 and 45; verify paired worlds, treatment settings and resource
   estimates before removing `--dry-run`. The current configured caps imply a
   conservative uncached ceiling of $32,548.68 across the full suite; this is not
   a measured full-season spending forecast. Successful progression runs are
   still needed to verify realistic run-level resource estimates.
5. **Build review/release packages.** `are-dcore handoff build --help` lists artifact
   arguments. `--stage review` requires passed engineering and frozen authored
   contents; `--stage release` additionally requires genuine approval and scientific
   gates. Use the exact clean tagged revision and verify the package hashes.
6. **Launch and monitor.** After release validation, run each matrix in its own
   output directory. Monitor call/token usage, provider failures, native errors,
   lifecycle termination, policy coverage, fault activation and harvest completeness.
   A high conditional diagnostic score on a short prefix is not season completion.
7. **Resume.** Reissue the same command only for compatible completed runs. A source
   or configuration mismatch is rejected. Interrupted runs with uncertain writes
   are preserved without automatic replay. Keep failed attempts and follow the
   frozen assignment/attempt policy for any deliberate new attempt.
8. **Annotate.** Use `are-dcore validation --help` and `AAMAS/PAPER_FOUNDATION.md`: freeze
   the plan before final outcomes, select 60 episodes with at most two per run,
   collect two independent labels, and adjudicate separately. Unknowns remain unknown.
9. **Report.** Use the saved-trace aggregation and report commands in the detailed
   execution guide. Include coverage and denominators, inactive treatment assignments,
   missing outcomes, failures, paired contrasts and world-cluster uncertainty.
   Rebuild the manuscript's planned-study table with
   `uv run --frozen python AAMAS/manuscript/build_tables.py`; replace pending empirical
   panels only with outputs of the prescribed analysis.
10. **Manuscript.** Compile `AAMAS/manuscript/main.tex` with the official class/style,
    using Tectonic 0.17.0 or a compatible TeX installation. Verify citations, eight
    main-text pages plus references, anonymity, licenses, AI-assistance disclosure
    and the anonymous supplement's 25 MB limit before submission.

Do not raise a cap for a favorable cell, drop a failed scenario, manufacture a
handoff to activate a treatment, replace a failed confirmation world, or turn
reviewer/pilot numbers into paper evidence.
