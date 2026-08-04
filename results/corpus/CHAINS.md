# The pointer-chase frames: the split, and how wide the offset should be

Measured 2026-08-04 against main.  Regenerate the sweep with
`python3 util/chain_width_sweep.py --widths 6,7,8,9,10,11,12 --verify`
and the hit counts with `util/rule_hits.py` / `util/rule_overlap.py`.

## Why they had to be split

`deref-load-chain` and `base-load-chain` were one frame named
`"deref-load-chain, base-load-chain"`, drawing two rows:

    │o│imma[9:5]│o│imma[4:0]│  rs1a   │o o o│   rdb   │0 1 1 0 o│1 0│
    │o│immb[9:5]│o│immb[4:0]│  rs1a   │o o o│   rdb   │0 1 1 0 o│1 0│

Both rows carried the **same identifier** and the same `aaabbb` op-select over
one 49-codepoint block, and nothing anywhere in the word said which row was in
force.  A decoder holding the word could not tell whether the 10-bit field was
the first load's offset or the second's — which is the entire difference
between the two forms.  The yaml said as much, in the form of a standing
`TODO: decide how to balance imma and immb sizes`.

They are two frames now, identifiers `0111` and `0110`, one 64-block each.
That costs **+64 codepoints** (838 → 902 of 1024 reserved, 122 spare).

The split changed accounting only, not pairing: the two frames take 8484 pairs
between them, exactly what the combined frame took.  What changed is the
honest price.  The old figure of 8484 hits over 49 codepoints — 173.1 per
codepoint — was never achievable, because the encoding it was billed against
could not distinguish the two forms.  The real figure is 8484 over 98, **86.6
per codepoint**.

## What each form is worth

| frame | cp | hits | reach | excl | excl/cp | RV32 | RV64 |
|---|---|---|---|---|---|---|---|
| `base-load-chain`  | 49 | 6017 | 6792 | 6011 | 122.7 | 2405 | 3612 |
| `deref-load-chain` | 49 | 2467 | 2467 | 1676 |  34.2 |  943 | 1524 |

**The offset sits on the second load 71% of the time.**  That asymmetry is the
main thing the split reveals: as one frame the two were indistinguishable in
the hit counts as well as in the encoding, and the roster carried them as a
single 173-per-codepoint entry that hid a 3.6x spread between its halves.
`base-load-chain` earns its block comfortably; `deref-load-chain` at 34.2
excl/cp sits in the bottom third of the roster.

The ratio holds on both bases (RV32 2.6x, RV64 2.4x), so it is a property of
the chase, not of a word size.

### The two frames are not quite disjoint

Each demands the *other* load's offset be zero, so a chase carrying two real
offsets fits neither and one carrying a single offset fits exactly one.  The
exception is the chase with **no offset at all** — both loads at zero — which
satisfies both rules.  That is 775 pairs, credited to `deref-load-chain` only
because it comes first in `RULES`; they are the whole of the gap between
`base-load-chain`'s reach (6792) and its hits (6017).

So `deref-load-chain`'s own exclusive population is **1676**, not 2467.  Nearly
a third of what it appears to earn is a tie it wins on rule order.  Whoever
gets those 775, they cost nothing extra: they fit at every width, and both
frames must handle a zero offset anyway.

## How wide should the offset be?

A field is five bits per register column it consumes and grows past that only
by taking more opcode entries — an op declaring N bits occupies `2^(N-field)`
of them.  Both frames draw 10 bits from `funct5`+`rs2`, columns the pair leaves
free because `tmp` is implicit.  So:

* **at or below 10 bits the width is free** — the columns are already drawn,
  and narrowing cannot spend them on anything else;
* **above 10 every bit doubles the block**: 49 → 98 codepoints at 11 (block
  128), 196 at 12 (block 256).

Full corpus, 14 builds, real scheduler and pairer at each setting:

```
  w     deref    base    both     total   d.cp   b.cp  block
--------------------------------------------------------------
  6      2463    4893    7356    502425     49     49     64
  7      2465    5901    8366    503417     49     49     64  +992
  8      2466    6017    8483    503529     49     49     64  +112
  9      2467    6017    8484    503529     49     49     64  +0
 10      2467    6017    8484    503529     49     49     64  +0
 11      2467    6017    8484    503529     98     98    128  +0
 12      2467    6017    8484    503529    196    196    256  +0
```

`total` is all corpus pairs, not the two frames' own — a width that gains pairs
by taking them from a frame that would have had them anyway has gained nothing.
Here the two move together, so the width is buying real pairs up to the point
it stops.

**Demand saturates at 9 bits, and 8 is within one pair of it.**  Every bit
above that is provably worthless: 11 and 12 bits buy *zero* pairs for a
doubling and a quadrupling of the block.

The two curves are very different, and that is the answer to the balance
question the yaml asked:

| | 6 bits | 7 | 8 | 9 | saturates at |
|---|---|---|---|---|---|
| `deref-load-chain` | 2463 | 2465 | 2466 | 2467 | **9**, but flat from 6 — the whole range is 4 pairs |
| `base-load-chain`  | 4893 | 5901 | 6017 | 6017 | **8**, and it costs 1124 pairs to drop to 6 |

`deref-load-chain` needs almost no range at all: 99.8% of its population fits
in six bits.  `base-load-chain` is where the width goes — dropping it from 8 to
6 costs 1124 pairs, 281 times what the same cut costs its sibling.  A pointer
chase that offsets the *first* load is indexing a small header; one that
offsets the *second* is reaching into a struct, and structs are bigger than
headers.

### What to set

**Keep both at 10.**  It is free at the current draw, it is above the measured
saturation with headroom for codebases unlike this corpus, and narrowing to the
measured 9/8 would save nothing — the columns cannot be spent elsewhere.

The actionable half of the result is the ceiling, not the floor: **never widen
either frame past 10**, because the corpus says a doubling would buy nothing.
And if these two are ever pushed back into one block with a selector bit, the
budget should not be split evenly — give `base-load-chain` the bits and leave
`deref-load-chain` six.

### Independence

Both curves come from single runs at `(wa, wb)`, which is only legitimate if
widening one form cannot move the other.  Checked rather than assumed, in both
directions:

```
  wa=12  wb=6    deref    2467  base    4893  total   502425
  wa=6   wb=12   deref    2463  base    6017  total   503529
```

Each frame's count matches its own width's row exactly and ignores the other's
— 2467 is the `w=12` deref, 4893 the `w=6` base, and so on.  The no-offset
population they share fits at every width, so it never moves.
