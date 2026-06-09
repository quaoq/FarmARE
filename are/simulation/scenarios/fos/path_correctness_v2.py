"""path_correctness_v2 — relaxed-numeric-arg variant of the original
``workflow_validation.evaluate_workflows`` path_correctness metric.

Design (deliberately minimal):
    * Same shape as the original: build an alphabet from (tool_name, args),
      map oracle/agent step sequences to symbol strings, run a vanilla
      Levenshtein DP, return ``1 - distance / max(len)``.
    * The ONLY change vs. the original metric is how the alphabet is
      assigned: numeric args are matched on a tolerance-ratio window
      (default tol=2.0 → "200% relaxation" — oracle=1 accepts agent in
      [0.5, 2.0]). Non-numeric args (str / list / dict / bool / None)
      still require exact equality, exactly like the original.
    * For a step that has multiple numeric args, ALL of them must lie
      inside the window for the agent step to share the oracle symbol
      (strict AND — same binary-match spirit). Anyone outside → fresh
      symbol.
    * If the oracle issues the same tool with several numeric values
      (e.g. ``irrigate(amount=1.5)`` then ``irrigate(amount=2.8)``),
      and the agent calls ``irrigate(amount=2.0)``, we map the agent
      call to the *closest* in-window oracle anchor (here 1.5: ratio
      2.0/1.5≈1.33 < 2.0/2.8≈1.4). This implements the "prefer
      most-similar" rule the user asked for without changing any other
      part of the algorithm.

This module is intentionally **additive**:
    * The original metric in ``workflow_validation.evaluate_workflows``
      is untouched and still computed alongside.
    * The output dict only carries ``path_correctness_v2`` and a small
      breakdown so callers can drop the new column into a CSV next to
      ``path_correctness`` without disturbing existing schemas.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Tunable defaults
# ---------------------------------------------------------------------------

#: Default tolerance ratio. tol=2.0 means oracle=1 accepts agent in [0.5, 2.0].
#: A pair (oracle_v, agent_v) of numeric args is "in window" iff
#:    max(|o|, |a|) / min(|o|, |a|) <= tol     (with the both-zero case = match).
DEFAULT_TOL_RATIO: float = 2.0

#: Numeric Python types we treat as "numeric" for tolerance matching.
#: bool intentionally excluded — True/False are categorical, not numeric.
_NUMERIC_TYPES = (int, float)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_numeric(v: Any) -> bool:
    return isinstance(v, _NUMERIC_TYPES) and not isinstance(v, bool)


def _numeric_in_window(o: float, a: float, tol: float) -> bool:
    """Binary in-window check. tol is a ratio bound (max/min <= tol)."""
    if o == a:
        return True
    abs_o, abs_a = abs(float(o)), abs(float(a))
    eps = 1e-12
    if abs_o < eps and abs_a < eps:
        return True
    if abs_o < eps or abs_a < eps:
        # one is zero, the other isn't — never a numeric match
        return False
    ratio = max(abs_o, abs_a) / min(abs_o, abs_a)
    return ratio <= tol


def _split_numeric_categorical(
    args: dict[str, Any] | None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Partition tool_args into (numeric_args, categorical_args).

    * Numeric args are floats / ints (not bool).
    * Everything else (str, list, dict, tuple, bool, None, ...) is categorical
      and must match exactly via the same normalisation the original
      ``_make_key`` uses below.
    """
    if not args:
        return {}, {}
    num: dict[str, float] = {}
    cat: dict[str, Any] = {}
    for k, v in args.items():
        if _is_numeric(v):
            num[k] = float(v)
        else:
            cat[k] = v
    return num, cat


def _normalize_value(value: Any) -> Any:
    """Same normalisation as workflow_validation._normalize_value.

    Kept locally to avoid coupling to the legacy module, but byte-identical
    so a v2 categorical key collides with a v1 categorical key whenever the
    args are byte-identical."""
    if isinstance(value, float) and value == int(value):
        return int(value)
    if isinstance(value, list):
        return tuple(_normalize_value(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _normalize_value(v)) for k, v in value.items()))
    return value


