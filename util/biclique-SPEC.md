# Bit-Budget Biclique Tiling — Specification

## 1. Purpose

Given a token co-occurrence table and a fixed bit budget, decide how to split
the budget across three address fields so that a hardware/storage scheme covers
the most co-occurrence mass **on unseen data**. The deliverable is a decision
aid: an honest, side-by-side comparison of every budget split, scored on
held-out mass, so the operator can make the "is b > 0 worth it?" judgement call
on measured numbers rather than intuition.

This document specifies `biclique_tiling.py`.

## 2. Input

A `V × V` matrix `M` of **raw integer co-occurrence counts**.

- Rows index the "from" token, columns the "to" token. `M` is **not** assumed
  symmetric: `M[a][b]` and `M[b][a]` are distinct directed pairs.
- The diagonal `M[a][a]` is meaningful and is treated as an ordinary cell.
- Counts must be non-negative integers. Raw counts are **required** (not
  normalised rates) because the honest-scoring step (§6) relies on the Poisson
  splitting property, which only holds for counts.
- Expected scale: `V ≈ 100`, heavy-tailed / log-distributed mass, typically an
  **incomplete sample** of the true distribution.

Accepted formats: `.npy` (numpy array) or `.csv` (comma-delimited). A square
shape and integer values are validated at load; violations raise immediately.

## 3. The budget split

The budget `N` bits is partitioned into three non-negative fields:

```
N = b + w + h
    b : overlap  bits  ->  B  = 2**b  blocks (tiles)
    w : width    bits  ->  Wt = 2**w  columns per tile
    h : height   bits  ->  Ht = 2**h  rows    per tile
```

The two stored indices are the row-token code (`b + h` bits) and the col-token
code (`b + w` bits); they overlap in the `b` block bits, so the stored union is
exactly `N` bits.

**Invariant.** Every split addresses exactly `2**N` cells
(`B · Wt · Ht = 2**(b+w+h) = 2**N`). The split changes only the *shape* of the
coverage, never the cell count. This is the central fact the optimisation
trades against.

Search range: `b ∈ {0, 1, …, b_max}` with `b_max = 3` by default; `w, h ≥ 0`.
The number of splits is small (`C(N+2, 2)`, i.e. 21 for N=5, 28 for N=6, 36 for
N=7), so the outer loop is brute-forced exhaustively. All optimisation effort
goes into the inner packing.

## 4. Geometry: tiles, coverage, and the uniqueness rule

A **tile** selects `Ht` distinct row tokens `R` and `Wt` distinct col tokens
`C`, and covers the full rectangle `R × C` of directed cells (a complete
bipartite subgraph / biclique). Coverage is all-or-nothing: committing a tile
pays for every cell in `R × C` whether dense or cold.

Constraints:

- **Within a tile**, row tokens are distinct and col tokens are distinct
  (no token counted twice on the same axis of the same block). A token may
  appear as both a row and a col token of the same tile (this realises its
  diagonal cell).
- **Across tiles**, each ordered pair `(row token, col token)` may be realised
  **at most once globally**. Two tiles conflict iff they share **both** a row
  token **and** a col token. Sharing only a row, or only a col, is permitted —
  this is the legitimate horizontal/vertical reuse case. Equivalently, the
  tiles must be **edge-disjoint** in the bipartite (row-copy, col-copy) graph.

Feasibility: a split is feasible only if `Wt ≤ V` and `Ht ≤ V` (there must be
enough distinct tokens to fill a tile in each direction). Infeasible splits are
reported as such and excluded from ranking.

## 5. Objective and algorithm

**Objective.** Choose `B` edge-disjoint tiles of the fixed shape `Ht × Wt` to
maximise total covered mass `Σ M[r][c]` over all covered cells.

This is maximum-weight edge-disjoint `K_{Ht,Wt}` packing with a fixed tile shape
and a cardinality cap of `B`. It is NP-hard for tile dimensions > 1, so the
harness uses a documented heuristic, not an exact solver. The aim is honest
*comparison* across splits, not certified optimality of any one packing.

**Inner solve — densest biclique (`_densest_biclique`).** For a fixed shape on a
residual matrix (already-covered cells contribute zero), find a high-value tile
by **alternating optimisation**: fix the column set, take the `Ht` rows with the
largest residual row-scores; fix the rows, take the `Wt` best columns; iterate
to a fixed point. Run from several seeds — the heaviest columns plus random
restarts (`restarts`, default 8) — and keep the best. This is standard
biclustering alternation; it is fast (`V ≈ 100`, tiny tiles) and robust enough
for the comparison task.

**Outer solve — greedy packing (`pack_tiles`).** Place tiles one at a time, each
maximising residual mass, marking covered cells as used after each placement.
Stop at `B` tiles or when no positive-value tile remains.

