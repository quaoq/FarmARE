"""Fuzzy workflow comparison for agricultural agent evaluation.

Drop-in companion to ``workflow_validation.evaluate_workflows()`` that relaxes
the exact-match assumption built into the original path_correctness / KTC
metrics. Designed for long-horizon farm-management tasks where:

  * The *amount* an agent applies (irrigation, fertilizer, pesticide) can
    differ from the oracle by up to 2× and still be agronomically sound.
  * Ridge-ID lists often differ in extent but overlap substantially.
  * The oracle may call the same method several times with different args
    (e.g. ``irrigate(amount=1.5)`` then ``irrigate(amount=2.8)``); an agent
    call ``irrigate(amount=2.0)`` should match the *closest* oracle call, not
    count as a mismatch just because the args aren't byte-identical.
  * Partially-correct steps deserve partial credit so sequence metrics don't
    collapse to near-zero on long-horizon episodes.

Design decisions
----------------
1. **Numeric args** – similarity = max(0, 1 − rel_err / tolerance) where
   ``rel_err = |a − b| / max(|a|, |b|, ε)``.  Default tolerance = 1.0
   (100 % relative error), so oracle=1 → agent [0.5, 2.0] gets ≥ 0.5 credit.

2. **List/set args** (e.g. ``ridge_ids``) – Jaccard similarity over the
   string representations of elements.

3. **String args** – exact match (1.0 or 0.0). These are usually categorical
   action names where approximate matching is not meaningful.

4. **Step matching** – greedy 1-to-1 assignment within each tool group,
   descending by similarity.  No external dependencies (no scipy).

5. **Partial credit in edit distance** – a matched pair with similarity *s*
   contributes edit cost ``1 − s`` instead of 0 (perfect) or 1 (mismatch).

Output metrics (all ∈ [0, 1])
------------------------------
  fuzzy_coverage           – |matched oracle steps| / |oracle steps|
  fuzzy_avg_match_sim      – mean similarity of matched pairs
  fuzzy_ktc_raw            – Kendall Tau on matched step ordering
  fuzzy_ktc_adjusted       – fuzzy_ktc_raw × fuzzy_coverage
  fuzzy_path_correctness   – 1 − weighted_edit_cost / max(|O|, |A|)
  fuzzy_combined           – 0.5 · fuzzy_path_correctness + 0.5 · fuzzy_ktc_adjusted
  n_oracle_steps           – total oracle tool steps
  n_agent_steps            – total agent tool steps
  n_matched                – matched pairs count
"""
from __future__ import annotations

from itertools import combinations
from typing import Any

# ---------------------------------------------------------------------------
# Tuneable defaults
# ---------------------------------------------------------------------------

#: Max ratio between two numeric values to still count as a match.
#: 2.0 means agent can be anywhere in [oracle/2, oracle*2] and gets sim=1.0.
#: Outside that range → sim=0.0 (hard cutoff, no partial credit for numerics).
NUMERIC_TOLERANCE: float = 2.0

#: Step pairs below this overall similarity are not counted as matches.
#: Keeps pairs where *every* arg is off from polluting coverage / KTC.
MIN_STEP_SIMILARITY: float = 0.25


# ---------------------------------------------------------------------------
# Value-level similarity
# ---------------------------------------------------------------------------


def _numeric_sim(a: float, b: float, tol: float) -> float:
    """Hard-cutoff similarity for two numeric values.

    ``tol`` is the maximum allowed ratio between the two values
    (larger / smaller).  ``tol=2.0`` means the larger value may be at most
    2× the smaller — i.e. oracle=2 accepts agent ∈ [1, 4].

    Returns 1.0 if the ratio is within tolerance, 0.0 otherwise.
    Both-zero is a perfect match.  One-zero vs non-zero is always 0.0.
    """
    if a == b:
        return 1.0
    abs_a, abs_b = abs(a), abs(b)
    if abs_a < 1e-9 or abs_b < 1e-9:
        return 0.0  # one is zero, the other is not
    ratio = max(abs_a, abs_b) / min(abs_a, abs_b)
    return 1.0 if ratio <= tol else 0.0


def _list_sim(a: Any, b: Any) -> float:
    """Jaccard similarity for list / tuple args (e.g. ridge_ids).

    Elements are coerced to ``str`` for comparison so ``[1, 2, 3]`` and
    ``["1", "2", "3"]`` are treated identically.
    """
    sa = set(str(x) for x in (a if isinstance(a, (list, tuple)) else [a]))
    sb = set(str(x) for x in (b if isinstance(b, (list, tuple)) else [b]))
    if not sa and not sb:
        return 1.0
    union = sa | sb
    if not union:
        return 1.0
    return len(sa & sb) / len(union)


