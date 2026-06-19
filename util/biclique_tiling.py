#!/usr/bin/env python3
"""
biclique_tiling.py
==================

Bit-budget tiling optimiser for an asymmetric token co-occurrence table.

Problem (see SPEC.md for the full statement)
--------------------------------------------
Given a V x V matrix M of co-occurrence counts (rows = "from" token, cols =
"to" token; M is NOT assumed symmetric and the diagonal is meaningful) and a
bit budget N, we split N into three non-negative fields:

    N = b + w + h

      b : "overlap" bits  -> 2**b blocks (tiles)
      w : independent width  bits -> 2**w columns per tile
      h : independent height bits -> 2**h rows per tile

A *tile* selects 2**h distinct row tokens R and 2**w distinct col tokens C and
covers the full rectangle R x C of directed cells. Every split addresses
exactly 2**N cells in total (2**b * 2**w * 2**h), so the choice of split changes
only the SHAPE of what is covered, never the count.

Global uniqueness rule: each ordered pair (row token, col token) may be
realised at most once. Two tiles therefore conflict iff they share BOTH a row
token AND a col token (sharing only a row, or only a col, is fine -- that is
the legitimate "horizontal reuse" case). Equivalently the tiles must be
edge-disjoint in the bipartite (row-copy, col-copy) graph.

Objective: choose 2**b edge-disjoint tiles of the fixed shape (2**h x 2**w)
to maximise the total covered mass.

This is max-weight edge-disjoint K_{n,n} packing with a fixed tile shape and a
cardinality cap -- NP-hard for tile dims > 1, so we use a greedy alternating
densest-biclique extractor (documented below). The point of the harness is not
a provably optimal packing; it is an HONEST comparison of splits under
out-of-sample yield, so that the b=0 (single big tile, robust) vs b>0
(finer tiling, higher in-sample mass but more overfit) trade-off can be judged
on held-out numbers rather than guessed.

Honest scoring
--------------
Counts are split by Poisson thinning into independent halves A and B
(Binomial(count, 0.5)). Tiling is selected ENTIRELY on A; captured mass is
reported on B. The held-out figure is an unbiased estimate of out-of-sample
yield and strips the winner's curse out of the comparison automatically.
Optional shrinkage toward the independence baseline regularises the tail.

No third-party deps beyond numpy. No network required.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass, asdict, field
from typing import Optional

import numpy as np


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #

@dataclass
class Tile:
    rows: tuple[int, ...]   # row-token indices (distinct, len == 2**h)
    cols: tuple[int, ...]   # col-token indices (distinct, len == 2**w)


@dataclass
class Split:
    b: int
    w: int
    h: int

    @property
    def n_blocks(self) -> int:
        return 1 << self.b

    @property
    def tile_cols(self) -> int:
        return 1 << self.w

    @property
    def tile_rows(self) -> int:
        return 1 << self.h

    @property
    def total_cells(self) -> int:
        return self.n_blocks * self.tile_cols * self.tile_rows

    def feasible(self, V: int) -> bool:
        # need enough distinct tokens to fill a tile in each direction
        return self.tile_cols <= V and self.tile_rows <= V

    def __str__(self) -> str:
        return (f"b={self.b} w={self.w} h={self.h} "
                f"({self.n_blocks}x [{self.tile_rows}x{self.tile_cols}])")


@dataclass
class ConfigResult:
    split: Split
    feasible: bool
    in_sample_mass: float          # captured mass on training half A
    in_sample_frac: float          # / total mass of A
    held_out_mass_mean: float      # captured mass on B, averaged over thinnings
    held_out_mass_std: float
    held_out_frac_mean: float      # / total mass of B
    held_out_frac_std: float
    overlap_cells_mean: float      # cells covered more than once (should be ~0)
    n_thinnings: int
    tiles_example: list[Tile] = field(default_factory=list)  # from last thinning


# --------------------------------------------------------------------------- #
# Synthetic data (for iterating before the real table arrives)
# --------------------------------------------------------------------------- #

def make_synthetic_table(
    V: int = 100,
    seed: int = 0,
    total_count: float = 5.0e5,
    hub_size: int = 12,
    hub_fraction: float = 0.45,
    tail_exponent: float = 1.6,
    asymmetry: float = 0.25,
) -> np.ndarray:
    """
    Build a plausible co-occurrence table: a dense 'hub' block of high-frequency
    tokens that co-occur strongly with each other, plus a heavy-tailed scatter
    over the rest. Returns an integer count matrix V x V.

    This is deliberately the regime discussed in conversation: heavy tail
    (favours fine tiling on in-sample mass) WITH a rectangular hub cluster
    (favours one big tile) -- so the b=0 vs b>0 tension is actually present and
    the harness has something to discover.
    """
    rng = np.random.default_rng(seed)

    # Underlying rate matrix (continuous), later turned into Poisson counts.
    rate = np.zeros((V, V), dtype=float)

    # Heavy-tailed per-token "popularity" (Zipf-ish).
    ranks = np.arange(1, V + 1)
    pop = ranks.astype(float) ** (-tail_exponent)
    rng.shuffle(pop)
    base = np.outer(pop, pop)                  # independence-like background
    base /= base.sum()
    rate += base

    # Planted dense hub: a block of hub_size tokens that co-occur strongly.
    hubs = rng.choice(V, size=hub_size, replace=False)
    hub_block = rng.gamma(shape=2.0, scale=1.0, size=(hub_size, hub_size))
    hub_block /= hub_block.sum()
    rate_hub = np.zeros_like(rate)
    rate_hub[np.ix_(hubs, hubs)] = hub_block
    rate = (1 - hub_fraction) * rate + hub_fraction * rate_hub

    # Inject asymmetry so (a,b) != (b,a) genuinely matters.
    skew = rng.uniform(1 - asymmetry, 1 + asymmetry, size=(V, V))
    rate = rate * skew
    rate /= rate.sum()

    counts = rng.poisson(rate * total_count).astype(np.int64)
    return counts


# --------------------------------------------------------------------------- #
# Preprocessing: thinning + shrinkage
# --------------------------------------------------------------------------- #

def poisson_thin(M: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Split integer counts into two independent Poisson halves A, B (A+B=M)."""
    A = rng.binomial(M, 0.5)
    B = M - A
    return A.astype(float), B.astype(float)


