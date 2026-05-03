# PR Checklist (FarmARE)

## Scope

- [ ] 10-family suite and A2A typed-expert paths remain intact
- [ ] Runner supports `--family` and `--scenario` filters
- [ ] Tool-argument normalization handles numeric-string + schema-dict inputs safely
- [ ] Suite outputs are timestamp-scoped and do not reuse stale run artifacts
- [ ] Docs updated: `AGENT_FAMILIES_AND_PAPERS.md`, `PROFESSOR_RUNBOOK.md`

## Validation

- [ ] `uv run ./scripts/check_readiness.sh` passes
- [ ] `uv run python scripts/run_agent_suite.py --config configs/agent_suite/smoke.yaml --dry-run --mock` works
- [ ] `uv run python -m pytest are/simulation/tests/cli/test_agent_suite_runner.py are/simulation/tests/agents/argument_normalizer_test.py -q` passes

## Notes

- Release gate for handoff: readiness check + targeted tests + mock smoke artifacts generated.
- Keep this PR additive and backward-compatible.
