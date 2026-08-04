# Immediate widths: budget against return, frame by frame

Measured 2026-08-04 against the current tree.  Who is paying for range their
population does not use, and who is starved by a cap a cheap bit would fix.

## Method

**The census must be uncensored.**  A rule rejects immediates wider than its
frame allows, so measuring the pairs the scheduler produced reports "100% fit"
whenever the cap binds — the frame looks fed because everything it could not
encode was never counted.  Every number here comes from a run with
`imm_contracts.width_of` returning 13 bits for every op, so the population is
the one the corpus contains rather than the one the rules let through.

**Scaled fields are scored scaled.**  `pre-inc-pair` read 3% fitting five bits
in the previous version of this file, on a field the yaml declares as ten bits
times four.  `util/needed.py` now asks `imm_contracts.scale_of` for the
declared multiplier instead of guessing from the mnemonic; that row now reads
100%, which is the truth.

**The ceiling.**  With every cap lifted, musl-gcc-rv32 schedules 28207 pairs
against 25801 (+9.3%) and sqlite-rv64 46441 against 41492 (+11.9%).  That is
the whole of what immediate range could ever be worth, tails included — and
the tails are what cost exponentially.

**Branch displacements are estimated, not skipped.**  A displacement is an
unresolved label in the corpus, so it has no exact width.  Skipping it — which
this census used to do — made the whole document SILENT on the axis that binds
the two load+branch frames, and reported the worse of the two as fully fed
because its load offset happens to be ten bits.  `needed.py` now scores them
from `est_packet_distance` (instructions to the nearest same-named label,
scaled to packets).  Approximate, and the only figure available.

**What is judged.**  A 5- or 10-bit field is the natural size: it costs
nothing beyond the register columns the frame drew anyway, so slack inside one
is free and is not flagged.  Only two things carry a price and are judged
here: opcode-duplication purchases of range above the drawn field, and caps
that measurably starve.

## Where the extension money sits

343 of the reserved codepoints are duplication purchases (`encoding_assign`
reports 838 of 1024 reserved after hosting, 186 spare).

```
frame                     entries bought   what for
rsd-alu-pair                 +156          addi@7, li@7 (both slots)
li-branch-chain               +42          li@8
load-alu-chain                +34          addi@6, ld@6, lw@6
alu-alu-chain                 +30          addi@6
arith-jump-pair               +24          addi/addiw/andi/li/slli/srli@6
load-store-chain              +12          all eight load/store ops @6
bit-test-branch-chain         +12          andi/slli/srli@6
addi-store-off-chain          +12          addi@6, sb/sh/sw/sd@6
dual-setup-pair               +11          li@6, addi4spn@6
mem-base-pair                  +8          all eight load/store ops @6
alu-store-chain                +2          addi@6
```

## Fit at the declared width — uncensored, musl-gcc-rv32 + sqlite-rv64

Slots omitted below are at 100% already: every 10-bit field (mem-sp-pair,
prologue, epilogue, setup-jump, deref/base-load, pre-inc A, addi-store A,
li-czero, load-sp-branch, load-call) plus load-alu-chain A, inc-branch A and
the register-only B slots.

```
frame                    slot      n  decl   @decl      +1      +2
alu-store-chain             a    897     6   59.8%   64.1%   69.6%
alu-alu-chain               b   1444     6   65.2%   81.4%   85.4%
alu-alu-chain               a   2362     6   69.3%   81.9%   87.7%
addi-store-off-chain        b    380     6   72.1%   97.9%   99.5%
load-base-branch-pair       a   6160     5   74.6%   83.8%   98.5%
arith-jump-pair             a   6617     6   75.9%   82.0%   88.5%
bit-test-branch-chain       a   1172     6   76.9%   88.3%   91.5%
rsd-alu-pair                a   6322     7   78.5%   83.9%   88.2%
rsd-alu-pair                b   6237     7   80.2%   86.8%   90.7%
addi-store-off-chain        a    380     6   82.1%   82.6%   83.2%
post-inc-pair               b   1919     5   83.6%   93.1%   97.9%
pre-inc-pair                b    605     5   84.8%   99.3%   99.3%
li-branch-chain             a   2101     8   87.0%   95.0%   95.4%
load-store-chain            b   1392     6   87.6%   96.0%   99.9%
load-store-chain            a   1392     6   87.8%   96.1%   99.5%
post-inc-pair               a   1919     5   91.3%   95.7%   98.5%
dual-setup-pair             a  10897     6   91.6%   95.1%   98.2%
load-alu-chain              b   1431     6   93.0%   97.3%   98.3%
dual-setup-pair             b  10897     6   94.4%   96.4%   98.4%
index-mem-chain             b    568     5   96.5%   98.9%   99.5%
mem-base-pair               a  10299     6   97.1%   98.6%  100.0%
mem-base-pair               b  10299     6   97.1%   98.6%  100.0%
```

## The other axis: branch displacement

`load-base-branch-pair` and `load-sp-branch-pair` both draw a 5-bit `immb`,
and it is the tightest field in the encoding.  Measured over 978 and 172
paired sites (musl-gcc-rv32 + sqlite-rv64, uncensored):