def shrink_to_independence(M: np.ndarray, alpha: float) -> np.ndarray:
    """
    Shrink a (count or rate) matrix toward its rank-1 independence baseline
    E[i,j] = rowsum_i * colsum_j / total. alpha in [0,1]; 0 = no shrinkage.
    Regularises the heavy tail so a single noisy spike cannot anchor a tile.
    """
    if alpha <= 0:
        return M
    total = M.sum()
    if total <= 0:
        return M
    r = M.sum(axis=1, keepdims=True)
    c = M.sum(axis=0, keepdims=True)
    E = (r @ c) / total
    return (1 - alpha) * M + alpha * E


# --------------------------------------------------------------------------- #
# Core: densest-biclique extraction + greedy edge-disjoint packing
# --------------------------------------------------------------------------- #

def _densest_biclique(
    M: np.ndarray,
    used: np.ndarray,
    nr: int,
    nc: int,
    rng: np.random.Generator,
    restarts: int = 8,
    max_iters: int = 12,
) -> tuple[tuple[int, ...], tuple[int, ...], float]:
    """
    Find a high-value tile of shape (nr rows, nc cols) on residual mass.

    Residual value of a cell = M[r,c] if NOT used[r,c] else 0. We maximise the
    sum of residual value over the chosen rectangle by alternating optimisation:
    fix columns -> take the nr rows with the largest residual row-scores; fix
    rows -> take the nc best columns; iterate to a fixed point. Multiple
    restarts (heavy seeds + random seeds) mitigate local optima.

    Returns (rows, cols, residual_value).
    """
    V = M.shape[0]
    # Residual matrix: zero out already-used cells once, reuse each iteration.
    Mres = np.where(used, 0.0, M)

    col_totals = Mres.sum(axis=0)
    row_totals = Mres.sum(axis=1)

    def top_k(scores: np.ndarray, k: int) -> np.ndarray:
        # indices of the k largest scores (ties broken by index)
        if k >= len(scores):
            return np.argsort(-scores, kind="stable")[:k]
        idx = np.argpartition(-scores, k - 1)[:k]
        return idx[np.argsort(-scores[idx], kind="stable")]

    best_rows: tuple[int, ...] = ()
    best_cols: tuple[int, ...] = ()
    best_val = -1.0

    # Seed column sets: heaviest columns, then random restarts.
    seeds: list[np.ndarray] = [top_k(col_totals, nc)]
    for _ in range(max(0, restarts - 1)):
        seeds.append(rng.choice(V, size=nc, replace=False))

    for C0 in seeds:
        C = np.asarray(C0)
        prev = None
        for _ in range(max_iters):
            row_scores = Mres[:, C].sum(axis=1)
            R = top_k(row_scores, nr)
            col_scores = Mres[R, :].sum(axis=0)
            C = top_k(col_scores, nc)
            key = (tuple(sorted(R.tolist())), tuple(sorted(C.tolist())))
            if key == prev:
                break
            prev = key
        val = float(Mres[np.ix_(R, C)].sum())
        if val > best_val:
            best_val = val
            best_rows = tuple(int(x) for x in R)
            best_cols = tuple(int(x) for x in C)

    return best_rows, best_cols, best_val