def _value_sim(oracle_val: Any, agent_val: Any, numeric_tol: float) -> float:
    """Dispatch to the right similarity function based on type."""
    # Both numeric
    if isinstance(oracle_val, (int, float)) and isinstance(agent_val, (int, float)):
        return _numeric_sim(float(oracle_val), float(agent_val), numeric_tol)
    # Both list / tuple → Jaccard
    if isinstance(oracle_val, (list, tuple)) and isinstance(agent_val, (list, tuple)):
        return _list_sim(oracle_val, agent_val)
    # Mixed numeric vs list: treat as mismatch (different semantic type)
    if isinstance(oracle_val, (int, float)) != isinstance(agent_val, (int, float)):
        return 0.0
    # String / other: exact match
    return 1.0 if str(oracle_val) == str(agent_val) else 0.0


# ---------------------------------------------------------------------------
# Step-level similarity
# ---------------------------------------------------------------------------


def step_similarity(
    oracle_step: dict[str, Any],
    agent_step: dict[str, Any],
    numeric_tolerance: float = NUMERIC_TOLERANCE,
    ignored_arg_keys: frozenset[str] = frozenset({"notes", "comment", "reason"}),
) -> float:
    """Compute [0, 1] similarity between two workflow steps.

    Returns 0.0 immediately if ``tool_name`` differs (tool identity is
    non-negotiable).  For matching tool names, averages per-arg similarities
    over the *union* of arg keys, excluding ``ignored_arg_keys``.

    An arg present in oracle but missing in agent (or vice-versa) contributes
    0.0 to the average — the agent either forgot a required arg or added an
    unexpected one.  ``ignored_arg_keys`` exempts free-text fields that carry
    no semantic weight.
    """
    if oracle_step.get("tool_name") != agent_step.get("tool_name"):
        return 0.0

    o_args: dict[str, Any] = oracle_step.get("tool_args") or {}
    a_args: dict[str, Any] = agent_step.get("tool_args") or {}

    # Remove ignored keys
    o_args = {k: v for k, v in o_args.items() if k not in ignored_arg_keys}
    a_args = {k: v for k, v in a_args.items() if k not in ignored_arg_keys}

    all_keys = set(o_args) | set(a_args)
    if not all_keys:
        return 1.0  # both have no meaningful args → perfect match

    sims = []
    for k in all_keys:
        if k not in o_args or k not in a_args:
            sims.append(0.0)
        else:
            sims.append(_value_sim(o_args[k], a_args[k], numeric_tolerance))

    return sum(sims) / len(sims)


# ---------------------------------------------------------------------------
# Step-level assignment (greedy, within tool groups)
# ---------------------------------------------------------------------------


def _greedy_assign(
    o_indices: list[int],
    a_indices: list[int],
    sim_matrix: list[list[float]],
    min_sim: float,
) -> list[tuple[int, int, float]]:
    """Greedy 1-to-1 assignment maximising total similarity.

    Builds a flat list of (sim, oracle_idx, agent_idx), sorts descending by
    sim, and greedily picks pairs — each oracle/agent index used at most once.
    Pairs below ``min_sim`` are discarded.
    """
    flat: list[tuple[float, int, int]] = []
    for i, oi in enumerate(o_indices):
        for j, ai in enumerate(a_indices):
            s = sim_matrix[i][j]
            if s >= min_sim:
                flat.append((s, oi, ai))
    flat.sort(reverse=True)

    used_o: set[int] = set()
    used_a: set[int] = set()
    matches: list[tuple[int, int, float]] = []
    for sim, oi, ai in flat:
        if oi not in used_o and ai not in used_a:
            matches.append((oi, ai, sim))
            used_o.add(oi)
            used_a.add(ai)
    return matches


