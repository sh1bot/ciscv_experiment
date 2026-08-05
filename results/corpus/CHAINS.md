# The pointer-chase frames: the redesign, and how the offset is split

Measured 2026-08-05 against main.  Regenerate with `util/rule_hits.py`,
`util/rule_overlap.py`, `util/chain_imm_grid.py --corpora ...` and
`util/chain_width_sweep.py --verify`.  Raw output in `chain-imm-grid.txt`
and `chain-width-sweep.txt`.

The two frames are now

    load0-load10-chain   lx tmp, 0(rs1a)       ; load rdb, k*immb(tmp)
    load5-load5-chain    lx tmp, k*imma(rs1a)  ; load rdb, k*immb(tmp)

replacing an earlier `deref-load-chain` / `base-load-chain` pair, which in turn
replaced a single frame that drew both of their rows over ONE op-select with
nothing selecting between them — so the offset in the word could not be
attributed to a load at all.

## The A slot spends one opcode, not seven

`must_chain_base` makes A's loaded value B's base ADDRESS.  A byte or a
halfword is not an address, so A can only ever be the natural word — and the
corpus agrees without a single exception:

| population | chains | A mnemonic |
|---|---|---|
| RV32 on-axis / off-axis / unencodable | 3334 / 959 / 95 | `lw` **100.0%** each |
| RV64 on-axis / off-axis / unencodable | 5108 / 1946 / 141 | `ld` **100.0%** each |

All 11583 chains the pairer can form, measured with the offset conditions
removed so the off-axis population is visible too.  So A is `lx`, the
XLEN-switchable opcode `mem-sp-pair` already uses, and each block is 1x7 = 7
codepoints instead of 7x7 = 49.

## What the frames were missing

Each old frame required one offset to be zero, so between them they could only
ever encode the axes of the A x B plane.  `chain_imm_grid` removes the offset
conditions — keeping every structural gate — to show the joint demand:

| region | chains | share |
|---|---|---|
| B axis (A offset zero) | 6793 | 58.6% |
| A axis (B offset zero) | 2425 | 20.9% |
| both zero (counted in both above) | 776 | 6.7% |
| **off the axes, both nonzero** | **2905** | **25.1%** |
| negative or unaligned — unencodable at any width | 236 | 2.0% |

A quarter of all pointer chases were unreachable by construction.

### `sp` as the base is the discriminator

| rs1a | chains | A-only | B-only | both nonzero |
|---|---|---|---|---|
| `== sp` | 1664 | 574 (34%) | **17 (1%)** | **961 (58%)** |
| `!= sp` | 9919 | 1075 (11%) | **6000 (60%)** | 1944 (20%) |

Two idioms in one shape.  An sp-based chase reads a pointer *out of a stack
slot*, so the slot displacement is an A offset by construction — it is almost
never B-only.  A non-sp chase loads from `0(reg)` and reaches into a struct.
That is why one frame pinning `imma` to zero cannot be the whole story.

## Realised result

| | before | after |
|---|---|---|
| `load0-load10-chain` (was `base-load-chain`) | 6017 hits, 49 cp | **6794 hits, 7 cp** |
| `load5-load5-chain` (was `deref-load-chain`) | 2467 hits, 49 cp | **4039 hits, 7 cp** |
| both | 8484 over 98 cp | **10833 over 14 cp** |
| excl/cp | 122.7 / 34.2 | **969.7 / 574.3** |
| namespace reserved | 902/1024 | **790/1024**, 234 spare |
| corpus pairs | 503529 | **505255** |

**+2349 chain pairs and 84 codepoints back.**  Note the corpus total rose by
only 1726, not 2349: about 620 of the chain frames' gain is taken from pairs
other frames would have had anyway.  That gap is the reason the sweep below
scores on corpus total rather than on the frames' own counts.

`rule_overlap` reports `hidden` 0 for both frames — neither shadows the other,
as the predicates guarantee (`imma == 0` versus nonzero).  The old pair had 775
pairs that either could take, resolved only by `RULES` order.

## How the ten bits should be split

Only `load5-load5-chain` has a choice.  Its ten bits come from `funct5`+`rs2`
— free because `tmp` is implicit — so a bit given to `imma` is taken from
`immb`, and **every split summing to ten costs the same 7 codepoints**.  This
is not a cost trade; it is purely about which division catches the most.

```
 imma immb   off-chain    wide    both     total   cp  block
    2    8        2066    6869    8935    504004    7      8
    3    7        3050    6814    9864    504635    7      8
    4    6        3669    6803   10472    505032    7      8
    5    5        4039    6794   10833    505255    7      8
    6    4        3764    6794   10558    505073    7      8
    7    3        2937    6794    9731    504522    7      8
    8    2        2426    6793    9219    504104    7      8
```

**5+5, and it is a clean single peak** — 505255 corpus pairs, falling away
symmetrically in both directions.  Predicted from the grid at 10823 chain
pairs; realised 10833, within ten.

Symmetric wins for a specific reason, not out of tidiness: `load0-load10-chain`
has already absorbed the entire `imma == 0` row, so what is left for the split
frame is the *diagonal* mass, and the grid shows that spread evenly rather than
concentrated on either axis.  Going to eleven bits (5+6) would reach ~10977 and
cost an opcode doubling for ~154 pairs.

### The `wide` column moves, and that is not overlap

`load0-load10-chain`'s own count drifts from 6869 to 6793 across the sweep — a
spread of 76 pairs, 1.1%.  The rules cannot overlap: one demands `imma == 0`
and the other demands it nonzero.  What moves is the greedy pairer.  Narrowing
the split frame leaves instructions unpaired, and the pairer then makes
different choices with them, a few of which land on `load0-load10-chain`.  Greedy
list scheduling is not monotone, so this is expected — and it is measured here
rather than assumed away.

## What is still on the floor

524 chases, 4.6% of the 11347 structurally encodable:

* **213 with `imma` above five bits** — the pointer sits deep in a frame.
* **311 with `imma` 1–5 but `immb` above five** — a deep reach from an offset
  pointer.  These are the ones eleven bits would buy.
* plus the **236** with negative or unaligned offsets, which no unsigned
  width-scaled field can hold at any width, and which sit outside the 11347.

The floor is not evenly spread.  The `immb` = 6–7 population it gives up is
concentrated in sqlite and godot — godot puts 41.6% of its chases at `immb` = 7
and 18% at 8, against cpp-rv64's 3.3% — so a corpus with wider structures would
pay more for the 5+5 split than this suite does.
