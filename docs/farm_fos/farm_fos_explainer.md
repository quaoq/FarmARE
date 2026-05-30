# FARM-FOS, in plain words

*Companion to `farm_fos_math.tex`. This file explains what the metric does and
why it is good, without equations.*

---

## 1. The problem we are solving

We want to score how well an AI agent managed a soybean season, by comparing
its sequence of actions to an expert "oracle" sequence. The standard metrics
the agent community uses for this — **BFCL** (did you call the right tools),
**CORE / path-correctness** (did you call them in the right order, measured by
edit distance), and **KTC** (right order, measured by Kendall rank correlation)
— all share one blind spot:

> **They only look at *which* actions you took and in *what order*. They ignore
> *when* you took them and *where* on the field.**

On a farm, *when* is everything. Irrigating during pod-fill (growth stage R5)
can save a third of the yield; the same irrigation two weeks early does almost
nothing. Spraying fungicide in the R3–R6 disease window protects the canopy;
the same spray after the leaves are already gone is wasted. Harvesting at 14%
grain moisture is ideal; harvesting at 19% or letting pods shatter loses grain.

We proved this blind spot empirically. On our 90 full-season runs, we measured
how each existing metric correlates with the **real yield** the agent achieved:

| Metric | Correlation with real yield |
|---|---|
| CORE / path-correctness | ≈ 0 (basically flat) |
| vanilla KTC | ≈ 0 (even slightly negative) |
| coverage / combined | ≈ 0 |

In other words: **an agent can score well on these metrics and still wreck the
harvest, or score badly and still bring in a good crop.** For a paper about
agents acting in the physical world, that is the gap worth closing.

---

## 2. The idea, in one sentence

> **Keep what KTC does (match action type and order), but add a penalty for how
> far each action is — in crop time and in field space — from where and when the
> expert did it, weighted by how much yield that action actually controls.**

That is FARM-FOS. It is a *spatiotemporally-aware* path metric.

---

## 3. The three things it adds

For every action the expert took, FARM-FOS asks three questions instead of one:

1. **Did you do it at all, of the right type?** (this is what BFCL/CORE/KTC
   already check)
2. **Where?** — did you treat the right ridges? We measure the overlap between
   the ridges the agent treated and the ridges the expert intended. Treat half
   the right strip, get half credit. *(the spatial part)*
3. **When?** — how close in *crop time* was your action to the expert's ideal
   moment? Right on time = full credit; a little early or late = partial credit
   that fades as you drift away from the ideal window; way off = no credit.
   *(the temporal part)*

Then each action's credit is **weighted by how much yield it controls.** Missing
a pod-fill irrigation in a drought year costs you a lot of points, because the
growth model says it costs a lot of yield. Being a day off on a routine scouting
check costs almost nothing. The weights are not hand-picked — they come from the
farm's own growth model (next section).

The "right time" is not a single instant but a **neighbourhood**: a peak at the
ideal moment with a tolerance window around it. Think of a hill — the top is the
best moment, and you slide down the slope the further early or late you are. The
width of that hill is different for each action: a weed-control window is narrow
(a few days), a fertilizer window is broad (a couple of weeks), and harvest is
lopsided (a few days late is fine, before maturity is bad).

---

## 4. Why it is "good" and not just another arbitrary formula

Our **old** FOS metric mixed outcome, decision, and efficiency with weights we
basically guessed (0.5 / 0.3 / 0.2). A reviewer can rightly ask "why those
numbers?" and we have no principled answer. That is the weakness we are fixing.

FARM-FOS is different because **its weights and timing-windows are grounded in
agronomy, fixed in advance, and never tuned to the yields we then report.** This
is the single most important design choice, because it is what stops a reviewer
from dismissing the whole thing as circular (see §8). Two steps:

1. **Set the weights and windows from known agronomy, frozen up front.** The
   critical timing windows are textbook crop science, not something we invented:
   pod-fill (R5) is the most water-sensitive stage; the critical weed-free period
   is the first few weeks (VE–V3); the disease-spray window is R3–R6; the harvest
   window is 13–15% grain moisture. A real Harbin soybean operation would use the
   same windows. We write these down **once, before computing any correlation,**
   and cite them to crop-science / extension sources. They give a clean rule we
   can state as one equation.

2. **Confirm — do not fit — against the engine.** As a *sanity check only*, for a
   few decisive actions (irrigation, fungicide, harvest) we sweep the action's
   timing through the simulator and record season-end yield, to show the
   yield-vs-offset curve peaks and narrows roughly where our a-priori windows say
   it should. **We do not set the weights equal to those measured yield drops** —
   doing that would be the circular "reconstruct yield from the trace, then
   correlate with yield" trick reviewers kill on sight. The sweeps live in an
   appendix as corroboration that the agronomy priors point the right way.