def fuzzy_match_steps(
    oracle_steps: list[dict[str, Any]],
    agent_steps: list[dict[str, Any]],
    numeric_tolerance: float = NUMERIC_TOLERANCE,
    min_step_similarity: float = MIN_STEP_SIMILARITY,
) -> tuple[list[tuple[int, int, float]], list[int], list[int]]:
    """Optimal-within-tool-group 1-to-1 assignment of agent steps to oracle.

    Returns:
        matches        – list of (oracle_idx, agent_idx, similarity)
        unmatched_o    – oracle indices with no agent match
        unmatched_a    – agent indices with no oracle match
    """
    from collections import defaultdict

    oracle_by_tool: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(oracle_steps):
        tn = s.get("tool_name")
        if tn:
            oracle_by_tool[tn].append(i)

    agent_by_tool: dict[str, list[int]] = defaultdict(list)
    for j, s in enumerate(agent_steps):
        tn = s.get("tool_name")
        if tn:
            agent_by_tool[tn].append(j)

    all_matches: list[tuple[int, int, float]] = []
    matched_o: set[int] = set()
    matched_a: set[int] = set()

    for tool, o_idx in oracle_by_tool.items():
        a_idx = agent_by_tool.get(tool, [])
        if not a_idx:
            continue
        # Build similarity matrix (rows = oracle, cols = agent)
        sim_matrix = [
            [
                step_similarity(
                    oracle_steps[oi], agent_steps[ai], numeric_tolerance
                )
                for ai in a_idx
            ]
            for oi in o_idx
        ]
        pairs = _greedy_assign(o_idx, a_idx, sim_matrix, min_step_similarity)
        all_matches.extend(pairs)
        matched_o.update(oi for oi, _, _ in pairs)
        matched_a.update(ai for _, ai, _ in pairs)

    unmatched_o = [i for i in range(len(oracle_steps)) if i not in matched_o]
    unmatched_a = [j for j in range(len(agent_steps)) if j not in matched_a]
    return all_matches, unmatched_o, unmatched_a


# ---------------------------------------------------------------------------
# Fuzzy Levenshtein distance (for path_correctness)
# ---------------------------------------------------------------------------