def pack_tiles(
    M: np.ndarray,
    split: Split,
    rng: np.random.Generator,
    restarts: int = 8,
) -> tuple[list[Tile], np.ndarray]:
    """
    Greedily place `split.n_blocks` edge-disjoint tiles of the fixed shape,
    each maximising residual mass on M. Returns (tiles, used_mask).

    Edge-disjointness is enforced softly: already-claimed cells contribute zero
    residual value, so the extractor is pushed toward disjoint tiles; any
    genuinely re-covered cells are reported by the caller via count_overlap().
    With V >> tile dims there is normally ample disjoint room and overlap is 0.
    """
    V = M.shape[0]
    used = np.zeros((V, V), dtype=bool)
    tiles: list[Tile] = []
    nr, nc = split.tile_rows, split.tile_cols

    for _ in range(split.n_blocks):
        rows, cols, val = _densest_biclique(M, used, nr, nc, rng, restarts=restarts)
        if val <= 0 and tiles:
            # nothing left worth covering; stop early
            break
        tiles.append(Tile(rows=rows, cols=cols))
        used[np.ix_(rows, cols)] = True

    return tiles, used


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

def covered_mask(tiles: list[Tile], V: int) -> np.ndarray:
    mask = np.zeros((V, V), dtype=bool)
    for t in tiles:
        mask[np.ix_(t.rows, t.cols)] = True
    return mask


def count_overlap(tiles: list[Tile], V: int) -> int:
    """Number of cells covered by more than one tile (uniqueness violations)."""
    counts = np.zeros((V, V), dtype=np.int32)
    for t in tiles:
        counts[np.ix_(t.rows, t.cols)] += 1
    return int((counts > 1).sum())


def captured_mass(M: np.ndarray, tiles: list[Tile]) -> float:
    mask = covered_mask(tiles, M.shape[0])
    return float(M[mask].sum())


# --------------------------------------------------------------------------- #
# Driver: enumerate splits, thin, evaluate
# --------------------------------------------------------------------------- #

def enumerate_splits(N: int, b_max: int = 3) -> list[Split]:
    out: list[Split] = []
    for b in range(0, min(b_max, N) + 1):
        rem = N - b
        for w in range(0, rem + 1):
            h = rem - w
            out.append(Split(b=b, w=w, h=h))
    return out


