# rsd-alu-pair, re-measured against what it alone is for

Measured 2026-08-05 against main.  Regenerate with `util/rsd_residue.py`, then
`util/biclique_tiling.py --table results/rsd_broad_last.csv --N <n>`; raw
tiling output in `results/rsd-tiling/`.

## The problem with the original tiling

`rsd-alu-pair` is `RULES[0]` — it sees every pair before any other frame — and
it costs **256 codepoints, 30% of the whole namespace**, more than any other
frame.  The biclique tiling that chose its op set was fed by
`analysis/alu_pair_cooccurrence.py` variant 3, which censuses pairable RSD
adjacencies **without asking whether another frame already encodes them**.

So the op set was chosen against a population that includes work
`dual-setup-pair` does for 17 codepoints.  `util/rule_overlap.py` measures the
size of that: of the 34927 pairs `rsd-alu-pair` is credited with, **10221 are
also acceptable to `dual-setup-pair`**, which sits at `RULES[16]` and therefore
never gets the chance to take them.

## Method

Two changes, then the real scheduler and pairer:

* **Demote** the rule to the end of `RULES`, so every other frame claims what
  it can first.  What it then takes is the residue — pairs only it can encode.
  Doing the exclusion by rule order rather than a hand-written list means it is
  whatever the frames actually are today, not a snapshot.
* **Broaden** it — drop the immediate-range gates and the nine-op mnemonic set,
  so the census is not pre-filtered by the very decisions the tiling is meant
  to make.  Structural gates stay (RSD-or-li form, swappable rule, x0..x15,
  distinct destinations): those are what the frame *is*.

The two relaxations are also measured separately, because widening the op set
and widening the immediates are different decisions with different costs.

## What the frame is actually worth

```
population           RV32     RV64     total   combos
narrow-first        14344    20583     34927      101   <- as it ships, at RULES[0]
narrow-last          9689    13617     23306       99   <- as it ships, demoted
wideimm-last        16948    26184     43132      101   <- + unlimited immediates
wideops-last        16718    23628     40346     1069   <- + any ALU op
broad-last          24731    37359     62090     1101   <- both: the residue
```

**A third of its credited hits are a priority artefact.**  Demoting it costs
11621 pairs (33.3%) — work another frame would have taken anyway.  Its honest
standalone value is **23306**, which at 256 codepoints is 91 per codepoint, not
the 136 its raw hit count suggests.

The two relaxations contribute almost equally and almost additively: immediates
+19826, ops +17040, both +38784.  So the current op set and the current
immediate widths are each turning away roughly the same amount of work.

## The re-tiling

`util/biclique_tiling.py` on the residue, held-out scored:

```
  N    cp  split         held-out                  marginal
------------------------------------------------------------
  5    32  b=3 w=1 h=1       65.93%                          
  6    64  b=3 w=1 h=2       75.27% +9.34 pts /   32 cp =  181.2 pairs/cp
  7   128  b=3 w=1 h=3       84.76% +9.48 pts /   64 cp =   92.0 pairs/cp
  8   256  b=3 w=2 h=3       90.11% +5.35 pts /  128 cp =   26.0 pairs/cp

op sets the optimiser picks (count = how many of the 8 tiles want it):

  N=6 (64 cp): 16 distinct A ops, 10 distinct B ops
    A: addi_rsd(5), li(4), add(4), slli(3), addiw(2), srli(2), or(2), andi(2), czero.eqz(1), sh2add(1)
    B: li(3), addi_rsd(3), add(2), czero.eqz(2), czero.nez(1), or(1), slli(1), andi(1), addiw(1), sh2add(1)

  N=7 (128 cp): 25 distinct A ops, 12 distinct B ops
    A: li(6), addi_rsd(6), add(5), andi(5), slli(5), srli(5), or(4), addiw(3), czero.nez(3), mul(3)
    B: li(3), addi_rsd(3), czero.eqz(1), add(1), or(1), czero.nez(1), slli(1), andi(1), srli(1), addiw(1)

  N=8 (256 cp): 29 distinct A ops, 18 distinct B ops
    A: li(5), addi_rsd(5), add(5), addiw(4), slli(4), or(3), czero.nez(3), sub(3), srai(3), mul(3)
    B: li(4), addi_rsd(4), add(3), czero.eqz(2), or(2), slli(2), andi(2), addiw(2), and(2), czero.nez(1)
```

**The marginal return collapses.**  The step to 128 codepoints buys 92 pairs
per codepoint; the step from 128 to 256 buys **25.9**.  The frame's own average
is 91/cp.  So the last half of the block — 128 codepoints, 12.5% of the entire
namespace — is bought at a quarter of the rate of the first half, and at a
worse rate than the frame achieves overall.

## What the op set should contain

