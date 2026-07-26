# RISC-V template

 31         25 24     20 19     15 14  12 11      7 6        0
┌─────────────┬─────────┬─────────┬──────┬─────────┬──────────┐
│   funct7    │  rs2    │  rs1    │funct3│   rd    │  opcode  │   R-type
└─────────────┴─────────┴─────────┴──────┴─────────┴──────────┘
│       imm[11:0]       │  rs1    │funct3│   rd    │  opcode  │   I-type
│  imm[11:5]  │  rs2    │  rs1    │funct3│ imm[4:0]│  opcode  │   S-type
│imm[12|10:5] │  rs2    │  rs1    │funct3│imm[4:1|11]│ opcode │   B-type
│             imm[31:12]                 │   rd    │  opcode  │   U-type
│        imm[20|10:1|11|19:12]           │   rd    │  opcode  │   J-type

# Overview

## Constraints

 * The rd field must not be x0 or x1 when it describes a register
   (sometimes it is an immediate, and then the bit pattern is allowed).
   Encoding these registers here is reserved for different
   instructions, as is demonstrated in the prologue/epilogue pairs.

# Chain rules

 * One defined output register, plus x31 becomes undefined.
 * First instruction produces result for use by second instruction
 * Generally second operation produces result, but second op may have no output
   (eg., store, branch) meaning result comes from first, or there's no result at all.

## chain-alu-pair

    alu     tmp, rs1a, rs2a/imma
    alu     rdb, tmp, rs2b/immb

┌─┬─────────┬─┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│  rs2b   │g│  rs2a   │  rs1a   │funct3│   rdb   │0 0 op3 10│
│h│  rs2b   │g│imma[4:0]│  rs1a   │funct3│   rdb   │0 1 op3 10│
│h│immb[4:0]│g│  rs2a   │  rs1a   │funct3│   rdb   │1 0 op3 10│
│h│immb[4:0]│g│imma[4:0]│  rs1a   │funct3│   rdb   │1 1 op3 10│

 * g and h may be imma[5] and immb[5] or may extend funct3, depending on whether the opcode
   demands more immediate space (eg., shifts, and maybe addi).


## load-chain-alu-pair

    load    tmp, k*imma(rs1a)
    alu     rdb, tmp, rs2b/immb

    load    tmp, k*imma(sp)
    alu     rdb, tmp, rs2b/immb

┌─┬─────────┬─┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│  rs2b   │ imma[5:0] │  rs1a   │funct3│   rdb   │0 1 op3 10│
│h│immb[4:0]│ imma[5:0] │  rs1a   │funct3│   rdb   │1 1 op3 10│
│h│  rs2b   │g│ imma[4:0|9:5]     │funct3│   rdb   │0 1 op3 10│ (SP-relative)
│h│immb[4:0]│g│ imma[4:0|9:5]     │funct3│   rdb   │1 1 op3 10│ (SP-relative)

 * k is deduced from the memory operation data width
 * g and h may be imma[5] and immb[5] or may extend funct3, depending on whether the opcode
   demands more immediate space (eg., shifts, and maybe addi).

## store-chain-alu-pair

    alu     tmp, rs1a, rs2a/imma
    store   tmp, k*immb(rs1b)

    alu     tmp, rs1a, rs2a/imma
    store   tmp, k*immb(sp)

┌─┬─────────┬─┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│  rs1b   │g│  rs2a   │  rs1a   │funct3│immb[4:0]│0 0 op3 10│
│h│  rs1b   │g|imma[4:0]│  rs1a   │funct3│immb[4:0]│0 1 op3 10│
│h│immb[9:5]│g│  rs2a   │  rs1a   │funct3│immb[4:0]│1 0 op3 10│ (SP-relative)
│h│immb[9:5]│g|imma[4:0]│  rs1a   │funct3│immb[4:0]│1 1 op3 10│ (SP-relative)

 * k is deduced from the memory operation data width
 * g and h may be imma[5] and immb[10] or may extend funct3, depending on whether the opcode
   demands more immediate space (eg., shifts, and maybe addi).


## deref-chain-load-pair, base-chain-load-pair

    load    tmp, k*imma(rs1a)
    load    rdb, k*immb(tmp)