```
displacement field   load-base    load-sp
   5 bits (today)       40.2%      21.5%
   6 bits               61.0%      42.4%
   7 bits               77.0%      67.4%
   8 bits               87.9%      76.2%
  10 bits               98.4%      96.5%
```

**`load-sp-branch-pair` is the worse of the two, not the better one.**  Its A
slot reads 100% in the table above only because dropping the implicit `sp`
base frees `imma` to span two columns for a ten-bit load offset.  On the axis
that actually binds, it fits half as much of its population as the base form
does.

Neither frame can be fixed inside its row: both are FULL at 20 bits, so
displacement bits must come out of the load offset, out of `rd`, or out of
opcode duplication.  Joint fit (displacement AND load offset both encodable):

```
                                   load-base    load-sp
imma 5b + immb 5b (today)             31.7%      17.4%
imma 4b + immb 6b                     42.4%      26.7%
imma 3b + immb 7b                     41.8%      32.0%
rd -> 3b (a0-a7), imma 5b + immb 7b   44.5%      43.6%
rd -> 3b (a0-a7), imma 5b + immb 9b   52.9%      57.6%
```

Even spending the destination register down to three bits AND four bits of
opcode duplication reaches only 53-58%, against the 94-99% the 10-bit frames
manage.  The row cannot hold ten displacement bits alongside a load, and no
rebalance of what it can hold changes that.

### Forced target alignment buys the same thing, in memory rather than budget

If branch targets were aligned past the packet, the low bits of the
displacement would be dead and the same field would reach further.  Measured,
it is an exact one-for-one trade with field width:

```
5-bit field, targets aligned to   load-base    load-sp     equivalent to
    4B (today, packet-aligned)      40.2%      21.5%        5 bits
    8B                              60.5%      42.4%        6 bits
   16B                              76.5%      66.3%        7 bits
   32B                              87.6%      76.2%        8 bits
```

The correspondence is within half a point at every step, because the
displacement distribution is smooth.  So alignment is a way to pay for reach
in padding instead of codepoints — but the padding is dead bytes in the
instruction stream, which costs the icache more than an inline cold block
would, and the icache was the reason to prefer a distant handler in the first
place.  Recorded as a real mechanism, not recommended.

## What a bit costs

THE IMMEDIATE RULE prices it: an op declaring `bits: N` over a field of `f`
occupies `2^(N-f)` entries, and a frame's codepoints are the product of its two
slots' weights.  So one more bit doubles every immediate-carrying op in that
slot, and the frame's whole block with it.

```
frame                  slot  bits   codepoints        fit gained
load-base-branch-pair    a    +2    14 ->   56  (+42)   74.6% -> 98.5%
addi-store-off-chain     b    +1    16 ->   32  (+16)   72.1% -> 97.9%
post-inc-pair            b    +1     4 ->    8  (+4)    83.6% -> 93.1%
pre-inc-pair             b    +1     8 ->   16  (+8)    84.8% -> 99.3%
load-store-chain         a    +1    16 ->   32  (+16)   87.8% -> 96.1%
li-branch-chain          a    +1    48 ->   96  (+48)   87.0% -> 95.0%
arith-jump-pair          a    +2    64 ->  256  (+192)  75.9% -> 88.5%
alu-alu-chain            a    +2   128 ->  512  (+384)  69.3% -> 87.7%
rsd-alu-pair             a    +2   256 -> 1024  (+768)  78.5% -> 88.2%
```

## Verdicts

**Buy: `post-inc-pair` B and `pre-inc-pair` B.**  Four and eight codepoints for
+9.5 and +14.5 points of fit on populations of 1919 and 605.  The cheapest
purchases on the board by an order of magnitude, and both frames sit inside
budgets that already round up to the next power of two.

**Buy: `addi-store-off-chain` B and `load-store-chain` A/B.**  Sixteen
codepoints each, and both curves are steep where it matters — 72.1% → 97.9%
and 87.8% → 96.1% for a single bit.  Together with the two above that is 44
codepoints of the 186 spare.

**Consider: `load-base-branch-pair` A.**  The largest starved population on the
board (6160) and the largest gain (74.6% → 98.5%), but two bits cost 42
codepoints because both slots carry the widening.  Worth it on the numbers;
hold it until the block-placement question on that frame is settled, since the
displacement estimate may move the population first.

**Refuse: the ALU family.**  `rsd-alu-pair` A at +2 costs 768 codepoints —
more than the entire spare budget — for 9.7 points.  `alu-alu-chain` at +384
for 18.4, `arith-jump-pair` at +192 for 12.6.  These are the frames with the
widest op sets, so doubling a slot doubles the largest blocks in the encoding.
No affordable width fixes them; their tails are long constants that belong in
a `li`, not in a paired field.

**Leave alone: `addi-store-off-chain` A.**  82.1% at six bits and 83.2% at
eight — the flattest curve measured.  Nothing to buy.

**Not starved, despite appearances:** every 10-bit field is at 100%, including
`mem-sp-pair` (7629 per slot, the largest immediate population in the
encoding) at a total cost of two codepoints.  `pre-inc-pair` A is at 100% too;
its former 3% reading was the unscaled-census bug, not a starved frame.