def _categorical_key(
    tool_name: str, categorical_args: dict[str, Any]
) -> tuple[Any, ...]:
    """Hashable key over (tool, non-numeric args). Numeric args excluded —
    those are matched separately via the tolerance window."""
    if not categorical_args:
        return (tool_name,)
    normalized = tuple(
        sorted((k, _normalize_value(v)) for k, v in categorical_args.items())
    )
    return (tool_name, normalized)


def _numeric_distance(
    oracle_num: dict[str, float], agent_num: dict[str, float], tol: float
) -> float | None:
    """Return a "closeness" score for two same-tool numeric arg sets, or
    None if any aspect disqualifies them from sharing a symbol.

    Disqualifying conditions (return None):
      * The two arg sets disagree on which keys carry numeric values
        (one has ``amount`` numeric, the other doesn't have ``amount``
        as a numeric arg). The categorical key already enforces equality
        on non-numeric args, so this case shouldn't happen — but we're
        defensive.
      * Any numeric arg pair lies outside the tolerance window.

    When both arg sets are empty (no numeric args at all), the score is
    0.0 (perfect-match anchor distance). Otherwise the score is the sum
    of log-ratios across all numeric args — smaller = closer. We use sum
    of ``log(ratio)`` rather than max so multi-arg ties break by overall
    closeness, not by single dimension only.
    """
    import math

    if set(oracle_num.keys()) != set(agent_num.keys()):
        return None

    if not oracle_num:
        return 0.0

    total = 0.0
    for k, ov in oracle_num.items():
        av = agent_num[k]
        if not _numeric_in_window(ov, av, tol):
            return None
        # Closeness contribution. Both-zero contributes 0 (perfect).
        abs_o, abs_a = abs(ov), abs(av)
        eps = 1e-12
        if abs_o < eps and abs_a < eps:
            continue
        if abs_o < eps or abs_a < eps:
            # Defensive: _numeric_in_window would already have rejected
            # this pair (tol can't reach an exact zero). Fall-through to
            # a large penalty so anchor selection still works.
            total += math.log(1.0 + tol)
            continue
        ratio = max(abs_o, abs_a) / min(abs_o, abs_a)
        total += math.log(ratio)
    return total


def _ktc(predicted: list[str], gold: list[str]) -> tuple[float, list[str]]:
    """Same Kendall-Tau-coefficient routine as
    ``workflow_validation._ktc``, kept local to avoid coupling.

    Walks the predicted symbol stream, picks each oracle symbol once on
    first sighting, then computes (concordant-discordant) on the chosen
    pairs' positions in the oracle ordering, normalised to [0, 1] via
    (tau + 1) / 2.

    Returns:
        (ktc_in_unit_range, matched_symbols_in_predicted_order)
    """
    from itertools import combinations

    seen: set[str] = set()
    matched: list[str] = []
    for sym in predicted:
        if sym in gold and sym not in seen:
            seen.add(sym)
            matched.append(sym)

    n = len(matched)
    if n < 2:
        return 0.0, []

    rank: dict[str, int] = {}
    for idx, sym in enumerate(gold):
        if sym in seen and sym not in rank:
            rank[sym] = idx
    ranks = [rank[sym] for sym in matched]

    concordant = 0
    discordant = 0
    for i, j in combinations(range(n), 2):
        if (ranks[i] - ranks[j]) * (i - j) > 0:
            concordant += 1
        else:
            discordant += 1

    tau = (concordant - discordant) / (0.5 * n * (n - 1))
    return (tau + 1) / 2.0, matched


def _levenshtein_distance(s1: list[str], s2: list[str]) -> int:
    """Standard unit-cost Levenshtein distance — same shape as the
    original ``workflow_validation._levenshtein_distance``."""
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
    return dp[m][n]