This is the honest framing the paper takes: **this is an application paper.** We
are not claiming a new general-purpose ML metric. We are saying: *for our farm,
we encoded known crop-stage agronomy into the score; the generic metrics throw
that information away and go flat against yield, while ours — using the same
traces, just more of the information in them — tracks it.* The contribution is
the **recipe** (calibrate your evaluation metric from your domain's process
model / agronomy) plus the **negative result** (standard path metrics miss
physical outcome), not "we beat three metrics."

---

## 5. How the baselines are just FARM-FOS with the good parts removed

This is a clean way to present it: the existing metrics are **special cases** of
ours where we delete ingredients.

- Turn off space, time, and weighting → you get a **BFCL** success rate ("did
  the right tools get called").
- Add back only action order → you get **CORE / KTC**.
- Add back space + crop-time + physics weighting → you get **FARM-FOS**.

So we are not competing with an unrelated method; we are showing that the three
ingredients the field usually discards are exactly the ones that carry the yield
signal.

---

## 6. What the figure will show

A scatter plot:

- **x-axis:** the real outcome — preserved-yield ratio (or yield lost).
- **y-axis:** the metric value.
- **Four clouds of points** (one per metric: BFCL, CORE, KTC, FARM-FOS), each
  with its best-fit line and its correlation coefficient in the legend.

The story the plot tells: the BFCL / CORE / KTC clouds are **flat** — knowing
the metric tells you nothing about the yield. The FARM-FOS cloud **slopes** —
high metric means high yield, low means low. We back this with Pearson and
Spearman correlations and a significance test that ours beats each baseline.

We report the correlation **two ways**, because they answer different questions:

1. **Across scenarios, one agent** (our current 90-run data): does the metric
   track how well the agent did on each scenario? This is what the spreadsheets
   support today.
2. **Across agents, same scenario** (using the earlier 10-controller × 3-model
   results): on a *fixed* field, does a *better* agent get a higher FARM-FOS
   *and* a higher yield? This is the one a reviewer will demand — it proves the
   metric measures **agent skill**, not just which scenarios happen to be easy
   (see §8, attack #2).

---

## 7. Honest caveats (so we are not surprised at review)

- **It needs per-action timestamps.** The metric reads *when* each action
  happened, so it must be computed from the full run traces, not from a summary
  table. (The summary spreadsheets already have the flat baselines; the new
  curve is computed by replaying the traces — no extra LLM cost.)
- **It is calibrated to our engine / agronomy.** That is a feature for an
  application paper (it makes it accurate for our farm) but the *specific
  numbers* don't transfer to a different crop or model without recalibration.
  The *recipe* (calibrate weights and windows from your process model / known
  agronomy) does transfer — that is the reusable contribution.
- **Yield here is simulated by the same engine.** We have no real harvest
  ground truth in this paper. This is a *benchmark-wide* limitation (the whole
  benchmark's yields are simulated), not specific to our metric; we state it
  plainly and call real-field validation future work.
- **We want the correlation strong but NOT perfect.** We target a Spearman of
  roughly 0.6–0.85. A near-perfect 0.99 would be a *red flag* that we
  accidentally rebuilt yield from the trace, not a success — so if it comes out
  that high we investigate rather than celebrate.

---

## 8. Will reviewers accept this? (and how we defend it)

Honest read: **borderline-but-defensible** if we ship the three guards below;
**reject-bait** without them. The three attacks a tough reviewer opens with:

1. **"This is circular — you built the metric from the yield model, then showed
   it predicts yield."** Our defense: the weights/windows come from *frozen
   agronomic priors set before any correlation*, not from fitted yield drops;
   and all four metrics see the *same traces* — ours just uses the timing/space
   information the others discard, so the gain is from information use, not from
   peeking at the answer. (This is why §4 insists on "confirm, do not fit.")

2. **"One agent over 90 scenarios — maybe the metric and yield both just track
   scenario difficulty, not agent quality."** Our defense: the cross-agent arm
   in §6 (better agent → higher FARM-FOS *and* higher yield on a fixed scenario).

3. **"Your yield is simulated; why not just report the yield directly?"** Our
   defense: (a) yield needs a full season rollout an external evaluator may not
   be able to run; (b) a trace metric gives *per-action diagnostic credit* —
   which decisions were good — not just one number; (c) the paper's point is
   *what an evaluation metric should capture* when outcome ground truth is
   unavailable, which is the normal situation in agentic evaluation.

Framed this way — **a methodology contribution plus a negative result**, not "we
beat three metrics" — it is much harder to reject.

---

## 9. One-line summary for the abstract

> We introduce FARM-FOS, a spatiotemporally-weighted path metric whose
> action weights and timing tolerances are reverse-engineered from the farm's
> growth model; unlike BFCL, CORE, and KTC — which are blind to *when* and
> *where* an action occurs and correlate near-zero with yield — FARM-FOS aligns
> with measured season yield, making it the appropriate way to score agents
> operating a physical, long-horizon agricultural process.