residue op alphabet (83): add, add.uw, addi_rsd, addiw, addw, and, andi, andn, bclr, bclri, bext, bexti, binvi, bset, bseti, clz, clzw, cpop, cpopw, ctz, ctzw, czero.eqz, czero.nez, div, divu, divuw, divw, li, max, maxu, min, minu, mul, mulh, mulhu, mulw, mv, negw, or, ori, orn, rem, remu, remuw, remw, rev8, rol, rolw, rori, roriw, sext.b, sext.h, sext.w, sh1add, sh1add.uw, sh2add, sh2add.uw, sh3add, sh3add.uw, sll, slli, slli.uw, slliw, sllw, slt, slti, sltiu, sltu, sra, srai, sraiw, sraw, srl, srli, srliw, srlw, sub, subw, xor, xori, zext.b, zext.h, zext.w

top residue (opA, opB) combinations:
    10114  li           li
     5971  addi_rsd     addi_rsd
     4883  addi_rsd     li
     3262  li           addi_rsd
     1712  srli         czero.eqz
     1355  czero.eqz    czero.nez
     1041  slli         slli
     1025  add          li
      984  sh2add       li
      944  sh3add       li
      935  slli         or
      870  addi_rsd     add
      848  add          add
      769  add          addi_rsd
      679  czero.nez    czero.eqz
      673  li           andi
      622  addiw        li
      600  addiw        addi_rsd
      570  andi         addi_rsd
      565  addi_rsd     sh2add
      560  andi         li
      541  addi_rsd     czero.eqz
      499  sh1add       li
      416  or           li
      384  li           add

wrote /home/user/ciscv_experiment/results/rsd_*.csv (+_ops.json). Tile with:
  python3 util/biclique_tiling.py --table /home/user/ciscv_experiment/results/rsd_broad_last.csv --N 8

The ops the optimiser reaches for that the **current nine-op set does not
have**: `czero.eqz`/`czero.nez`, `mul`, `sh2add`/`sh3add`, `sub`.  The current
set is `add, addi, addiw, and, andi, or, slli, srli, xor` — and `xor` and `and`
never appear in the tiles at all, while `czero.*` shows up in every budget from
64 codepoints upward.

## The caveat that has to be resolved before acting

**The residue is measured with unlimited immediates, so it counts pairs no
32-bit packet could encode.**  `li, li` is the single largest residue cell at
10114 — it survives exclusion by `dual-setup-pair` precisely because those
pairs' immediates are too wide for it, and two wide immediates plus two
register fields plus an op selector do not fit in 32 bits either.

The decomposition bounds the damage: at most the +19826 attributed to
immediates is suspect, and the `wideops-last` row (40346, +17039 over
narrow-last with immediates still gated) is the conservative population.  A
tiling on `results/rsd_wideops_last.csv` rather than `rsd_broad_last.csv` is
the pessimistic reading, and the honest answer is between them.

That is the next measurement, not a conclusion available now: the residue needs
the same joint (immA, immB) grid treatment `chain_imm_grid` gave the pointer
chases, so op-set choice and immediate width can be traded against each other
instead of one being assumed while the other is swept.

## WITHDRAWN: the uniform-immediate sweep costed range wrongly

An earlier version of this section swept a uniform 5/6/7-bit immediate across
the whole op set and costed an A x B frame at `|A| x |B|` codepoints, one entry
per op. **That is not how immediate range is paid for.** A field is five bits
per register column the ROW draws, and an op reaches past that only by
occupying more opcode-list entries: an op declaring N bits takes `2^(N-field)`
of them. `rsd-alu-pair`'s rows draw five bits, so

    reg-reg op, or 5-bit immediate op        weight 1
    6-bit                                    weight 2
    7-bit  (`addi` and `li`, as declared)    weight 4
    8-bit                                    weight 8

and the block is `weight(A) x weight(B)`. That is exactly where today's 256
comes from — ten ops per slot, but `addi` and `li` at seven bits cost four
entries each, so the slot weighs `4 + 4 + 8x1 = 16` and `16 x 16 = 256`. **Two
ops buy half the frame.**

The withdrawn section claimed a 6-bit immediate at 64 codepoints covered 24231
against today's 23306 — i.e. that a quarter of the block beat the whole of it.
Correctly costed it does not beat it, it *matches* it (below). The direction
survives; the margin was an artefact of pricing range as free.

Raw output of the superseded sweep is kept in `rsd-imm-sweep.txt`; the
population and order-free figures in it are sound, only the costing was wrong.

## Choosing per-op widths against a weight budget

`util/rsd_weighted.py` optimises the real thing: a width **per op** (or reg-reg
only), against a weight cap per slot, exploiting the 87.1% order-freedom so a
pair needs only one of its two orientations. Because cost is a weight product,
"add another reg-reg op" and "give `li` one more bit" compete on one scale —
which is the trade a uniform sweep cannot see.

It also disposes of the unlimited-immediate problem without a hand-written
cutoff: a pair needing a twenty-bit immediate is never selected because
reaching it would cost 2^15 entries. The budget does the filtering.

**Model check first.** Today's declared policy weighs 16 per slot for a 256
block — reproducing the frame's actual cost exactly, so the cost model is
right. It covers 20619 of this residue, against the 23306 the same rule takes
when demoted in its own run (88.5%). That gap is population, not model: the two
are separate scheduler runs, the broadened rule takes different pairs, and
greedy pairing is not monotone. **So every figure below is against 20619 —
today's op set scored on this same population — not against 23306.**

