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
 * When `imma` or `immb` are required to be 6-bit immediates, the extra
   bits are stored in bits `g` and `h` respectively.  Otherwise these
   bits are used to extend the range of the `funct3` field.

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

    load    tmp, k*imma(sp)
    alu     rdb, tmp, rs2b/immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2b   │g│imma[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│immb[4:0]│g│imma[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│  rs2b   │g│   imma[4:0|9:5]   │ fn3 │   rdb   │ opcode5 │1 0│ (SP-relative)
│h│immb[4:0]│g│   imma[4:0|9:5]   │ fn3 │   rdb   │ opcode5 │1 0│ (SP-relative)

## store-chain-alu-pair

    alu     tmp, rs1a, rs2a/imma
    store   tmp, k*immb(rs1b)

    alu     tmp, rs1a, rs2a/imma
    store   tmp, k*immb(sp)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1b   │g│  rs2a   │  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│
│h│  rs1b   │g│imma[4:0]│  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│
│h│immb[9:5]│g│  rs2a   │  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│ (SP-relative)
│h│immb[9:5]│g│imma[4:0]│  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│ (SP-relative)

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

    addi    tmp, rs1a, imma
    store   tmp, k*immb(sp)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1a   │g│   imma[4:0|9:5]   │ fn3 │  rbase  │ opcode5 │1 0│
│h│  rs1a   │g│   imma[4:0|9:5]   │ fn3 │immb[4:0]│ opcode5 │1 0│ (SP-relative)

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
│h│immb[9:5]│ imma[5:0] │  rs1b   │ fn3 │immb[4:0]│ opcode5 │1 0│

* For this frame `h` extends `imma` by one bit.
* TODO: could replace li with alu op and compare result with zero (mostly?).

## chain-bit-test-branch

    andi    tmp, rs1a, imma
    beqz/bnez tmp, immb

    slli    tmp, rs1a, imma
    blt/bge tmp, zero, 4*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│immb[9:5]│ imma[5:0] │  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│

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

* For this frame `g` and `h` extend `immb` by two bits.

## addi-branch-pair

    addi/addiw rsda, imma
    beqz/bnez rsda, zero, 4*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│immb[9:5]│ imma[5:0] │  rsda   │ fn3 │immb[4:0]│ opcode5 │1 0│

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
    shXadd  rsda, rsda, rs2b

    store   rs2a, k*imma(rsda)
    shXadd  rsda, rsda, rs2b

    load    rda, k*imma(rsda)
    addi    rsda, rsda, k*immb

    store   rs2a, k*imma(rsda)
    addi    rsda, rsda, k*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2b   │g│  rs2a   │  rsda   │ fn3 │imma[4:0]│ opcode5 │1 0│
│h│  rs2b   │g│imma[4:0]│  rsda   │ fn3 │   rda   │ opcode5 │1 0│
│h│immb[4:0]│g│  rs2a   │  rsda   │ fn3 │imma[4:0]│ opcode5 │1 0│
│h│immb[4:0]│g│imma[4:0]│  rsda   │ fn3 │   rda   │ opcode5 │1 0│

* Timing oddity here because reg in `rd` field is written in first cycle not second.
* TODO: Try zero memory offset and all the load/store permutations instead
* TODO: decide how to balance imma and immb sizes (proper coordination switches pre/post incr).

# Other stuff

# mem-pair

    load    rda, k*imm(rbase)
    load    rdb, k*imm+k(rbase)

    store   rs2a, k*imm(rbase)
    store   rs2b, k*imm+k(rbase)

    load    rda, k*imm(sp)
    load    rdb, k*imm+k(sp)

    store   rs2a, k*imm(sp)
    store   rs2b, k*imm+k(sp)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│   rda   │ imm[5:0]  │  rbase  │ fn3 │   rdb   │ opcode5 │1 0│
│h│  rs2b   │i│  rs2a   │  rbase  │ fn3 │imm[4:0] │ opcode5 │1 0│
│h│   rda   │g│   imm[4:0|9:5]    │ fn3 │   rdb   │ opcode5 │1 0│ (SP-relative)
│h│  rs2b   │g│  rs2a   │imm[9:5] │ fn3 │imm[4:0] │ opcode5 │1 0│ (SP-relative)

* For row two, `i` extends `imm` by one bit (consistent with `imma` extension but in a B-type frame).
* both opcodes in a pair must be identical operations

# dual-arith2-pair

    alu     rda, rs1a, rs2a
    alu     rdb, rs1a, rs2a

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│   rda   │g│  rs2a   │  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│

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

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1b   │g│imma[4:0]│  rs1a   │ fn3 │   rda   │ opcode5 │1 0│
│h│  rs1b   │g│   imma[4:0|9:5]   │ fn3 │   rda   │ opcode5 │1 0│

* `j` covers `jal x0`; a jal with a real destination is a call and is
  excluded from every jump frame. Jump displacements are unbounded and
  not encoded in these fields.

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

* For this frame `g` and `h` provide a 2-bit `immb`.
