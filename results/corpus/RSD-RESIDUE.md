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

## Shaving the immediate limit, and choosing A and B separately
The residue above was measured with UNLIMITED immediates, which counts pairs
no 32-bit packet could hold.  Re-measured with a uniform N-bit immediate on
both slots instead — signedness per `analysis/imm_traits`, so shifts stay
unsigned and arithmetic signed — the population is honest:

```
population      RV32     RV64     total   order-free
narrow-last     9689    13617     23306        (today's frame, demoted)
imm5-last      13518    18209     31727         85.8%
imm6-last      14572    21566     36138         86.0%
imm7-last      16750    25119     41869         87.0%
broad-last     24731    37359     62090         87.1%   <- unlimited, inflated
```

**Order is free in ~86% of the residue.**  `rsd-alu-pair` packs two independent
results, and nothing in it forces an order except when one op reads the other's
destination.  Where order is free the frame does not need both orientations
encodable — the scheduler can emit whichever one the A and B sets allow.  So A
and B can be chosen separately, and a pair `{x, y}` counts as covered if
`x∈A, y∈B` OR `y∈A, x∈B`.

`util/rsd_residue.py` optimises the two sets against exactly that objective by
alternating maximisation.  `util/biclique_tiling.py` cannot: it works on the
ordered matrix, so it must cover both orientations, but in exchange it can use
`b>0` block structure that a single A×B tile cannot.  Both are reported.

```
                single tile A x B          biclique tiler (b=3)
imm    pop      64cp    128cp   256cp      64cp    128cp   256cp
 5   31727     63.4%   76.9%   83.8%     67.6%   77.5%   86.0%
 6   36138     67.1%   78.0%   85.2%     67.9%   78.6%   86.4%
 7   41869     71.3%   79.8%   86.3%     70.5%   80.5%   87.6%
 inf 62090     76.3%   83.0%   88.6%     75.3%   84.8%   90.1%
```

At 64 codepoints the order-free single tile **beats** the block tiler on the
wider immediates (71.3% vs 70.5% at 7 bits, 76.3% vs 75.3% unlimited):
exploiting order-freedom is worth about as much as block structure, and the two
are not combined here.  At 256 the tiler's extra shape wins by 1–2 points.

### The number that matters

Today's frame covers **23306 pairs for 256 codepoints**.  Against the same
demoted, exclusion-corrected population:

| | covered | codepoints |
|---|---|---|
| today (`narrow-last`, 9 ops, per-subform immediates) | 23306 | **256** |
| 6-bit immediate, A×B = 8×8 | **24231** | **64** |
| 7-bit immediate, A×B = 8×8 | **29865** | **64** |
| 7-bit immediate, A×B = 16×8 | 33422 | 128 |

**A 64-codepoint frame with a 6-bit immediate covers more than the current
256-codepoint frame does.**  At 7 bits it covers 28% more for a quarter of the
namespace.  Both fit the word: two RSD ops need `rd`(4) + imm(N) twice, so 7
bits leaves 32 − 22 − 2 = 8 bits of op-select, which is exactly the 256 an
8×8×(b=3) block wants.

### The sets themselves

**5-bit immediate**
- `8x8` (64 cp) — 20127 pairs, 63.4%
  - A: add, addi_rsd, addiw, czero.eqz, li, or, slli, srli
  - B: add, addi_rsd, czero.eqz, czero.nez, li, or, slli, sub
- `16x8` (128 cp) — 24411 pairs, 76.9%
  - A: add, addi_rsd, addiw, andi, czero.eqz, czero.nez, li, mul, or, sh1add, sh2add, sh3add, slli, slliw, srli, sub
  - B: add, addi_rsd, czero.eqz, czero.nez, li, or, slli, srli

**6-bit immediate**
- `8x8` (64 cp) — 24231 pairs, 67.1%
  - A: add, addi_rsd, czero.nez, li, or, slli, srli, sub
  - B: add, addi_rsd, addiw, andi, czero.eqz, li, or, slli
- `16x8` (128 cp) — 28174 pairs, 78.0%
  - A: add, addi_rsd, addiw, andi, czero.eqz, czero.nez, li, mul, or, sh1add, sh2add, sh3add, slli, srai, srli, sub
  - B: add, addi_rsd, czero.eqz, czero.nez, li, or, slli, srli

**7-bit immediate**
- `8x8` (64 cp) — 29865 pairs, 71.3%
  - A: add, addi_rsd, addiw, andi, czero.nez, li, slli, srli
  - B: add, addi_rsd, czero.eqz, li, or, sh2add, slli, sub
- `16x8` (128 cp) — 33422 pairs, 79.8%
  - A: add, addi_rsd, addiw, andi, czero.eqz, czero.nez, li, mul, or, sh1add, sh2add, sh3add, slli, srai, srli, sub
  - B: add, addi_rsd, czero.eqz, czero.nez, li, or, slli, srli

`add`, `addi_rsd`, `li`, `or`, `slli`, `srli` and `czero.eqz`/`czero.nez` are in
every set at every width.  `czero.*`, `mul`, `sh1add`/`sh2add`/`sh3add` and
`sub` are NOT in the frame's declared nine — and `xor` and `and`, which are,
appear only once the sets grow past 8.
