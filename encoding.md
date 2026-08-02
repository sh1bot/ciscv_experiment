<!-- Generated from encoding.yaml by util/encoding_render.py — do not edit by hand. -->

# RISC-V template

 31         25 24     20 19     15 14 12 11      7 6           0
┌─────────────┬─────────┬─────────┬─────┬─────────┬─────────────┐
│   funct7    │   rs2   │   rs1   │ fn3 │   rd    │   opcode    │   R-type
└─────────────┴─────────┴─────────┴─────┴─────────┴─────────────┘
│       imm[11:0]       │   rs1   │ fn3 │   rd    │   opcode    │   I-type
│  imm[11:5]  │   rs2   │   rs1   │ fn3 │imm[4:0] │   opcode    │   S-type
│imm[12|10:5] │   rs2   │   rs1   │ fn3 │[4:1|11] │   opcode    │   B-type
│              imm[31:12]               │   rd    │   opcode    │   U-type
│         imm[20|10:1|11|19:12]         │   rd    │   opcode    │   J-type

# Overview

## Constraints and general patterns

 * The rd field must not be x0 or x2 when it describes a register
   (sometimes it is an immediate, and then the bit pattern is allowed).
   Encoding these registers here is reserved for different
   instructions, as is demonstrated in the prologue/epilogue pairs.
 * the value of `k` in instruction templates is the size of the
   corresponding load or store data width.  The value of `X` in `shXadd`
   mnemonics is the corresponding bit shift.
 * AN IMMEDIATE FIELD IS FIVE BITS PER REGISTER FIELD IT CONSUMES:
   five bits from one register column, ten from two.  Beyond that it
   grows INCREMENTALLY, one bit at a time, by taking multiple entries in
   the opcode list: an op declaring `imm: {bits: N}` occupies
   2^(N - field) entries, so field+1 bits costs 2 entries, field+2 costs
   4, and so on (see the width-annotated entries in `opsets`).  There is
   no other widening mechanism; `g` and `h` are opcode bits like any
   other.

# Enumeration policy (intent only)

How codepoints are ASSIGNED — which bit patterns select which frames and
ops — is a matter of decoder convenience, and we hold preferences for it.
These are strictly enumeration policy: they change nothing about demand,
budgets, or immediate widths, and no tool may read them as capacity.

 * Specific selector bits, `g` and `h` among them, have preferential
   uses.  In particular, when a frame's op-select bits enumerate the
   duplicated immediate-form entries of a width-annotated op, we prefer
   to place those entries so the extension bits of the immediate land in
   `g` (A-slot ops) and `h` (B-slot ops).  That is a naming of which
   opcode bits carry the duplication — the codepoints are already paid —
   so a decoder can route them straight into the immediate mux.
 * Frame identifiers are ordered so their leading bits track the real
   RISC-V `opcode[6:2]` of the A slot (see `util/encoding_assign.py`),
   letting an A-slot decoder branch on bits it already examines.
 * Block sizes round up to powers of two and blocks are allocated in
   descending size, keeping every frame's identifier a contiguous prefix.

Further ordering and rounding policies belong here as they are decided.

# Reserved register encodings

 * **rd field — x0/x2** [active]: When rd names a register it may not be x0 or x2 (sp); those two bit patterns are sentinels selecting the prologue / epilogue / jump marker formats (drawn "0 0 0 1 0").

No general register block is reserved at present. Earlier drafts held out a contiguous block — the high registers x16..x31, or the low x0..x3 — to give dual-rsd and similar frames a fallback under encoding-space pressure, but the current layout fits without it. Such a block remains an option to reserve if a future frame ever needs one.

# Chain rules

 * One defined output register, plus x31 becomes undefined.
 * First instruction produces result for use by second instruction
 * Generally second operation produces result, but second op may have no output
   (eg., store, branch) meaning result comes from first, or there's no result at all.

