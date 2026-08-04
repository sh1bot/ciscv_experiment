# Compiler scheduling and `-mtune`, rv32

> **Historical measurement, 2026-07-30.** These numbers predate the 2026-08-04 parser corrections (`jalr imm(rs)` decoded as a jump, objdump's `<target>` annotation left on the operand) and the `arg-call-pair` frame, so both the pair counts and the per-corpus
> ratios have moved. They are kept for the ARGUMENT they make, not as current figures; `results/corpus/README.md` holds the live scores.

Two questions: how much does the compiler's pipeline scheduling change what we
see, and which `-mtune` gives the best result at `-O2`?

All builds are musl 1.2.5, clang 18.1.3, `-march=rv32gc_zba_zbb_zbs_zicond
-mabi=ilp32d -O2`, ISA held constant so only the tuning model varies.
`util/tune_sweep.sh` reproduces them. `-mtune=generic-rv32` is byte-identical
to the existing `tests/musl-rv32.s` build, which is the control.

## What rv32 actually offers

Of the 26 CPUs clang lists, the rv64-only ones (`sifive-p450`, `sifive-p670`,
`veyron-v1`, `xiangshan-nanhu`) are rejected outright for an rv32 target. The
rest collapse into **four** distinct scheduling models — every name inside a
group produces a byte-identical `libc.so`:

| model | names | libc.so |
|---|---|---|
| none | `generic`, `generic-rv32` | 862340 |
| Rocket | `rocket-rv32`, `sifive-e20/e21/e24/e31/e34` | 862356 |
| SiFive7 | `sifive-7-series`, `sifive-e76` | 950500 |
| SCR1 | `syntacore-scr1-base`, `syntacore-scr1-max` | 952244 |

So the choice is four-way, not twenty-six-way.

## Scores

Absolute bytes, because the tunes do not produce the same instruction count and
per-build normalisation would hide that (same trap as the RVC comparison in
README.md).

```
tune                       insns   pairs  RVC bytes  packet bytes  vs RVC   uncompressed
generic-rv32              119026   27896     356620        364520  102.2%         476104
rocket-rv32               119031   27857     356634        364696  102.3%         476124
no-misched                119047   27779     356762        365072  102.3%         476188
sifive-7-series           146566   33011     443988        454220  102.3%         586264
syntacore-scr1-max        146958   33237     445722        454884  102.1%         587832
```

`no-misched` is `-mllvm -enable-misched=false -mllvm -enable-post-misched=false`
on the generic model: scheduling switched off entirely.

**`generic-rv32` — the default — wins, and nothing else is close.** It is the
smallest in every column. The `vs RVC` ratio is flat at 102.1–102.3% across all
five, a 0.2-point spread; `syntacore-scr1-max` edges it on ratio alone while
costing 90KB more code, which is the ratio being useless rather than the tune
being good. There is no tune setting worth switching to, and no tune setting
that flatters or penalises packets relative to RVC.

## How much scheduling is being done to us

`util/duse.py` gives the def-to-first-use distance within a basic block.
Distance 1 is a producer/consumer pair already adjacent — free for us; anything
further has to be closed by the 16-instruction reorder window.

```
build                     1      2     3     4     5     6   >=7
no-misched            72.3%   9.4%  6.2%  1.9%  2.8%  0.9%  6.5%
generic-rv32          64.1%  12.7%  5.9%  6.3%  3.0%  1.2%  6.8%
rocket-rv32           63.0%  16.3%  7.4%  2.8%  2.9%  1.3%  6.4%
sifive-7-series       58.3%  20.2%  7.6%  3.2%  2.8%  2.1%  5.8%
```

The compiler is doing real work here: adjacency falls from 72.3% with the
scheduler off to 58.3% under the dual-issue SiFive7 model. Fourteen points of
producer/consumer pairs get pushed apart to hide latency.

**It costs us almost nothing.** Turning scheduling off entirely moves the pair
count from 27896 to 27779 — 117 pairs, 0.4%, and in the *wrong* direction:
scheduled code pairs very slightly BETTER than unscheduled. The reorder window
recovers the separation, and the compiler's motion occasionally brings an
unrelated pairable neighbour into range. Compiler pipeline scheduling is not a
factor for this project in either direction, and there is nothing to be gained
by asking for a build with it disabled.

## What `-mtune` really changes on rv32

Not the schedule — the unroll and inline cost models. A model with real latency
data makes the unroller far more willing:

```c
int f(int *p, int n) { int s = 0; for (int i = 0; i < n; i++) s += p[i]*3; return s; }
```

is 24 instructions under `generic-rv32` and 95 under `sifive-7-series`. Across
musl that is +23% instructions (119026 → 146566) spread near-uniformly over
every mnemonic. That is the entire 90KB difference in the table above; it is a
speed-for-size trade, and it is orthogonal to pairing — the ratio does not
move.