def evaluate(
    M: np.ndarray,
    N: int,
    *,
    b_max: int = 3,
    n_thinnings: int = 12,
    shrink_alpha: float = 0.0,
    restarts: int = 8,
    seed: int = 0,
) -> list[ConfigResult]:
    """
    For every feasible (b,w,h) split of N, run `n_thinnings` Poisson thinnings;
    in each, select tiles on half A (optionally shrunk) and score on half B.
    Returns one ConfigResult per split.
    """
    V = M.shape[0]
    rng = np.random.default_rng(seed)
    splits = enumerate_splits(N, b_max=b_max)

    results: list[ConfigResult] = []
    for sp in splits:
        if not sp.feasible(V):
            results.append(ConfigResult(
                split=sp, feasible=False,
                in_sample_mass=0.0, in_sample_frac=0.0,
                held_out_mass_mean=0.0, held_out_mass_std=0.0,
                held_out_frac_mean=0.0, held_out_frac_std=0.0,
                overlap_cells_mean=0.0, n_thinnings=0,
            ))
            continue

        ho_mass, ho_frac, is_mass, is_frac, overlaps = [], [], [], [], []
        last_tiles: list[Tile] = []
        for _ in range(n_thinnings):
            A, B = poisson_thin(M.astype(np.int64), rng)
            A_train = shrink_to_independence(A, shrink_alpha)
            tiles, _ = pack_tiles(A_train, sp, rng, restarts=restarts)
            last_tiles = tiles

            is_m = captured_mass(A, tiles)          # in-sample uses unshrunk A
            ho_m = captured_mass(B, tiles)
            a_tot = float(A.sum()) or 1.0
            b_tot = float(B.sum()) or 1.0
            is_mass.append(is_m); is_frac.append(is_m / a_tot)
            ho_mass.append(ho_m); ho_frac.append(ho_m / b_tot)
            overlaps.append(count_overlap(tiles, V))

        results.append(ConfigResult(
            split=sp, feasible=True,
            in_sample_mass=float(np.mean(is_mass)),
            in_sample_frac=float(np.mean(is_frac)),
            held_out_mass_mean=float(np.mean(ho_mass)),
            held_out_mass_std=float(np.std(ho_mass)),
            held_out_frac_mean=float(np.mean(ho_frac)),
            held_out_frac_std=float(np.std(ho_frac)),
            overlap_cells_mean=float(np.mean(overlaps)),
            n_thinnings=n_thinnings,
            tiles_example=last_tiles,
        ))
    return results


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def baseline_held_out(results: list[ConfigResult]) -> float:
    """Best held-out fraction achievable at b=0 (the robust reference)."""
    b0 = [r for r in results if r.feasible and r.split.b == 0]
    return max((r.held_out_frac_mean for r in b0), default=0.0)


def print_report(results: list[ConfigResult], N: int) -> None:
    base = baseline_held_out(results)
    print(f"\n=== N = {N} bits | {sum(r.feasible for r in results)} feasible "
          f"splits | b=0 held-out reference = {base:.4f} ===\n")
    header = (f"{'split':<22} {'feas':<5} {'in-samp%':>9} {'held-out%':>11} "
              f"{'±std':>7} {'lift vs b=0':>12} {'overlap':>8}")
    print(header)
    print("-" * len(header))
    # sort by held-out fraction descending, infeasible last
    ordered = sorted(
        results,
        key=lambda r: (r.feasible, r.held_out_frac_mean),
        reverse=True,
    )
    for r in ordered:
        if not r.feasible:
            print(f"{str(r.split):<22} {'no':<5} {'-':>9} {'-':>11} "
                  f"{'-':>7} {'-':>12} {'-':>8}")
            continue
        lift = r.held_out_frac_mean - base
        lift_s = f"{lift:+.4f}"
        print(f"{str(r.split):<22} {'yes':<5} "
              f"{100*r.in_sample_frac:>8.2f}% {100*r.held_out_frac_mean:>10.2f}% "
              f"{100*r.held_out_frac_std:>6.2f}% {lift_s:>12} "
              f"{r.overlap_cells_mean:>8.1f}")
    print()
    print("Read: 'in-samp%' is optimistic (winner's curse); 'held-out%' is the "
          "honest yield.\nA b>0 split only earns its keep if 'lift vs b=0' is "
          "clearly positive beyond its ±std.\n'overlap' > 0 flags uniqueness "
          "violations (should be ~0).")