def _fuzzy_levenshtein(
    oracle_steps: list[dict],
    agent_steps: list[dict],
    numeric_tolerance: float,
) -> float:
    """Compute a fuzzy edit distance between two step sequences.

    This is a weighted Levenshtein DP where:
      - Insertion  (extra agent step with no oracle counterpart): cost 1.0
      - Deletion   (oracle step the agent skipped entirely):      cost 1.0
      - Substitution, same tool name:  cost = 1 - step_similarity()
        (0.0 for a perfect arg match, approaching 1.0 as args diverge)
      - Substitution, different tools: cost 1.0 (same as original)

    This is consistent with the original ``path_correctness`` Levenshtein but
    gives partial credit when the agent calls the right tool with close-enough
    arguments, instead of treating it as a full mismatch.
    """
    m, n = len(oracle_steps), len(agent_steps)
    # dp[i][j] = fuzzy edit distance between oracle[:i] and agent[:j]
    dp = [[0.0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = float(i)  # delete all oracle steps
    for j in range(n + 1):
        dp[0][j] = float(j)  # insert all agent steps

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            o_step = oracle_steps[i - 1]
            a_step = agent_steps[j - 1]
            if o_step.get("tool_name") == a_step.get("tool_name"):
                sub_cost = 1.0 - step_similarity(o_step, a_step, numeric_tolerance)
            else:
                sub_cost = 1.0
            dp[i][j] = min(
                dp[i - 1][j] + 1.0,          # deletion
                dp[i][j - 1] + 1.0,          # insertion
                dp[i - 1][j - 1] + sub_cost,  # substitution / match
            )
    return dp[m][n]


# ---------------------------------------------------------------------------
# Sequence-level metrics
# ---------------------------------------------------------------------------


def _ktc_on_oracle_order(oracle_indices_in_agent_order: list[int]) -> float:
    """Kendall Tau on matched oracle indices as seen in agent order.

    Computes concordant vs discordant pairs among the sequence of oracle
    positions [p0, p1, …] where pi is the oracle index of the i-th matched
    step in the agent trace.  A concordant pair (i, j) has i < j in agent
    order *and* p_i < p_j in oracle order.

    Result is normalised to [0, 1] via (tau + 1) / 2.
    """
    n = len(oracle_indices_in_agent_order)
    if n < 2:
        return 1.0 if n == 1 else 0.0

    concordant = 0
    discordant = 0
    for i, j in combinations(range(n), 2):
        # i < j in agent order by construction
        if oracle_indices_in_agent_order[i] < oracle_indices_in_agent_order[j]:
            concordant += 1
        else:
            discordant += 1

    total = concordant + discordant
    tau = (concordant - discordant) / max(1, total)
    return (tau + 1.0) / 2.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _extract_tool_steps(
    workflow: dict[str, dict[str, Any]] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract tool-calling steps (no USER ops) from a workflow dict/list."""
    steps = list(workflow.values()) if isinstance(workflow, dict) else list(workflow)
    return [
        s for s in steps if s.get("tool_name") and s.get("op_type") != "USER"
    ]


def evaluate_fuzzy_workflow(
    oracle_workflow: dict[str, dict[str, Any]] | list[dict[str, Any]],
    agent_workflow: dict[str, dict[str, Any]] | list[dict[str, Any]],
    numeric_tolerance: float = NUMERIC_TOLERANCE,
    min_step_similarity: float = MIN_STEP_SIMILARITY,
) -> dict[str, Any]:
    """Compute fuzzy path-correctness metrics for one agent run.

    Parameters
    ----------
    oracle_workflow, agent_workflow
        Dicts (or lists) of workflow steps as produced by
        ``workflow_validation.workflow_from_oracle_events`` /
        ``workflow_from_event_log``.
    numeric_tolerance
        Relative-error tolerance for numeric args.  Default 1.0 = 100 %,
        so oracle=1 → agent ∈ [0.5, 2.0] receives ≥ 0.5 credit.
    min_step_similarity
        Minimum per-step similarity to count a pair as "matched".

    Returns
    -------
    dict with keys:
        fuzzy_coverage, fuzzy_avg_match_sim,
        fuzzy_ktc_raw, fuzzy_ktc_adjusted,
        fuzzy_path_correctness, fuzzy_combined,
        n_oracle_steps, n_agent_steps, n_matched,
        match_detail (list of per-pair dicts for debugging)
    """
    oracle_steps = _extract_tool_steps(oracle_workflow)
    agent_steps = _extract_tool_steps(agent_workflow)

    n_oracle = len(oracle_steps)
    n_agent = len(agent_steps)

    matches, unmatched_o, unmatched_a = fuzzy_match_steps(
        oracle_steps,
        agent_steps,
        numeric_tolerance=numeric_tolerance,
        min_step_similarity=min_step_similarity,
    )

    n_matched = len(matches)

    # ---- Coverage --------------------------------------------------------
    fuzzy_coverage = n_matched / max(1, n_oracle)

    # ---- Average match similarity ----------------------------------------
    fuzzy_avg_match_sim = (
        sum(sim for _, _, sim in matches) / n_matched if n_matched else 0.0
    )

    # ---- KTC on matched steps (order consistency) ------------------------
    # Sort matches by agent index to get oracle indices in agent order.
    matches_by_agent = sorted(matches, key=lambda t: t[1])
    oracle_order = [m[0] for m in matches_by_agent]
    fuzzy_ktc_raw = _ktc_on_oracle_order(oracle_order)
    fuzzy_ktc_adjusted = fuzzy_ktc_raw * fuzzy_coverage

    # ---- Fuzzy path correctness (fuzzy Levenshtein DP) ------------------
    # Uses the same normalisation as the original path_correctness so the
    # two metrics are directly comparable. The DP gives partial credit for
    # same-tool steps with close-enough args, instead of treating them as
    # full substitutions (cost=1) like the exact version does.
    fld = _fuzzy_levenshtein(oracle_steps, agent_steps, numeric_tolerance)
    norm = max(n_oracle, n_agent, 1)
    fuzzy_path_correctness = max(0.0, 1.0 - fld / norm)

    # ---- Combined --------------------------------------------------------
    fuzzy_combined = 0.5 * fuzzy_path_correctness + 0.5 * fuzzy_ktc_adjusted

    # ---- Debug detail (per-pair) ----------------------------------------
    match_detail = []
    for oi, ai, sim in matches_by_agent:
        match_detail.append(
            {
                "oracle_idx": oi,
                "agent_idx": ai,
                "similarity": round(sim, 4),
                "oracle_tool": oracle_steps[oi].get("tool_name"),
                "agent_tool": agent_steps[ai].get("tool_name"),
                "oracle_args": oracle_steps[oi].get("tool_args"),
                "agent_args": agent_steps[ai].get("tool_args"),
            }
        )

    return {
        "fuzzy_coverage": round(fuzzy_coverage, 4),
        "fuzzy_avg_match_sim": round(fuzzy_avg_match_sim, 4),
        "fuzzy_ktc_raw": round(fuzzy_ktc_raw, 4),
        "fuzzy_ktc_adjusted": round(fuzzy_ktc_adjusted, 4),
        "fuzzy_path_correctness": round(fuzzy_path_correctness, 4),
        "fuzzy_combined": round(fuzzy_combined, 4),
        "n_oracle_steps": n_oracle,
        "n_agent_steps": n_agent,
        "n_matched": n_matched,
        "match_detail": match_detail,
    }
