# The other compiler: GCC 13.3 vs clang 18.1

> **Historical measurement, 2026-08-02.** These numbers predate the 2026-08-04 parser corrections (`jalr imm(rs)` decoded as a jump, objdump's `<target>` annotation left on the operand) and the `arg-call-pair` frame, so both the pair counts and the per-corpus
> ratios have moved. They are kept for the ARGUMENT they make, not as current figures; `results/corpus/README.md` holds the live scores.

Every corpus before this one was built by clang 18. `results/corpus/TUNE.md`
established that variation *within* clang is negligible — the whole rv32
`-mtune` space moves the packets-vs-RVC ratio by 0.2 points — which left the
compiler itself as the only untested generalisation axis. It is not negligible.

Builds: `riscv64-linux-gnu-gcc 13.3.0`, `-O2
-march=rv64gc_zba_zbb_zbs_zicond -mabi=lp64d`, same source trees and the same
disassembly pipeline as the clang corpora. sqlite is the cleanest comparison —
identical amalgamation, identical flags, one compiler apart.

## Scores

```
corpus             insns   pairs  packet %  real RVC   vs RVC  to parity
sqlite-rv64       189677   43115     77.3%     72.1%   107.2%    +9840
sqlite-gcc-rv64   167510   35156     79.0%     71.2%   111.0%   +13092
musl-rv64         102040   22291     78.2%     72.7%   107.4%    +5525
musl-gcc-rv64     103442   21458     79.3%     72.9%   108.7%    +6532
musl-rv32         119026   27896     76.6%     74.9%   102.2%    +1975
musl-gcc-rv32     119956   26564     77.9%     71.9%   108.2%    +7112
```

GCC's own code is smaller (sqlite: 167510 instructions against clang's 189677,
−11.7%) and compresses about as well under RVC. But **we pair it worse on every
build**: 107.2% → 111.0% on sqlite, 107.4% → 108.7% on musl rv64, and 102.2% →
108.2% on musl rv32 — the rv32 figure, our best corpus, loses six points.

This is the generalisation gap. Roughly a third of the frame set is fitted to
clang's idioms more tightly than anyone intended.

## Where the pairs went

sqlite, GCC's actual hits against clang's scaled to the same instruction count
(×0.883, so the columns are comparable):

```
rule                         clang  scaled     gcc   delta
prologue-pair                  894     790      65    -725
load-alu-chain           2052    1812    1206    -606
czero-or-chain              603     533       0    -533
setup-jump-pair              6623    5849    5363    -486
addi-store-chain                797     704     225    -479
bit-test-branch-chain          902     797     469    -328
rsd-alu-pair                  3412    3013    2712    -301
index-mem-chain           436     385      88    -297
...
load-sp-branch-pair                 477     421     557    +136
alu-alu-chain                1143    1009    1266    +257
post-inc-pair             693     612    1038    +426
pre-inc-pair                   565     499    1059    +560
indep-pair               5949    5254    5935    +681
TOTAL                        43115   38076   35156   -2920
```

### 1. `prologue-pair` is fitted to clang's stack-slot convention — −725

The frame has no offset field of its own. Its two rows share one immediate and
spell the store as `16*imm - k(sp)`: the store offset is *derived* from the
frame-size immediate with a fixed `k`. That works because clang always writes
the same slot first — in sqlite, 905 of its 922 adjacent `addi sp,sp,-N` /
store pairs use `k = 8`.

GCC has no such habit. Its `k` distribution over 865 adjacent pairs:

```
k     8    16    24    32    40    48   other
n    75   364   164    86    53    34      89
```

Only the `k = 8` cases can encode, which is exactly the 65 pairs we scheduled.
Every `k` observed is a multiple of 8, and `k/8 ∈ 1..6` covers 776 of 865
(89.7%), so a 3-bit `k` field would recover essentially all of it. That is a
frame change, not a rules.py change — noted here, not yet costed against the
codepoint budget.

### 2. GCC 13 never emits `czero` — −627

`czero-or-chain` and `li-czero-chain` are 603 + 107 on clang sqlite and **0 +
0** on GCC, because GCC 13.3 emits no `czero.eqz`/`czero.nez` at all despite
accepting `zicond` in `-march` (1530 czero instructions in the clang build, 0 in
GCC's). Nothing is broken; the conditional-select work we did earlier this
session is real but currently earns only against clang. Re-check against GCC 14+,
which has better Zicond codegen.

### 3. GCC prefers the other half of several dual frames — +2060

`pre-inc-pair` doubles (499 → 1059), `post-inc-pair` +70%, `alu-alu-chain`
+25%, `indep-pair` +13%. These are the frames whose value we have
repeatedly questioned on clang evidence alone — `pre-inc-pair` in particular was
called "pretty useless" on clang numbers and is GCC's third-biggest earner among
the chain frames. Its low clang score was a fact about clang.

## What this changes

- **No frame should be cut on clang-only evidence.** `pre-inc-pair` was a
  candidate for the codepoint-reclamation list; on this evidence it stays.
  `post-inc-shadd-pair` is still 0 on both compilers and is still safe to cut.
- **`prologue-pair` is the single biggest generalisation fix available**, worth
  ~700 pairs per sqlite-sized corpus on GCC and nothing at all on clang.
- The clang corpora are not wrong, but they are one compiler's habits. Anything
  sized against them alone should now be re-checked against `*-gcc-*`.