def write_csv(results: list[ConfigResult], path: str) -> None:
    with open(path, "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["b", "w", "h", "n_blocks", "tile_rows", "tile_cols",
                      "total_cells", "feasible", "in_sample_frac",
                      "held_out_frac_mean", "held_out_frac_std",
                      "held_out_mass_mean", "held_out_mass_std",
                      "overlap_cells_mean", "n_thinnings"])
        for r in results:
            sp = r.split
            wtr.writerow([sp.b, sp.w, sp.h, sp.n_blocks, sp.tile_rows,
                          sp.tile_cols, sp.total_cells, int(r.feasible),
                          f"{r.in_sample_frac:.6f}",
                          f"{r.held_out_frac_mean:.6f}",
                          f"{r.held_out_frac_std:.6f}",
                          f"{r.held_out_mass_mean:.3f}",
                          f"{r.held_out_mass_std:.3f}",
                          f"{r.overlap_cells_mean:.3f}", r.n_thinnings])


def dump_best_tiles(results: list[ConfigResult], path: str) -> None:
    best = max((r for r in results if r.feasible),
               key=lambda r: r.held_out_frac_mean, default=None)
    if best is None:
        return
    payload = {
        "split": asdict(best.split),
        "held_out_frac_mean": best.held_out_frac_mean,
        "tiles_example": [{"rows": list(t.rows), "cols": list(t.cols)}
                          for t in best.tiles_example],
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def load_table(path: str) -> np.ndarray:
    if path.endswith(".npy"):
        M = np.load(path)
    else:
        M = np.loadtxt(path, delimiter=",")
    M = np.asarray(M)
    if M.ndim != 2 or M.shape[0] != M.shape[1]:
        raise ValueError(f"expected a square 2D table, got shape {M.shape}")
    if (M < 0).any():
        raise ValueError("counts must be non-negative")
    if not np.allclose(M, np.round(M)):
        raise ValueError("table must be raw integer counts (thinning needs counts)")
    return M.astype(np.int64)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--table", help="path to V x V count table (.npy or .csv). "
                                    "If omitted, synthetic data is generated.")
    p.add_argument("--N", type=int, default=6, help="bit budget (default 6)")
    p.add_argument("--b-max", type=int, default=3, help="max overlap bits (default 3)")
    p.add_argument("--thinnings", type=int, default=12,
                   help="number of Poisson thinnings to average (default 12)")
    p.add_argument("--shrink", type=float, default=0.0,
                   help="shrinkage alpha toward independence, 0..1 (default 0)")
    p.add_argument("--restarts", type=int, default=8,
                   help="alternating-optimiser restarts per tile (default 8)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--V", type=int, default=100, help="synthetic table size")
    p.add_argument("--out-prefix", default="tiling",
                   help="prefix for csv/json outputs (default 'tiling')")
    args = p.parse_args(argv)

    if args.table:
        M = load_table(args.table)
        print(f"Loaded table {M.shape}, total count = {int(M.sum())}")
    else:
        M = make_synthetic_table(V=args.V, seed=args.seed)
        print(f"Generated synthetic table {M.shape}, total count = {int(M.sum())} "
              f"(use --table to supply your own)")

    results = evaluate(
        M, args.N, b_max=args.b_max, n_thinnings=args.thinnings,
        shrink_alpha=args.shrink, restarts=args.restarts, seed=args.seed,
    )
    print_report(results, args.N)
    write_csv(results, f"{args.out_prefix}_N{args.N}.csv")
    dump_best_tiles(results, f"{args.out_prefix}_N{args.N}_best.json")
    print(f"Wrote {args.out_prefix}_N{args.N}.csv and "
          f"{args.out_prefix}_N{args.N}_best.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
