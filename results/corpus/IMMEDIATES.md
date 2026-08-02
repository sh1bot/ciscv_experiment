# Immediate widths: what each frame can afford, and what it needs

> **HISTORICAL SNAPSHOT.**  This table predates the width-honesty pass and the
> mem-pair sp/base split: every width it lists as `bare` is now declared, the
> ranges it flags in `rules.py` are fixed, and `mem-pair`'s sp traffic lives in
> its own `mem-pair-sp` frame.  The *method* stands; regenerate the numbers
> with `util/achievable.py` and `util/needed.py` before relying on any.

Two measurements, per frame and slot.

**Achievable** — the field is drawn from register columns (5 bits, or 10 from
two), and extra range is bought by repeating the opcode, priced by
`opcode_codepoints`. `util/achievable.py` redeclares every immediate-carrying op in a slot at
each candidate width and reads the block size that results.

**Needed** (`util/needed.py`) — the width the encoded field would actually have to be, over every
pair the scheduler takes on musl-rv32: memory offsets divided by their access
width (the field is width-scaled), `addi4spn` by 4, everything else as-is.
Branch and jump displacements are unresolved labels in the corpus, so they are
skipped rather than guessed.

**The need figures are CENSORED and read low.** They cover only pairs the
current rules accept, and most rules already reject immediates wider than the
frame allows. A frame showing "100% at 7 bits" may mean its rule caps at 7, not
that nothing wider exists. Treat the columns as "where the current width binds",
not as the true population.

## The table

`drawn` is the field width in register columns; `demand/budget` is codepoints
used against the block reserved.

```
frame                      slot drawn declared   demand/budget    5b     6b     7b     8b    10b
--------------------------------------------------------------------------------------------------
mem-pair                    a/b     5     bare         11/16    49.4%  60.8%  73.4%  85.5% 100.0%
pre-inc-pair                  a     5     bare           8/8    40.8%  42.3%  42.3%  45.1%  63.4%
chain-li-branch               a     5     li:6         12/16    41.5%  47.6%  80.3% 100.0% 100.0%
chain-bit-test-branch         a     5   andi bare      20/32    53.9%  72.0%  97.6%  97.6% 100.0%
load-sp-branch                a     5     bare         14/16    70.1%  87.3%  90.3%  94.0% 100.0%
rsd-alu-pair                a/b     5   addi:7,li:7   256/256   72.9%  83.4% 100.0% 100.0% 100.0%
chain-alu-pair              a/b     5    addi:6       256/256   78.1% 100.0% 100.0% 100.0% 100.0%
arith-mem-pair                a     5     bare         55/64    92.3%  94.9% 100.0% 100.0% 100.0%
addi-branch-pair              a     5    addi:6        24/32    91.2% 100.0% 100.0% 100.0% 100.0%
dual-indep-pair             a/b     5     li:6         17/32    92.6%  99.8% 100.0% 100.0% 100.0%
load-chain-alu-pair           a     5   ld:6,lw:6      64/64    96.6%  99.5%  99.5%  99.7% 100.0%
arith-jump-pair               a     5    addi:5        40/64    97.9% 100.0% 100.0% 100.0% 100.0%
store-chain-alu-pair          b     5     bare         32/32    94.0%  97.0%  97.7%  99.2% 100.0%
post-inc-addi-pair            b     5     bare           4/8    96.4%  98.3%  99.8% 100.0% 100.0%
index-chain-mem-pair          b     5     bare           8/8   100.0% 100.0% 100.0% 100.0% 100.0%
--- fields already drawn 10 wide (two columns): free to 10 bits, 4x at 12 ---
addi-store-pair               a    10    addi:10         4/4    64.6%  73.1%  85.5%  90.6% 100.0%
prologue-pair                 b    10     bare           2/2    78.4%  88.8%  94.4%  97.1% 100.0%
epilogue-pair                 a    10     bare           2/2    64.1%  78.8%  90.5%  95.0% 100.0%
li-czero-pair                 a    10     li:10          2/2    53.8%  73.8%  81.5%  87.7% 100.0%
mvload-jump-pair              a    10     bare         15/16    98.5%  99.2%  99.7%  99.9% 100.0%
```

## What this says

**The 10-bit group is already right.** A field spanning two register columns
needs no extension at all, and four of those five frames would be badly served
by anything narrower — `li-czero-pair` needs 10 bits for 46% of its pairs,
`epilogue-pair` for 36%. They cost 2 or 4 codepoints each. This is the shape
the rest should aspire to where a spare column exists.

**`mem-pair` is the whole problem.** 10591 pairs per slot on musl-rv32 — an
order of magnitude more than anything else in the table — and only 49.4% fit
5 bits. It is also one of the two frames taking width without declaring it.
Its SP rows already draw 10 bits and are fine; the base-register rows draw 5
(plus an unfunded `g`), and that is where the loss sits.

Cost to declare it honestly: each cluster is a singleton `{a:[x], b:[x]}`, so
6 bits makes every cluster weigh 2x2 and demand goes 11 -> 44, block 16 -> 64,
**+48 codepoints against 12 spare**. But `mem-pair`'s immediate is SHARED — one
`imm` field, not `imma`/`immb` — and the model multiplies the slot weights as if
the extension were bought on each side independently. A shared field should pay
`2^ext` once: demand 22, block 32, **+16**. Resolving that is worth 32
codepoints on this frame alone, and it gates the decision.

**Cheap and clearly worth doing:**

  * `chain-bit-test-branch` — `andi` is bare on a 5-bit field and only 53.9% of
    its pairs fit. Declaring 6 bits buys 18 points, 7 bits buys 44, and the
    frame has 12 codepoints of headroom inside its existing 32-block.
  * `load-sp-branch` — bare loads, 70.1% at 5, 87.3% at 6. Only 2 codepoints of
    headroom, so 6 bits needs the block doubled: +16.

**Expensive, and the reason to be careful:**

  * `chain-li-branch` — needs 8 bits to reach 100%, and `rules.py` currently
    grants 8 against a declared 6. Honest at its 16-block is 6 bits = 47.6%,
    i.e. today's count is roughly twice what the encoding can hold. Buying 7
    bits costs +16, 8 bits costs +48.
  * `pre-inc-pair` — the worst fit anywhere: 40.8% at 5, still only 63.4% at 10,
    with zero headroom in its 8-block. Consistent with the 35.1% encodability
    `encoding_verify` reports. This frame needs a redesign, not a wider field.

**Nothing to reclaim by shrinking.** Only `post-inc-pair` is over-provisioned
(4 used of 8), and blocks are powers of two, so it returns 4 codepoints. Every
other frame either fills its block or would still need it after rounding.

## Suggested order

1. Fix the shared-immediate double-charge in `opcode_codepoints`. It is the
   difference between `mem-pair` costing 16 and costing 48, and `mem-pair` is
   the largest immediate population we have by a factor of ten.
2. Declare `chain-bit-test-branch`'s `andi` at 6 or 7 bits — free inside its
   existing block.
3. Then decide `mem-pair` and `chain-li-branch` against whatever the corrected
   model says, with the 12 spare plus 4 from `post-inc-pair`.
4. Re-run the need table with each rule's cap lifted, so the columns stop being
   censored by the very limits we are trying to set.