┌─┬─────────┬─┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│imma[9:5]│g│imma[4:0]│  rs1a   │funct3│   rdb   │ opcode 10│
│h│immb[9:5]│g│immb[4:0]│  rs1a   │funct3│   rdb   │ opcode 10│

 * k is deduced from the memory operation data width
 * TODO: decide how to balance imma and immb sizes.
 * TODO: maybe use g and h for the other immediate?  Or switch them between +2 bits on a or b
   immediate?


## chain-li-branch

    li      tmp, imma
    bXX     rs1b, tmp, immb

┌─┬─────────┬─┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│immb[9:5]│ imma[5:0] │  rs1b   │funct3│immb[11:2]│opcode 10│

 * h is imma[6]
 * TODO: should h be immb[12], or extend funct3?
 * TODO: could replace li with alu op and compare result with zero (mostly).


## chain-bit-test-branch

    andi    tmp, rs1a, imma
    beqz/bnez tmp, immb

    slli    tmp, rs1a, imma
    blt/bge tmp, zero, immb

┌─────────────┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│immb[9:5]│ imma[5:0] │  rs1a   │funct3│immb[11:2]│opcode 10│

 * h extends funct3?

# Chain rules, but first op is result

## load-sp-branch, load-base-branch

    load    rda, k*imma(sp)
    beqz/bnez rda, zero, immb

    load    rda, k*imma(rs1a)
    beqz/bnez rda, zero, immb

┌─────────────┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│   rda   │g│imma[4:0]│  rs1a   │funct3│immb[4:0]│0 1 op3 10│
│h│   rda   │g│ imma[4:0|9:5]     │funct3│immb[4:0]│1 1 op3 10│ (SP-relative)

 * k is deduced from the memory operation data width
 * g and h are immb[6:5]


## addi-branch-pair

    addi/addiw rsda, imma
    beqz/bnez rsda, zero, immb

┌─────────────┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│immb[9:5]│ imma[5:0] │  rsda   │funct3│immb[11:2]│opcode 10│


# pre/post increment addressing

Also chain rules with surviving first result, but also sometimes a second result.

## pre-inc-pair

    shXadd  rsda, rsda, rs2a
    load    rdb, k*immb(rsda)

    addi    rsda, rsda, imma
    load    rdb, k*immb(rsda)

    shXadd  rsda, rsda, rs2a
    store   rs2b, k*immb(rsda)

    addi    rsda, rsda, imma
    store   rs2b, k*immb(rsda)

┌─────────────┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h|  rs2b   │g|  rs2a   │  rsda   │funct3│immb[4:0]│0 0 op3 10│
│h|immb[4:0]│g|  rs2a   │  rsda   │funct3│  rdb    │1 0 op3 10│
│h|  rs2b   │g|imma[4:0]│  rsda   │funct3│immb[4:0]│0 1 op3 10│
│h|immb[4:0]│g|imma[4:0]│  rsda   │funct3│  rdb    │1 1 op3 10│

 * k and X are deduced from the memory operation data width
 * TODO: decide how to use g and h.
 * TODO: decide how to balance imma and immb sizes (proper coordination switches pre/post incr).

## dual-mem-addi-pair, dual-mem-shadd-pair
load/store	0 (offset implied)	addi	5 (u5×w stride)	shared base
load/store	0 (offset implied)	shNadd	0	shared base

    load    rda, 0(rs1a)
    shXadd  rs1a, rs1a, rs2a

    store   rs2a, 0(rs1a)
    shXadd  rs1a, rs1a, rs2a

    addi    rsda, rsda, k*imma
    load    rdb, -k*imma(rsda)

    addi    rsda, rsda, k*imma
    store   rs2b, -k*imma(rsda)

┌─────────────┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h|  rs2b   │g|  rs2a   │  rs1a   │funct3│immb[4:0]│0 0 op3 10│
│h|immb[4:0]│g|  rs2a   │  rs1a   │funct3│  rda    │1 0 op3 10│
│h|  rs2b   │g|imma[4:0]│  rsda   │funct3│immb[4:0]│0 1 op3 10│
│h|immb[4:0]│g|imma[4:0]│  rsda   │funct3│  rdb    │1 1 op3 10│

 * k and X are deduced from the memory operation data width
 * Somewhat contorted logic for coherent `rd` field usage.

# Other stuff

