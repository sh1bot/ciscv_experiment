# Immediate widths: budget against return, frame by frame

Who is paying for immediate range their population does not use, and who is
starved by a cap that a cheap bit would fix.  Three measurements:

**Uncensored census.**  Every scheduled-pair width table before this one was
censored: rules reject immediates wider than the frame allows, so "100% fit"
can mean "the cap binds", not "the population fits".  For this study every
width cap in `rules.py` was lifted to 13 bits in a scratch tree and
`util/needed.py` run over musl-rv32 + sqlite-rv64.  The ceiling: uncapped,
musl-rv32 schedules 31949 pairs (+17.8% over 27124) and sqlite-rv64 47108
(+16.6% over 40410) — the total mass beyond today's widths, most of it in
tails that cost exponential codepoints.  Census populations are
attribution-shifted (pairs migrate between rules when caps lift), so its
percentages are trusted and its absolute pair counts are not; every
recommendation below was re-measured with only the candidate widened.

**Extension audit.**  From the yaml: 276 of the 1018 reserved codepoints are
opcode-duplication purchases of range above the drawn field.

**The concession.**  A 5- or 10-bit field is the natural size — it costs
nothing beyond the register columns the frame drew anyway, so slack inside it
is free and is not flagged.  (No, we are not reclaiming two bits of a natural
field at the cost of format regularity.)  Judged here are only the two things
that carry a price: duplication purchases, and caps that measurably starve.

## Where the extension money sits

```
frame                     entries bought   what for            return
rsd-alu-pair                 +156          addi@7, li@7 x2     ~5.5 pairs/cp
load-alu-chain           +34          ld/lw@6, addi@6     ~5.6 pairs/cp
alu-alu-chain                +31          addi@6 x2           FREE (block unchanged)
li-branch-chain               +18          li@7                ~20 pairs/cp
addi-branch-pair              +12          addi/addiw@6        ~2 pairs/cp
bit-test-branch-chain         +12          andi/slli/srli@6    ~12 pairs/cp
indep-pair               +11          li@6, addi4spn@6    ~10 pairs/cp
alu-store-chain           +2          addi@6              FREE (block unchanged)
```

"FREE" means the frame's block is the same size with or without the extension
— the bits are riding rounding slack, and there is nothing to reclaim.

## Verdicts

### Too much — paying for range the population does not use

* **`addi-branch-pair` `addi@6` — egregious.**  Twelve entries buy the
  population's 52.9% → 56.6%: about 25 pairs across two corpora, ~2 per
  codepoint against a portfolio floor around 6.  And no affordable width
  rescues it — the census reaches only 77.4% at TWELVE bits, because
  compare-and-branch constants have a long flat tail.  Dropping to 5 bits
  halves the block: **reclaim 16 codepoints for ~25 pairs.**

* **`rsd-alu-pair` `addi/li@7` — the expensive question, but held.**  156
  entries (61% of all extension spending) buy roughly 850–1000 pairs, ~5.5
  per codepoint — below what the average frame earns.  But demand exactly
  fills its 256-block, retreat to @6 reclaims nothing (144 still rounds to
  256), and only full retreat to @5 frees 128 codepoints, at market-rate
  return forgone.  The widths were chosen by the biclique search over this
  population; hold unless 128 codepoints are needed for something measured
  better.

* **`load-alu-chain` +34 — borderline, held.**  ~190 pairs, ~5.6/cp
  forgone if dropped (reclaims 32).  At the portfolio margin either way.

### Too little — starved, and cheap to fix (measured, not censused)

One scratch run with only these widened, against current:
**musl-rv32 27124 → 27247, sqlite-rv64 40410 → 40759: +472 pairs for +12
codepoints (~39/cp).**

* **`arith-jump-pair` imm ops 5 → 6: FREE.**  Demand 40 → 64 inside its
  existing 64-block.  Census 73.7% → 79.7% of 4861.
* **`mem-base-pair` base offset 5 → 6: +8 codepoints** (block 8 → 16).  Census
  87.8% → 94.1% of 10326.  The post-split narrowing to 5 was itself set from
  censored data; the true base population wants the sixth bit.
* **`post-inc-pair` stride 5 → 6: +4 codepoints** (block 4 → 8).  Census
  stride fit 73.3% → 85.2% of 1423.  This is also where the `k`-scale
  disagreement between `rules.py` and `encoding_verify` lives (TODO A1.7) —
  settle that when taking this.
* **`arith-mem-pair` `addi` 5 → 6: FREE but nearly worthless** — measured at
  +19 pairs on top of the +472 (demand 55 → 63 inside its 64-block).  The
  census's 54.9% → 63.0% was attribution inflation; the frame's real matched
  population is small.  Take it only because it is free.

The arithmetic writes its own funding note: the +12 codepoints these need
exceed the 6 spare, and `addi-branch`'s reclaim of 16 covers them with room
left over — the one egregious over-provision pays for every verified
under-provision.

* **`li-branch-chain` li 7 → 8 is the best big-ticket buy left**: +32
  codepoints (block 32 → 64) for census 66.9% → 85.3% of 2293, roughly +420
  pairs at ~13/cp.  Priced, not recommended — it needs a block nothing
  currently funds.

### Structurally starved — no affordable width fixes them; do not widen

* **`pre-inc-pair` A stride**: 26.4% at 5 bits and still 56.2% at TWELVE.
  Pre-increment addends are offsets into running values, not small constants.
  The frame needs a redesign or acceptance, not bits.
* **`alu-store-chain` A value**: 42.6% at 5, 68.5% at 12 — stored
  constants are wide.  Its @6 extension is free, keep it; nothing else helps.
* **`addi-branch-pair` A**: see above — the tail is the problem.
* **`arith-mem-pair` B offset**: the rows draw no `immb` field at all, and
  the census says that constraint, not any width, is the binding one — with a
  B offset allowed the frame's matched population is ~10x larger (90 → 2828
  per corpus).  That is a row-layout question (a column would have to come
  from somewhere), recorded here as the frame's real ceiling.

### Right-sized (the concession list — natural fields, no purchase, good fit)

`load-base-branch-pair` (100.0% at its 5 bits — perfectly cut), `index-mem-chain`
(97.9%), `deref/base-load-chain` (96.8–100% at 10), `mem-sp-pair` (100% at
10, and needs all ten: 41% at 5), `setup-jump` (100% at 10),
`addi-store-chain` A (98.2% at 10), `prologue` (98.5%), `epilogue` (100%),
`li-czero` (94.9% at 10), `indep` (99.8% at its declared 6),
`alu-alu-chain` (@6 free), `bit-test-branch-chain` (@6 earning ~12/cp; the 7th bit
would cost +32 for ~5.5/cp — skip).

`load-sp-branch-pair` is excluded from verdicts: its sp/base split (TODO A9) will
change both its field and its population.

## Summary

The immediate budget is broadly honest after this year's enforcement work:
most fields are natural sizes with real occupancy, and the largest extension
purchases either fill rounding slack (free) or were optimized against their
population (`rsd-alu`).  The one egregious over-provision is
`addi-branch-pair`'s sixth bit (~2 pairs/codepoint against a hopeless tail);
the verified under-provisions total +12 codepoints for +491 pairs (measured:
27124 → 27252 and 40410 → 40773) and are more than funded by reclaiming it.