## chain-alu-pair

    alu     tmp, rs1a, rs2a/imma
    alu     rdb, tmp, rs2b/immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2b   │g│  rs2a   │  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│  rs2b   │g│imma[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│immb[4:0]│g│  rs2a   │  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│immb[4:0]│g│imma[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│

## load-chain-alu-pair

    load    tmp, k*imma(rs1a)
    alu     rdb, tmp, rs2b/immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2b   │g│imma[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│immb[4:0]│g│imma[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│

## store-chain-alu-pair

    alu     tmp, rs1a, rs2a/imma
    store   tmp, k*immb(rs1b)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1b   │g│  rs2a   │  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│
│h│  rs1b   │g│imma[4:0]│  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│

## czero-select-pair

    czero.X tmp, rs1a, rs2a
    or      rdb, tmp, rs2b

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2b   │g│  rs2a   │  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│

## addi-store-pair

    addi    tmp, rs1a, imma
    store   tmp, 0(rbase)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1a   │g│   imma[4:0|9:5]   │ fn3 │  rbase  │ opcode5 │1 0│

* The data width comes from the op list (sb/sh/sw/sd), as in the other
  memory frames, rather than a width field -- 4 codepoints is cheaper
  than two bits of layout, and it matches existing convention. sh
  captures nothing on this corpus but only 3 sites carry it, which is
  too thin to conclude it never would.
* A covers li (rs1a = x0), mv (imma = 0) and addi4spn (rs1a = sp) as
  register/immediate choices, so they need no opcodes of their own.

## deref-chain-load-pair, base-chain-load-pair

    load    tmp, k*imma(rs1a)
    load    rdb, k*immb(tmp)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│imma[9:5]│g│imma[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│immb[9:5]│g│immb[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│

* TODO: decide how to balance imma and immb sizes.
* TODO: maybe use g and h for the other immediate?  Or switch them between +2 bits on a or b
  immediate?

## chain-li-branch

    li      tmp, imma
    bXX     rs1b, tmp, 4*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│immb[9:5]│g│imma[4:0]│  rs1b   │ fn3 │immb[4:0]│ opcode5 │1 0│

* `imma` is a 5-bit register column; `li` declares 7 bits, bought by
  opcode duplication.
* TODO: could replace li with alu op and compare result with zero (mostly?).

## chain-bit-test-branch

    andi    tmp, rs1a, imma
    beqz/bnez tmp, immb

    slli    tmp, rs1a, imma
    blt/bge tmp, zero, 4*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│immb[9:5]│g│imma[4:0]│  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│

# Chain rules, but first op is result

## load-sp-branch, load-base-branch

    load    rda, k*imma(sp)
    beqz/bnez rda, zero, 4*immb

    load    rda, k*imma(rs1a)
    beqz/bnez rda, zero, 4*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│   rda   │g│imma[4:0]│  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│
│h│   rda   │g│   imma[4:0|9:5]   │ fn3 │immb[4:0]│ opcode5 │1 0│ (SP-relative)

* `immb` is the branch displacement, a 5-bit field.  Displacements
  are unresolved labels in the corpus, so their fit is unmeasured.

## addi-branch-pair

    addi/addiw rsda, imma
    beqz/bnez rsda, zero, 4*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│immb[9:5]│g│imma[4:0]│  rsda   │ fn3 │immb[4:0]│ opcode5 │1 0│

# Scaled-index addressing

RISC-V has no register+register addressing mode, so `array[i]` costs two
instructions: form the address, then access it. The address is a pure
temporary — it exists only because the ISA has that hole.

## li-czero-pair

    li      tmp, imma
    czero.X rdb, tmp, rs2b

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2b   │g│   imma[4:0|9:5]   │ fn3 │   rdb   │ opcode5 │1 0│

## index-chain-mem-pair

    shXadd  tmp, rs1a, rs2a
    load    rdb, k*immb(tmp)

    shXadd  tmp, rs1a, rs2a
    store   rs2b, k*immb(tmp)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2a   │g│immb[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│  rs2a   │g│  rs2b   │  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│

# pre/post increment addressing

Also chain rules with surviving first result, but also sometimes a second result.

## pre-inc-pair

    shXadd  rsda, rsda, rs2a
    load    rdb, k*immb(rsda)

    addi    rsda, rsda, k*imma
    load    rdb, k*immb(rsda)

    shXadd  rsda, rsda, rs2a
    store   rs2b, k*immb(rsda)

    addi    rsda, rsda, k*imma
    store   rs2b, k*immb(rsda)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2b   │g│  rs2a   │  rsda   │ fn3 │immb[4:0]│ opcode5 │1 0│
│h│immb[4:0]│g│  rs2a   │  rsda   │ fn3 │   rdb   │ opcode5 │1 0│
│h│  rs2b   │g│imma[4:0]│  rsda   │ fn3 │immb[4:0]│ opcode5 │1 0│
│h│immb[4:0]│g│imma[4:0]│  rsda   │ fn3 │   rdb   │ opcode5 │1 0│

* TODO: Try zero memory offset and all the load/store permutations instead
* TODO: decide how to balance imma and immb sizes (proper coordination switches pre/post incr).

## post-inc-pair

    load    rda, k*imma(rsda)
    addi    rsda, rsda, k*immb

    store   rs2a, k*imma(rsda)
    addi    rsda, rsda, k*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│immb[4:0]│g│  rs2a   │  rsda   │ fn3 │imma[4:0]│ opcode5 │1 0│
│h│immb[4:0]│g│imma[4:0]│  rsda   │ fn3 │   rda   │ opcode5 │1 0│

* Timing oddity here because reg in `rd` field is written in first cycle not second.
* No shXadd clusters: a post-increment by a register-held stride is a
  real idiom, but neither clang nor GCC emits it adjacent to the
  access (zero scheduled pairs on every corpus).
* TODO: Try zero memory offset and all the load/store permutations instead
* TODO: decide how to balance imma and immb sizes (proper coordination switches pre/post incr).

# Other stuff

# mem-pair-sp

    load    rda, k*imm(sp)
    load    rdb, k*imm+k(sp)

    store   rs2a, k*imm(sp)
    store   rs2b, k*imm+k(sp)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│   rda   │g│   imm[4:0|9:5]    │ fn3 │   rdb   │ opcode5 │1 0│
│h│  rs2b   │g│  rs2a   │imm[9:5] │ fn3 │imm[4:0] │ opcode5 │1 0│

* both opcodes in a pair must be identical operations
* offsets differ by one data width, as in mem-pair

# mem-pair

    load    rda, k*imm(rbase)
    load    rdb, k*imm+k(rbase)

    store   rs2a, k*imm(rbase)
    store   rs2b, k*imm+k(rbase)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│   rda   │g│imm[4:0] │  rbase  │ fn3 │   rdb   │ opcode5 │1 0│
│h│  rs2b   │g│  rs2a   │  rbase  │ fn3 │imm[4:0] │ opcode5 │1 0│

* both opcodes in a pair must be identical operations
* No `lb`, `lh` or `lwu`: they account for 12 of 37816 scheduled
  slots across musl-rv32 and sqlite-rv64.

# dual-arith2-pair

    alu     rda, rs1a, rs2a
    alu     rdb, rs1a, rs2a

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│   rda   │g│  rs2a   │  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│

* KEPT DELIBERATELY DESPITE A NEAR-ZERO SCORE. Do not cut this frame on
  pairing-rate evidence; it is not here to earn pairs.
* Every cluster is two halves of ONE computation over the same operands:
  the low and high words of a multiply, the quotient and remainder of a
  divide, the sum and difference, the min and the max. Encoding them as
  a declared pair tells the implementation both results are wanted, so
  it can FUSE -- one pass of the multiplier or divider producing both
  halves -- instead of issuing the operation twice and discarding half
  of each result. That is a hardware invitation, and it is worth a
  codepoint block whether or not today's compilers accept it.
* They mostly do not, yet. Measured over four corpora: 70 scheduled
  pairs. The ceiling is no higher -- adjacent tuple matches with
  positionally shared operands number 31 on musl-rv32, 25 on cpp-rv32,
  2 on sqlite-gcc-rv64, 0 on sqlite-rv64 -- so nothing is suppressing
  it. Notably the frame is NOT register-window constrained: rules.py
  carries no x0-x15 set, and this row draws four full 5-bit fields.
* ORDER IS NOT ARBITRARY, and it is not a dependency either. The two
  ops read the same two sources and neither consumes the other's
  result, so they commute -- but the RISC-V M extension names one
  sequence as THE fusion idiom, and a microarchitecture told to detect
  fusable pairs is looking for that one:

      MULH[[S]U] rdh, rs1, rs2 ; MUL rdl, rs1, rs2
      DIV[U]     rdq, rs1, rs2 ; REM[U] rdr, rs1, rs2

  high half first, quotient first, "source register specifiers must be
  in the same order and rdh cannot be the same as rs1 or rs2" -- that
  last clause because the fused unit still needs both sources intact
  when it delivers the second result. `_reject_dependence` already
  enforces exactly that (`a.rd not in b.uses_regs`), so rules.py gets
  it for free.
* The clusters are listed in the spec's sequence and the compiler
  already emits it (hi-first outnumbers lo-first 22:9 on musl-rv32,
  div-first is universal, and every measured occurrence satisfies the
  rdh-not-a-source rule). rules.py canonicalises, so both directions
  still pair; the canonical direction also gets the lighter
  dependence test, so keep it spec-ordered.
* The gap is a toolchain one -- a compiler that knew this pairing were
  available would emit the two halves adjacently and in order. Treat
  the low score as a measurement of clang and GCC, not of the frame.

# dual-indep-pair

    mv      rda, rs1a
    mv/li   rdb, rs2b/immb

    li      rda, imma
    li      rdb, immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│   rda   │g│  rs2b   │  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│   rda   │g│immb[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│   rda   │g│immb[4:0]│imma[4:0]│ fn3 │   rdb   │ opcode5 │1 0│

## rsd-alu-pair

    alu rsda, rsda, rs2a/imma
    alu rsdb, rsdb, rs2b/immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2b   │g│  rs2a   │  rsda   │ fn3 │  rsdb   │ opcode5 │1 0│
│h│  rs2b   │g│imma[4:0]│  rsda   │ fn3 │  rsdb   │ opcode5 │1 0│
│h│immb[4:0]│g│  rs2a   │  rsda   │ fn3 │  rsdb   │ opcode5 │1 0│
│h│immb[4:0]│g│imma[4:0]│  rsda   │ fn3 │  rsdb   │ opcode5 │1 0│

* The four register operands occupy the four 5-bit columns -- 20 bits,
  the whole operand budget -- so registers here are a FULL 5-bit field,
  x0..x31. An earlier note anticipated cutting them to 4 bits; that is
  not needed, and scheduler/rules.py enforcing x0..x15 was costing 377
  pairs across the corpus.

# Function head and tail special cases

Entry and exit into blocks makes alignment hard, so let's try to special-case as many common
patterns as possible to tamp down the cost.

## prologue-pair

    addi    sp, -16*imm
    store   rs1b, 16*imm-k(sp)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1b   │g│   imm[4:0|9:5]    │ fn3 │0 0 0 1 0│ opcode5 │1 0│

## epilogue-pair

    addi    sp, 16*imm
    jr      rs1b

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1b   │g│   imm[4:0|9:5]    │ fn3 │0 0 0 1 0│ opcode5 │1 0│

# Other desperate measures

## arith-jump-pair

    alu     rsda, rsda, rs2a/imma
    jr/jalr rs1b

    mv     rsda, rs2a
    jr/jalr rs1b

    li     rsda, imma
    jr/jalr rs1b

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1b   │g│  rs2a   │  rsda   │ fn3 │0 0 0 1 0│ opcode5 │1 0│
│h│  rs1b   │g│imma[4:0]│  rsda   │ fn3 │0 0 0 1 0│ opcode5 │1 0│

## mvload-jump-pair

    mv      rda, rs1a
    jr      rs1b

    load    rda, k*imma(rs1a)
    jr      rs1b

    li      rda, imma
    jr      rs1b

    mv      rda, rs1a
    j       4*immb

    load    rda, 0(rs1a)
    j       4*immb

    li      rda, imma
    j       4*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1b   │g│imma[4:0]│  rs1a   │ fn3 │   rda   │ opcode5 │1 0│
│h│  rs1b   │g│   imma[4:0|9:5]   │ fn3 │   rda   │ opcode5 │1 0│
│h│  rs1a   │g│   immb[4:0|9:5]   │ fn3 │   rda   │ opcode5 │1 0│
│h│imma[4:0]│g│   immb[4:0|9:5]   │ fn3 │   rda   │ opcode5 │1 0│

* `j` covers `jal x0`; a jal with a real destination is a call and is
  excluded from every jump frame.
* Direct `j` (78-92% of this frame's packets) takes rows 3-4: `rs1b`
  is dropped -- a direct jump has no register operand -- and `immb`
  gets the rs2+rs1 span, a 10-bit displacement in PACKET units.
  Packets are 4-byte aligned, so the low bit RVC must carry is dead
  and a displacement costs 0.54x its RVC bits. 10 bits covers 84.6%
  of direct `j` on sqlite and 97-98% on musl.
* rules.py cannot range-check the displacement: corpus jump operands
  are unresolved labels, so a pairwise rule has nothing to test. The
  scheduled count includes the over-range tail (~15% on sqlite).
  See results/corpus/README.md.
* On the direct-`j` rows a load has no offset field (offsets are zero
  in 98.2% of chained cases anyway) and `li` narrows to 5 bits.

# arith-mem-pair

    alu     rsda, rsda, rs2a/imma
    load    rdb, k*immb(rs1b)

    alu     rsda, rsda, rs2a/imma
    store   rs2b, k*immb(rs1b)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1b   │g│  rs2a   │  rsda   │ fn3 │   rdb   │ opcode5 │1 0│
│h│  rs1b   │g│imma[4:0]│  rsda   │ fn3 │  rs2b   │ opcode5 │1 0│

* B's memory offset MUST BE ZERO -- the rows draw no `immb` field.
  A 2-bit offset declared on the eleven B ops would cost 4x each
  (demand 55 -> 220, a 256-block); not worth it.