**Edge-disjointness handling.** Enforced softly: used cells contribute zero
residual value, which pushes the extractor toward disjoint tiles. With
`V ≫ tile dims` there is normally ample disjoint room, so overlap is 0 in
practice. Any cells genuinely covered more than once are **counted and reported**
(`count_overlap`); a non-zero overlap column in the output flags a uniqueness
violation for that config and should be treated as a red flag, not silently
trusted. (A strict-disjoint mode via hard masking is a documented extension
point if the soft scheme ever reports overlaps on real data.)

## 6. Honest scoring (the core of the harness)

In-sample captured mass is optimistic: selecting tiles on noisy counts and then
scoring on the *same* counts inflates the result, and the inflation grows with
the number of independent selection decisions — i.e. with `b`. The harness
removes this bias by held-out evaluation.

**Poisson thinning.** Split the count table into two independent halves by
`A[i][j] ~ Binomial(M[i][j], ½)`, `B = M − A`. By the Poisson splitting
property, `A` and `B` are independent samples of the same underlying rates. The
**entire** selection — split, packing, and token choice — is performed on `A`;
captured mass is then read off `B`. The held-out figure is an unbiased estimate
of out-of-sample yield and strips the winner's curse out of the comparison.

The protocol is repeated `n_thinnings` times (default 12) with fresh random
splits; results are averaged and a standard deviation reported, so split noise
does not drive the ranking. **No information from `B` may leak into selection**,
or the bias returns.

**Optional shrinkage.** Before selection, the training half may be shrunk toward
its rank-1 independence baseline `E[i][j] = rowsum_i · colsum_j / total` via
`(1−α)·A + α·E` (`shrink_alpha`, default 0). This regularises the heavy tail so
a single noisy spike cannot anchor a tile. Thinning *measures* optimism;
shrinkage *suppresses* it; they compose. In-sample mass is always reported on
the unshrunk `A` so the optimism gap stays meaningful.

## 7. Output

Per feasible split, one record containing:

- the split `(b, w, h)`, derived shape, total cells, and feasibility flag;
- **in-sample fraction** — captured mass on `A` ÷ total mass of `A` (optimistic);
- **held-out fraction** — captured mass on `B` ÷ total mass of `B`, mean and std
  over thinnings (the honest yield);
- **lift vs b=0** — held-out fraction minus the best held-out fraction among all
  `b = 0` splits (the robust reference). A `b > 0` split earns its keep only if
  this lift is clearly positive beyond its ±std;
- **overlap cells** — mean count of multiply-covered cells (should be ≈ 0).

Emitted as: a ranked console table (sorted by held-out fraction), a CSV of all
configs, and a JSON dump of the example tile assignments for the best config.

## 8. Interpretation guidance

- **The fraction is scale-free.** Because thinning halves the counts, absolute
  held-out mass is ~½ the true scale; compare splits on the *fraction*, not the
  raw mass.
- **`b` is the overfitting dial.** Larger `b` ⇒ more, smaller tiles ⇒ more
  independent selection decisions ⇒ higher in-sample mass but a larger
  optimism gap. The held-out column already prices this in; the in-sample
  column is shown only to expose the gap.
- **The judgement call.** Prefer the smallest `b` whose held-out lift over `b=0`
  is both positive and larger than a couple of its standard deviations. If no
  `b > 0` split clears that bar, `b = 0` is the honest choice and the overlap
  field has bought nothing for mass — at which point its value must be justified
  by a non-mass benefit (a single fused lookup, locality, cache behaviour), not
  by this table.
- **Watch the tile-shape uniformity.** Every tile in a split shares one fixed
  `Ht × Wt` shape. If the true structure is "one big hub block + many tiny
  scattered cells", no single split expresses it well; that mismatch, not the
  bit arithmetic, is usually what pins the optimum at a non-obvious split.

## 9. CLI

```
python3 biclique_tiling.py [--table PATH] [--N 6] [--b-max 3]
                           [--thinnings 12] [--shrink 0.0] [--restarts 8]
                           [--seed 0] [--V 100] [--out-prefix tiling]
```

With no `--table`, a synthetic heavy-tailed table with a planted dense hub is
generated so the pipeline can be exercised before the real data exists. The
synthetic generator deliberately combines a heavy tail (which favours fine
tiling on in-sample mass) with a rectangular hub (which favours one big tile),
so the `b=0` vs `b>0` tension is genuinely present and the harness has something
to discover.

## 10. Known limitations / extension points

- The packing heuristic is greedy + alternating, not exact; for small budgets it
  is close to optimal in practice but carries no guarantee. An ILP/MIQP inner
  solve is a drop-in upgrade for certification at small `V`.
- Soft edge-disjointness; add hard-masking rejection if real data ever produces
  non-zero overlap.
- Thinning assumes counts are approximately Poisson. For strongly overdispersed
  data, a parametric bootstrap or a genuine train/test corpus split is more
  appropriate; the held-out scoring interface is unchanged.
- Tile shape is uniform within a split by construction (it is a hardware
  constraint, not a modelling choice); mixed-shape schemes are out of scope.