```
  wA  wB  block   covered       %  vs today
   4   4     16     12394   20.0%     -8225
   8   4     32     15938   25.7%     -4681
   4   8     32     15862   25.5%     -4757
   8   8     64     20574   33.1%       -45
  16   8    128     26702   43.0%     +6083
   8  16    128     26889   43.3%     +6270
  16  16    256     32585   52.5%    +11966
```

**A 64-codepoint frame matches today's 256-codepoint one** — 20574 against
20619, a difference of 45 pairs out of 62090. At the same 256 block a re-chosen
set covers **32585, 58% more** than the shipped one.

### The slots want opposite things

```
8x8 = 64 cp
  A (8): add, addi:5b, addiw:5b, czero.nez, li:5b, slli:5b, srli:5b, sub
  B (8): addi:6b, add, andi:5b, czero.eqz, li:5b, or, slli:5b

16x16 = 256 cp
  A (16): slli:6b, add, addi:5b, addiw:5b, andi:5b, czero.nez, li:5b, mul,
          or, sh1add, sh2add, sh3add, slliw:5b, srli:5b, sub
  B (16): li:8b, addi:7b, add, czero.eqz, or, slli:5b
```

At 64 codepoints the optimiser buys almost no range at all — everything is
weight 1 bar one 6-bit `addi` — and spends the budget on op VARIETY.

At 256 the two slots diverge sharply. **A takes breadth**: fifteen ops, nearly
all weight 1, reaching for `mul`, `sh1add`/`sh2add`/`sh3add`, `sub`, `or`,
`czero.nez` — none of which the declared nine contains. **B takes depth**:
just six ops, but `li` at eight bits costs weight 8 and `addi` at seven costs
4, so twelve of B's sixteen weight goes on two ops.

That asymmetry is only available because order is free 87% of the time — the
scheduler can put the wide-immediate operand in B and the exotic ALU op in A.
A symmetric frame cannot do it, and neither can a uniform width.

### What is still not settled

The greedy is alternating-maximisation, not optimal, so these are lower bounds
on what each budget can reach. The comparison is on the demoted residue, which
is what the frame is worth if it goes LAST in `RULES` — still an open design
question. And the row would need redrawing to carry the chosen widths before
any of this is encodable as measured.

## ADOPTED — and what it actually did

The 16x16 policy is now the frame (`encoding.yaml`, `scheduler/rules.py`).

```
A (weight 16, 15 ops): add sub mul or sh1add sh2add sh3add czero.nez
                       addi:5 li:5 addiw:5 andi:5 slli:6 slliw:5 srli:5
B (weight 16,  6 ops): add or czero.eqz  li:8 addi:7 slli:5
```

`and` and `xor` are gone — the optimiser never picked either at any budget.
The block is 256, exactly what the symmetric set cost, so this is cost-neutral
by construction.

| | before | after |
|---|---|---|
| `rsd-alu-pair` hits | 34927 | **46340** |
| its exclusive pairs | 23306 | **36303** |
| excl/cp | 91.0 | **141.8** |
| corpus pairs | 505255 | **513095** |
| TOTAL to parity | 144262 | **136422** |

**+7840 corpus pairs, no corpus regressed, for the same 256 codepoints.**  The
exclusive count — the like-for-like figure, since the op set was chosen on the
demoted residue — went 23306 to 36303, +56%, against the +58% the weighted
optimiser projected.  The projection held.

Frames it takes from: `dual-setup-pair` still co-accepts 8350 of its pairs
(down from 10221), and corpus-wide overlap fell 27.8% -> 26.7%.

### Two things this did NOT settle

The frame is still `RULES[0]`.  Its op set was chosen against the residue it
would see if demoted, and it is not demoted — so it still harvests 8350 pairs
`dual-setup-pair` could take for 17 codepoints.  Whether to reorder them is
open, and unchanged by this.

~~`uses_low_regs` still clamps registers to x0..x15~~ — CHECKED, and it does
not.  `_RSD_ALU_REGS` is `frozenset(range(32))`, so `_confirm_low_regs` tests
membership in a set containing every register and can never reject: the clamp
was already vacuous, and the yaml note's 377 pairs had been reclaimed earlier
by widening that set.  The note is accurate and past tense; I read it as a live
discrepancy and it was not.

Removing `@uses_low_regs` from the frame changed the corpus by **exactly zero
pairs** (513095 before and after, no frame moved), which is the measurement
that settles it.  It is gone anyway, because a decorator that looks like a
constraint and is not is worse than no decorator.  The same vacuity covers
`alu-alu-chain`, `load-alu-chain`, `alu-store-chain` and `arith-jump-pair`;
every register operand in every frame row sits in a 5-bit column, which
`tests/test_conformance.py` now gates so the clamp cannot silently become
load-bearing again.

So the figures above are NOT floors for this reason — an earlier version of
this section said they were.
