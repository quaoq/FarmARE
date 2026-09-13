# Execution record

1. The initial connectivity run under restricted networking produced two
   controller connection failures, zero recorded successful calls and zero
   recorded tokens. Preserve `llm_connectivity/`; do not include it among the
   eight treatment assignments. A separate read-only model lookup reproduced
   `APIConnectionError` in the sandbox and succeeded with network access.
   Retry the identical 12-call manifest in `llm_connectivity_network/` with
   network access. No model, prompt, treatment or budget changes were made.
   The runner's generic “Auth error” did not establish invalid credentials.

2. The network-enabled attempt reached OpenAI but the inherited EU hostname
   returned “incorrect regional hostname,” requesting `api.openai.com`.
   Preserve `llm_connectivity_network/` (zero recorded successful calls/tokens).
   Set both actors' endpoint explicitly to `https://api.openai.com/v1` in the
   connectivity and eight-run manifests; leave `.env` unchanged. Retry in
   `llm_connectivity_endpoint/` with the same model and budgets. The original
   endpoint-free configurations remain in each attempt's resolved manifest.

3. At the corrected endpoint, OpenAI rejected the adapter's `max_tokens`
   parameter and required `max_completion_tokens`. Preserve the failed attempt
   in `llm_connectivity_endpoint/` (zero recorded successful calls/tokens).
   Correct the OpenAI JSON adapter to forward the configured cap through
   `max_completion_tokens`; keep other providers' parameter handling unchanged.
   Also correct the copied provider name in the JSON instruction from DeepSeek
   to OpenAI. Regression-test the transmitted cap and retry the 12-call check
   in `llm_connectivity_compatible/` before starting the eight-run matrix.

4. Re-evaluating the saved scripted oracle exposed conflicting engineering
   phase hints: three R5 decisions had policy commitments labelled midseason.
   Proposal validity now reports these as unassessable when no frozen phase
   windows resolve the conflict. It does not infer invalidity from the wrong
   phase's acceptance constraints. Frozen phase windows retain precedence.
   The primary EF/CC definitions remain unchanged; a focused regression covers
   conflicting hints. This correction precedes the eight-run matrix.

5. The compatible connectivity check succeeded: eight recorded model calls,
   141,543 recorded tokens, no controller errors, and token-budget termination.
   The 41,543-token overshoot illustrates the documented stop-before-next-call
   accounting policy. It did not complete harvest. Proceed with all eight
   predeclared assignments and the unchanged one-million-token per-run budget.