def _extract_tool_steps(
    workflow: dict[str, dict[str, Any]] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Same filter the original metric uses: keep only tool steps, drop USER ops."""
    steps = list(workflow.values()) if isinstance(workflow, dict) else list(workflow)
    return [
        s
        for s in steps
        if s.get("tool_name") and s.get("op_type") != "USER"
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_path_correctness_v2(
    oracle_workflow: dict[str, dict[str, Any]] | list[dict[str, Any]],
    agent_workflow: dict[str, dict[str, Any]] | list[dict[str, Any]],
    tol_ratio: float = DEFAULT_TOL_RATIO,
) -> dict[str, Any]:
    """Compute relaxed-numeric path_correctness_v2.

    The output dict contains:
        path_correctness_v2 — float in [0, 1], the headline metric.
        n_oracle_steps      — int.
        n_agent_steps       — int.
        levenshtein_distance — raw int (debug).
        tol_ratio           — the tol used (for traceability).
        n_oracle_anchors    — distinct oracle symbols (alphabet size on oracle side).
        n_agent_reused      — agent steps that were mapped onto an oracle anchor
                              (i.e. found a within-window same-tool match).
        n_agent_fresh       — agent steps that received a fresh (non-oracle) symbol.

    Implementation notes:
        * Build the oracle alphabet first by grouping oracle steps under
          the categorical key (tool, non-numeric_args). Within each group,
          every oracle step gets its OWN symbol — anchored on its numeric
          args — so two ``irrigate(amount=1.5)`` and ``irrigate(amount=2.8)``
          calls are still distinct anchors (Levenshtein then tells us
          whether the agent's order/coverage matches).
        * For each agent step, look up the same categorical key. Among
          oracle anchors in that group, pick the one with the smallest
          ``_numeric_distance`` whose result is not None (i.e. all numeric
          args inside the tolerance window). Tie-break: pick the anchor
          that hasn't been claimed yet by an earlier agent step in this
          run; if all are claimed, allow re-use (Levenshtein handles
          duplicates fine — duplicate symbols just look like a repeated
          tool call to the DP).
        * If no oracle anchor matches, the agent step gets a fresh symbol
          (it's a "new" event from the alphabet's perspective).
    """
    oracle_steps = _extract_tool_steps(oracle_workflow)
    agent_steps = _extract_tool_steps(agent_workflow)

    # ----- Build oracle alphabet -----
    # alphabet maps "anchor-id" → assigned symbol. Anchor-id is a per-oracle-step
    # tuple so distinct oracle calls inside the same (tool, cat_key) bucket get
    # different symbols — preserving the order/coverage signal Levenshtein needs.
    oracle_symbols: list[str] = []
    # group_anchors[cat_key] = list of (oracle_step_index, numeric_args, symbol)
    group_anchors: dict[tuple[Any, ...], list[tuple[int, dict[str, float], str]]] = {}
    next_idx = 0

    def _assign_symbol() -> str:
        nonlocal next_idx
        if next_idx < 26:
            sym = chr(ord("A") + next_idx)
        elif next_idx < 26 + 26 * 26:
            # AA, AB, ..., AZ, BA, ...
            q, r = divmod(next_idx - 26, 26)
            sym = chr(ord("A") + q) + chr(ord("A") + r)
        else:
            sym = f"S{next_idx}"
        next_idx += 1
        return sym

    # Reuse a symbol when an oracle step is byte-identical to an earlier one
    # (same tool, same categorical args, same numeric args). This matches v1
    # alphabet behaviour exactly so tol=1.0 produces v2 == v1 across every
    # downstream metric (coverage, ktc, combined). Distinct numeric values
    # still get distinct symbols — they only collapse to the *same* anchor
    # if they're equal at the byte level.
    exact_anchor_key_to_symbol: dict[tuple[Any, ...], str] = {}
    for i, step in enumerate(oracle_steps):
        tool = step["tool_name"]
        num, cat = _split_numeric_categorical(step.get("tool_args"))
        cat_key = _categorical_key(tool, cat)
        # Hashable key for "byte-identical anchor" reuse.
        num_key = tuple(sorted(num.items()))
        exact_key = (cat_key, num_key)
        sym = exact_anchor_key_to_symbol.get(exact_key)
        if sym is None:
            sym = _assign_symbol()
            exact_anchor_key_to_symbol[exact_key] = sym
        oracle_symbols.append(sym)
        group_anchors.setdefault(cat_key, []).append((i, num, sym))

    # ----- Map agent steps -----
    agent_symbols: list[str] = []
    n_reused = 0
    n_fresh = 0
    # Track how many times each oracle anchor symbol has been "claimed" so we
    # prefer un-claimed anchors when an agent step has multiple in-window
    # candidates of equal closeness.
    claim_count: dict[str, int] = {}

    # Agent-side fresh-symbol cache: a brand-new (tool, cat, num) combo not
    # seen in oracle is given a fresh symbol — but if the agent calls the
    # SAME byte-identical combo twice, both calls share that fresh symbol,
    # mirroring v1's alphabet semantics. Without this, two identical agent-
    # only calls would each get a unique symbol and inflate the alphabet.
    fresh_agent_key_to_symbol: dict[tuple[Any, ...], str] = {}
    for step in agent_steps:
        tool = step.get("tool_name")
        num, cat = _split_numeric_categorical(step.get("tool_args"))
        cat_key = _categorical_key(tool, cat)
        candidates = group_anchors.get(cat_key, [])

        best: tuple[float, int, str] | None = None  # (distance, claim_count, symbol)
        for _idx, anchor_num, sym in candidates:
            d = _numeric_distance(anchor_num, num, tol_ratio)
            if d is None:
                continue
            cc = claim_count.get(sym, 0)
            key = (d, cc, sym)
            if best is None or key < best:
                best = key

        if best is not None:
            agent_symbols.append(best[2])
            claim_count[best[2]] = claim_count.get(best[2], 0) + 1
            n_reused += 1
        else:
            num_key = tuple(sorted(num.items()))
            fresh_key = (cat_key, num_key)
            sym = fresh_agent_key_to_symbol.get(fresh_key)
            if sym is None:
                sym = _assign_symbol()
                fresh_agent_key_to_symbol[fresh_key] = sym
            agent_symbols.append(sym)
            n_fresh += 1

    # ----- Levenshtein, same shape as the original metric -----
    ld = _levenshtein_distance(agent_symbols, oracle_symbols)
    max_len = max(len(agent_symbols), len(oracle_symbols), 1)
    pc2 = 1.0 - ld / max_len

    # ----- Coverage / KTC / Combined, sharing the v2 alphabet -----
    # Identical formulas to workflow_validation.evaluate_workflows; the only
    # difference is that the symbols here come from the relaxed-numeric
    # alphabet built above. coverage_v2 etc. therefore answer "did the
    # agent hit the right oracle anchors *under the relaxed-numeric rule*"
    # rather than "byte-identical args".
    oracle_set = set(oracle_symbols)
    ktc_raw, matched = _ktc(agent_symbols, oracle_symbols)
    coverage_v2 = (len(matched) / len(oracle_set)) if oracle_set else 0.0
    ktc_adjusted_v2 = ktc_raw * coverage_v2
    combined_v2 = 0.5 * pc2 + 0.5 * ktc_adjusted_v2

    return {
        "path_correctness_v2": round(pc2, 4),
        "coverage_v2": round(coverage_v2, 4),
        "ktc_raw_v2": round(ktc_raw, 4),
        "ktc_adjusted_v2": round(ktc_adjusted_v2, 4),
        "combined_v2": round(combined_v2, 4),
        "n_oracle_steps": len(oracle_steps),
        "n_agent_steps": len(agent_steps),
        "levenshtein_distance": ld,
        "tol_ratio": tol_ratio,
        "n_oracle_anchors": len(oracle_steps),
        "n_agent_reused": n_reused,
        "n_agent_fresh": n_fresh,
        "oracle_symbols": "".join(oracle_symbols) if all(len(s) == 1 for s in oracle_symbols) else oracle_symbols,
        "agent_symbols": "".join(agent_symbols) if all(len(s) == 1 for s in agent_symbols) else agent_symbols,
    }