# mem-pair
load/store	8 (u8×w, sp) / 5 (u5×w)	same op	0 (implied +w)	shared base

    load    rda, k*imm(rbase)
    load    rdb, k*imm+k(rbase)

    store   rs2a, k*imm(rbase)
    store   rs2b, k*imm+k(rbase)

    load    rda, k*imm(sp)
    load    rdb, k*imm+k(sp)

    store   rs2a, k*imm(sp)
    store   rs2b, k*imm+k(sp)

┌─────────────┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│   rda   │ imm[5:0]  │  rbase  │funct3│   rdb   │ opcode 10│
│h│  rs2b   │i│  rs2a   │  rbase  │funct3│ imm[4:0]│ opcode 10│
│h│   rda   │g│  imm[4:0|9:5]     │funct3│   rdb   │ opcode 10│ (SP-relative)
│h│  rs2b   │g│  rs2a   │imm[9:5] │funct3│ imm[4:0]│ opcode 10│ (SP-relative)

 * i is imm[5]
 * k is deduced from the memory operation data width
 * both opcodes in a pair must be identical operations

# dual-arith2-pair

    alu     rda, rs1a, rs2a
    alu     rdb, rs1a, rs2a

┌─────────────┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│   rda   │g│  rs2a   │  rs1a   │funct3│   rdb   │ opcode 10│

 * g and h extend funct3

# dual-indep-pair

    mv      rda, rs1a
    mv/li   rdb, rs2b/immb

    li      rda, imma
    li      rdb, immb

┌─────────────┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│   rda   │g│  rs2b   │  rs1a   │funct3│   rdb   │ opcode 10│
│h│   rda   │g│immb[4:0]│  rs1a   │funct3│   rdb   │ opcode 10│
│h│   rda   │g│immb[4:0]│imma[4:0]│funct3│   rdb   │ opcode 10│


## rsd-alu-pair

    alu rsda, rsda, rs2a/imma
    alu rsdb, rsdb, rs2b/immb

┌─────────────┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│  rs2b   │g│  rs2a   │  rsda   │funct3│  rsdb   │0 0 op3 10│
│h│  rs2b   │g│imma[4:0]│  rsda   │funct3│  rsdb   │0 1 op3 10│
│h│immb[4:0]│g│  rs2a   │  rsda   │funct3│  rsdb   │1 0 op3 10│
│h│immb[4:0]│g│imma[4:0]│  rsda   │funct3│  rsdb   │1 1 op3 10│

 * g and h may be imma[5] and immb[5] or may extend funct3, depending on whether the opcode
   demands more immediate space (eg., shifts, and maybe addi).
 * TODO: these will probably have to be cut down to 4-bit register fields.


# Function head and tail special cases

Entry and exit into blocks makes alignment hard, so let's try to special-case as many common
patterns as possible to tamp down the cost.

## prologue-pair

    addi    sp, -16*imm
    store   rs1b, 16*imm-k(sp)

┌─────────────┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│   rs1b  │g│  imm[4:0|9:5]     │funct3│0 0 0 0 1│ opcode 10│

 * k is deduced from the memory operation data width


## epilogue-pair

    addi    sp, 16*imm
    jr      rs1b

┌─────────────┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│   rs1b  │g│  imm[4:0|9:5]     │funct3│0 0 0 0 1│ opcode 10│


# Other desperate measures

## arith-jump-pair

    alu     rsda, rsda, rs2a/imma
    jr/jalr rs1b

    mv     rsda, rs2a
    jr/jalr rs1b

    li     rsda, imma
    jr/jalr rs1b

┌─────────────┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│   rs1b  │g│  rs2a   │  rsda   │funct3│0 0 0 0 1│ opcode 10│
│h│   rs1b  │g│imma[4:0]│  rsda   │funct3│0 0 0 0 1│ opcode 10│

# arith-mem-pair

    alu     rsda, rsda, rs2a/imma
    load    rdb, k*immb(rs1b)

    alu     rsda, rsda, rs2a/imma
    store   rs2b, k*immb(rs1b)

┌─────────────┬─────────┬─────────┬──────┬─────────┬──────────┐
│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │
└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘
│h│   rs1b  │g│  rs2a   │  rsda   │funct3│   rdb   │ opcode 10│
│h│   rs1b  │g│imma[4:0]│  rsda   │funct3│0 0 0 0 1│ opcode 10│

 * k is deduced from the memory operation data width
 * g and h are immb[1:0]
