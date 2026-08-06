<!-- Generated from encoding.yaml by util/encoding_render.py — do not edit by hand. -->

# Reserved register encodings

 * **rd field — x0/x2** [active]: When rd names a register it may not be x0 or x2 (sp); those two bit patterns are sentinels selecting the prologue / epilogue / jump marker formats (drawn "0 0 0 1 0"). DECIDED (A1.11): this is enforced, not merely declared -- rules.py rejects rd in {x0, x2} wherever a row draws a register there. The payoff is hosting: a sentinel-selected frame can ride inside another rd-bearing frame's opcode word, in the slice that frame's rd cannot reach, at zero opcode cost. A frame may host only if its rd column holds a REGISTER in every row -- a frame whose rd carries immb[4:0] has no unreachable slice to lend.

No general register block is reserved at present. Earlier drafts held out a contiguous block — the high registers x16..x31, or the low x0..x3 — to give dual-rsd and similar frames a fallback under encoding-space pressure, but the current layout fits without it. Such a block remains an option to reserve if a future frame ever needs one.

## alu-alu-chain

*Two ALU operations, the second consuming the first's result.*

    alu     tmp, rs1a, rs2a/imma
    alu     rdb, tmp, rs2b/immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2b   │g│  rs2a   │  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│  rs2b   │g│imma[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│immb[4:0]│g│  rs2a   │  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│immb[4:0]│g│imma[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│

## load-alu-chain

*Load a value and immediately compute with it.*

    load    tmp, k*imma(rs1a)
    alu     rdb, tmp, rs2b/immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2b   │g│imma[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│immb[4:0]│g│imma[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│

## alu-store-chain

*Compute a value and store it.*

    alu     tmp, rs1a, rs2a/imma
    store   tmp, k*immb(rs1b)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1b   │g│  rs2a   │  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│
│h│  rs1b   │g│imma[4:0]│  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│

## czero-or-chain

*Finish a conditional select: merge the surviving arm into the result.*

    czero.X tmp, rs1a, rs2a
    or      rdb, tmp, rs2b

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2b   │g│  rs2a   │  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│

## addi-store-chain

*Form a value -- constant, copy or sp-relative address -- and store it.*

    addi    tmp, rs1a, imma
    store   tmp, 0(rbase)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1a   │g│imma[4:0]│imma[9:5]│ fn3 │  rbase  │ opcode5 │1 0│

 * The data width comes from the op list (sb/sh/sw/sd), as in the other
   memory frames, rather than a width field -- 4 codepoints is cheaper
   than two bits of layout, and it matches existing convention. sh
   captures nothing on this corpus but only 3 sites carry it, which is
   too thin to conclude it never would.
 * A covers li (rs1a = x0), mv (imma = 0) and addi4spn (rs1a = sp) as
   register/immediate choices, so they need no opcodes of their own.

## load0-load10-chain

*Pointer chase: bare first load, the second carries a wide offset.*

    lx      tmp, 0(rs1a)
    load    rdb, k*immb(tmp)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│immb[9:5]│g│immb[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│

 * The A slot spends ONE opcode, not seven. `must_chain_base` makes A's
   loaded value B's base address, and a byte or halfword is not an
   address -- so A is the natural word by construction. Measured over
   every chain the pairer can form, all 11583 of them across the suite, A
   is `lw` on RV32 and `ld` on RV64 100.0% of the time, with no exception
   on or off the axes. That is what `lx` names, and it takes the block
   from 7x7=49 codepoints to 1x7=7.
 * `immb` gets the full ten bits -- five from `funct5`, five from `rs2`,
   fields the pair leaves free because `tmp` is implicit and A's offset
   is pinned at zero. This single form is 59.9% of all chases.
 * Split from a frame that drew BOTH this row and its sibling's over one
   49-codepoint op-select, with nothing selecting between them: a decoder
   holding the word could not tell whether the field was the first load's
   offset or the second's. That frame's standing TODO -- balance imma
   against immb -- is answered in results/corpus/CHAINS.md.

## load5-load5-chain

*Pointer chase with BOTH loads offset: a pointer in a slot, then indexed.*

    lx      tmp, k*imma(rs1a)
    load    rdb, k*immb(tmp)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│imma[4:0]│g│immb[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│

 * The offset-bearing sibling of load0-load10-chain, on the pattern of
   addi-store-off-chain. It replaces an earlier `deref-load-chain`, whose
   population (offset on the FIRST load, second at zero) is the immb=0
   column here -- 2397 of its 2425 chases, the 28 lost being those
   needing more than five bits of `imma`.
 * The ten free bits are split evenly because the corpus says so, not for
   symmetry. load0-load10-chain has already absorbed the whole imma=0
   row, so what is left to catch is diagonal mass, and it is spread:
   measured corpus totals are 5+5 505255, 6+4 505073, 4+6 505032, 7+3
   504522, 3+7 504635. Eleven bits (5+6) would reach ~10977 chases and
   cost an opcode doubling for ~154 pairs.
 * `rs1a` = sp is what makes this frame necessary rather than a rounding
   error. An sp-based chase -- a pointer read out of a stack slot, then
   indexed -- is 58% both-offsets-nonzero and 1% B-only, against 20% and
   60% for a non-sp chase. The slot displacement is an A offset by
   construction.
 * The two frames are disjoint by construction: this one demands imma be
   nonzero and its sibling demands it be zero, so no chase is encodable
   both ways and neither shadows the other.

## li-branch-chain

*Compare a register against a constant and branch.*

    li      tmp, imma
    bXX     rs1b, tmp, 4*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│immb[9:5]│g│imma[4:0]│  rs1b   │ fn3 │immb[4:0]│ opcode5 │1 0│

 * `imma` is a 5-bit register column; `li` declares 8 bits, bought by
   three opcode doublings (census li fit 66.9% -> 85.3% of 2293, ~13
   pairs/codepoint for the extra 32).
 * The row spells the constant in rs2. A site with the constant on the
   LEFT of an asymmetric compare (`blt tmp, rs`) is still encodable via
   the dead-tmp rewrite `bXX K, rs` -> `bYY rs, K+1` (blt<->bge,
   bltu<->bgeu) -- tmp carries only the comparison constant and dies at
   B, so changing its value is licensed. rules.py accepts those sites and
   rejects the two edge cases the rewrite cannot reach: K at the top of
   the field (K+1 overflows) and K = -1 under an unsigned compare (the
   predicate flips).
 * TODO: could replace li with alu op and compare result with zero
   (mostly?).

## bit-test-branch-chain

*Test a bit or bit-field and branch on the result.*

    andi    tmp, rs1a, imma
    beqz/bnez tmp, 4*immb

    slli/srli tmp, rs1a, imma
    beqz/bnez tmp, 4*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│immb[9:5]│g│imma[4:0]│  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│

 * The shift forms are the E1/E2 rewrite targets (low-mask and high-mask
   zero tests); a single-bit sign test via `slli` + `blt tmp, zero` is
   equivalence E5, a candidate this frame does NOT yet encode -- its b
   list has no blt/bge -- and rules.py matches accordingly.

## load-base-branch-pair

*Load a value and branch on whether it is zero; the value survives.*

    load    rda, k*imma(rs1a)
    beqz/bnez rda, zero, 4*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│   rda   │g│imma[4:0]│  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│

 * `immb` is the branch displacement, a 5-bit field. Displacements are
   unresolved labels in the corpus, so their fit is unmeasured.

## load-sp-branch-pair

*Load a stack slot and branch on whether it is zero; the value survives.*

    load    rda, k*imma(sp)
    beqz/bnez rda, zero, 4*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│   rda   │g│imma[4:0]│imma[9:5]│ fn3 │immb[4:0]│ opcode5 │1 0│

 * `immb` as in load-base-branch-pair: unresolved, fit unmeasured.

## inc-branch-pair

*Step a loop counter by one and branch on the comparison.*

    inc/dec  rsda
    bXX     rsda, rs2b, 4*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│immb[9:5]│g│  rs2b   │  rsda   │ fn3 │immb[4:0]│ opcode5 │1 0│

 * The step is +/-1, implied by the opcode (`inc`/`dec` = `addi rsda,
   rsda, +/-1`): 88% of adjacent counter-branch sites compare against a
   REGISTER, so the immediate column goes to `rs2b` instead of a step
   field; `rs2b = x0` gives every vs-zero form for free. Full XLEN width
   only -- there are no `w` forms.
 * `_r` marks the operand-reversed spelling (the counter in rs2). The two
   clusters are the best sixteen JOINT direction x mode cells of the
   adjacent-site census, not a mode product: down-loops are bltu/bgeu-
   heavy (pointer-vs-limit, both operand orders), up-loops beq/bne with
   bge/bgeu sum-first. Joint enumeration covers 98.7% of adjacent sites
   against ~79% for the best 4-mode x 2-direction product at the same
   sixteen entries.
 * The scheduler also matches `addiw rsd, rsd, +/-1` and bills it here.
   That is optimistic for unsigned int counters on rv64 (defined wrap is
   not width-equivalent) in the same spirit as RVC- eligibility; signed
   counters are provably width-equivalent (overflow is UB), so a packet-
   targeted compiler emits `addi`.
 * `immb` is the branch displacement in packets. Displacements are
   unresolved labels in the corpus, so pairwise fit is unmeasured; the
   label-distance study puts 10-bit fit near 100%.

## li-czero-chain

*Materialise a constant and conditionally zero it -- one arm of a select.*

    li      tmp, imma
    czero.X rdb, tmp, rs2b

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2b   │g│imma[4:0]│imma[9:5]│ fn3 │   rdb   │ opcode5 │1 0│

## index-mem-chain

*Scaled-index addressing: compute `base + i*width` and access it.*

    shXadd  tmp, rs1a, rs2a
    load    rdb, k*immb(tmp)

    shXadd  tmp, rs1a, rs2a
    store   rs2b, k*immb(tmp)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2a   │g│immb[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│  rs2a   │g│  rs2b   │  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│

## pre-inc-pair

*Advance a pointer, then access through it (pre-increment).*

    shXadd  rsda, rs1a, rsda
    load    rdb, k*immb(rsda)

    addi    rsda, rsda, k*imma
    load    rdb, 0(rsda)

    shXadd  rsda, rs1a, rsda
    store   rs2b, k*immb(rsda)

    addi    rsda, rsda, k*imma
    store   rs2b, 0(rsda)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs2b   │g│  rsda   │  rs1a   │ fn3 │immb[4:0]│ opcode5 │1 0│
│h│immb[4:0]│g│  rsda   │  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│  rs2b   │g│imma[4:0]│imma[9:5]│ fn3 │  rsda   │ opcode5 │1 0│
│h│   rdb   │g│imma[4:0]│imma[9:5]│ fn3 │  rsda   │ opcode5 │1 0│

 * The addi rows access AT the bumped pointer: at genuine (non- prologue)
   surviving-sum sites the memory offset is zero 68-78% of the time, so
   the rows spend no immb and give the bump the freed column instead -- a
   10-bit width-scaled imma, which the op declares at no extra
   codepoints. Even so the bump population is structurally wide (record-
   sized walks: 10-bit scaled fit is 39-59%); this is the affordable
   ceiling, not full coverage.
 * The shXadd rows keep the 5-bit scaled immb: their stride is the
   register, so the offset field still earns its column.
 * The shXadd form is the scaled POINTER WALK, `shXadd rsda, rs1a, rsda`
   (Zba: rd = rs2 + (rs1 << X)) -- the pointer advanced by a scaled
   stride, which is what "advance a pointer" means and the form the
   corpus emits (its in-place-scaling sibling `shXadd a, a, x` measured
   69 scheduled pairs on sqlite-rv64 against this form's 210; one row
   decodes one operand binding, so rules.py admits only this one). The
   row is drawn at the STANDARD Zba read ports: the shifted stride `rs1a`
   in the rs1 column, the pointer `rsda` in rs2 -- Zba's rs2 position. B
   needs no port for its base at all: a pre-increment accesses through
   the UPDATED pointer, which is A's result, forwarded inside the packet
   -- so nothing competes with A for the columns and the operand-position
   discipline holds without a trade.

## post-inc-pair

*Access through a pointer, then advance it (post-increment).*

    load    rda, k*imma(rsda)
    addi    rsda, rsda, k*immb

    store   rs2a, k*imma(rsda)
    addi    rsda, rsda, k*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│immb[4:0]│g│  rs2a   │  rsda   │ fn3 │imma[4:0]│ opcode5 │1 0│
│h│immb[4:0]│g│imma[4:0]│  rsda   │ fn3 │   rda   │ opcode5 │1 0│

 * Timing oddity here because reg in `rd` field is written in first cycle
   not second.
 * No shXadd clusters: a post-increment by a register-held stride is a
   real idiom, but neither clang nor GCC emits it adjacent to the access
   (zero scheduled pairs on every corpus).
 * Both fields earn their columns, unlike pre-inc: the access offset imma
   is the position inside an unrolled window (small, 67-74% within +/-16)
   and the stride immb is the window size (wide). They decouple under
   unrolling -- stride = offset + width holds at only 3-19% -- so neither
   a zero offset nor a delta encoding works here.

# mem-sp-pair

*Two adjacent stack accesses one word apart -- a spill or reload pair.*

    load    rda, k*imm(sp)
    load    rdb, k*imm+k(sp)

    store   rs2a, k*imm(sp)
    store   rs2b, k*imm+k(sp)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│   rda   │g│imm[4:0] │imm[9:5] │ fn3 │   rdb   │ opcode5 │1 0│
│h│  rs2b   │g│  rs2a   │imm[9:5] │ fn3 │imm[4:0] │ opcode5 │1 0│

 * both opcodes in a pair must be identical operations
 * offsets differ by one data width, as in mem-base-pair

# mem-base-pair

*Two adjacent accesses through one base register, one data width apart.*

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
 * No `lb`, `lh` or `lwu`: they account for 12 of 37816 scheduled slots
   across musl-rv32 and sqlite-rv64.

## load-store-chain

*Copy a value from one memory location to another.*

    load    tmp, k*imma(rs1a)
    store   tmp, k*immb(rs1b)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│immb[4:0]│g│imma[4:0]│  rs1a   │ fn3 │  rs1b   │ opcode5 │1 0│

 * Both offsets are width-scaled and unsigned; the loaded value is the
   chain temporary and is not encoded.

## addi-store-off-chain

*Compute a value from one base and store it at an offset from another.*

    addi    tmp, rs1a, imma
    store   tmp, k*immb(rbase)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1a   │g│immb[4:0]│  rbase  │ fn3 │imma[4:0]│ opcode5 │1 0│

 * `immb` is width-scaled and unsigned; `imma` is signed -- and that
   asymmetry is the frame's whole shape. A memory offset carries an
   access width, so five bits of `immb` reach 4x or 8x further; an `addi`
   addend is pointer arithmetic and carries none, so its bits are bytes.
   `load5-load5-chain` gets a symmetric split because BOTH its immediates
   are scaled offsets; this frame cannot, and scaling A by the store's k
   would not help -- the addends are 93% ODD, only 28.8% are aligned to
   their own store's width, and for `sw` (65% of the population) it is 5
   of 295. The stored value is the chain temporary and is not encoded.
 * THE WIDTH BELONGS TO B, measured 2026-08-04. The row draws five bits
   per column, so a sixth costs a doubling. Over 455 scheduled pairs on
   sqlite-rv32/rv64 and cpp-rv32/rv64: A's addend fits FIVE bits 97% of
   the time and two bits 95% of the time, while B's scaled offset fits
   five only 33% of the time and six 100%. So A was paying 8 codepoints
   for the 2% that need a sixth bit. Dropped to 5+6: 16 codepoints to 8,
   at a cost of 5 pairs on sqlite-rv32 and 4 on sqlite-rv64. Narrowing B
   too (5+5, 4cp) costs a further 135 and 130 -- that bit is real.
 * This is a SQLITE-shaped frame: 212 hits on sqlite-rv32 against 12 on
   cpp-rv32. A cpp-only reading makes it look like the worst frame in the
   encoding; it is not.
 * rules.py EXCLUDES the li form (rs1a = x0) here even though the row
   draws rs1a and could encode it: li + store belongs to the frames that
   price it (addi-store-chain at offset zero, alu-store-chain up to a
   5-bit offset). The residue -- li + store at an offset only this
   frame's sixth bit reaches -- stays solo.

## load-call-chain

*Load a function pointer and call through it (virtual dispatch).*

    load    tmp, k*imma(rs1a)
    jalr_link_ra tmp

    load    tmp, k*imma(rs1a)
    jalr_link_t1 tmp

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│imma[9:5]│g│imma[4:0]│  rs1a   │ fn3 │ unused  │ opcode5 │1 0│

 * `rd: unused` leaves the selecting sentinel to the enumerator, which
   allocates it from the reserved x0/x2 pool per (host, sentinel) -- both
   patterns are reserved either way, so whichever has room carries this
   frame.
 * The link register is NOT drawn -- both templates share one row and are
   told apart by the op-select, which is why the budget is 4 for two
   loads rather than 2. `rules.py` reads the permitted set from the `b`
   op list above (each op's `encode.rd`), so a register that is not
   spelled here cannot be paired by the scheduler either.

## arg-call-pair

*Set up an argument, then call through a hard-coded base register.*

    mv      rda, rs1a
    jalr_ra 4*immb

    li      rda, imma
    jalr_ra 4*immb

    addi4spn rda, 4*imma
    jalr_ra 4*immb

    load    rda, k*imma(x2)
    jalr_ra 4*immb

    store   rs2a, k*imma(x2)
    jalr_ra 4*immb

    addi_rsd rda, imma
    jr_t1   4*immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1a   │g│immb[4:0]│immb[9:5]│ fn3 │   rda   │ opcode5 │1 0│
│h│imma[4:0]│g│immb[4:0]│immb[9:5]│ fn3 │imma+rda │ opcode5 │1 0│
│h│imma[4:0]│g│immb[4:0]│immb[9:5]│ fn3 │  rs2a   │ opcode5 │1 0│

 * Row 1 holds `mv`, which needs only rs1a and rda; row 2 holds the three
   ops that want a wide immediate and an ARGUMENT destination, splitting
   the rd column three-two so `imma` reaches seven bits; row 3 holds the
   stores, whose source register needs all five bits and whose stack
   offsets are small.
 * rd 3 bits costs almost nothing here and buys two: `li` at rd3+imm5
   catches 925 of the 933 that an unrestricted rd catches on cpp- rv32,
   because these are argument setups by construction. 3+7 beats 5+5 by
   68% on cpp `li` and 18% on cpp `addi4spn`.
 * Load and store offsets scale by the ACCESS width, as `c.lwsp` and
   `c.sdsp` do: spill slots are aligned to the access, so `k*imm` costs
   nothing and reaches four or eight times further.
 * `addi4spn` scales by four, which is a real trade rather than a free
   one: 10.7% of cpp's `addi rd,sp` are NOT 4-aligned, because C++ takes
   the address of byte- and short-sized stack temporaries, so scaling
   costs 13% of the cpp hits (6473 against 7456 for a raw 7-bit field)
   and buys 0..508 instead of 0..127. On rv64 the scaled form is the one
   the fit prefers outright.
 * The rd column is a register in row 1 only, so this frame neither hosts
   nor is hosted.

# macro-op-pair

*Both halves of ONE computation over the same operands (mul/mulh, div/rem), declared as a pair so an implementation can fuse them.*

    alu     rda, rs1a, rs2a
    alu     rdb, rs1a, rs2a

    add     rda, rs1a, rs2a
    sltu    rdb, rda, rs1a

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│   rda   │g│  rs2a   │  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│

 * CARRY-OUT, measured (2026-08-04). cpp-rv32 holds 156 carry-shaped
   adjacencies, godot 36, everything else under five. The cluster takes
   the frame from 59 hits to 167 on cpp-rv32, but the corpus total rises
   by 29. The difference is NOT another frame losing the same pairs --
   `alu-alu-chain` cannot encode (add, sltu) at all, since `sltu` appears
   only in its A sets. It is greedy DISPLACEMENT: claiming the `add`
   denies it to whatever was pairing with it from the left, so alu-alu-
   chain drops 74 elsewhere in the stream. Report the frame's worth as
   29, not 108.
 * THE REST OF THE FRAME IS KEPT DESPITE A NEAR-ZERO SCORE. Do not cut
   the mul/div clusters on pairing-rate evidence; they are not there to
   earn pairs.
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
   positionally shared operands number 31 on musl-rv32, 25 on cpp- rv32,
   2 on sqlite-gcc-rv64, 0 on sqlite-rv64 -- so nothing is suppressing
   it. Notably the frame is NOT register-window constrained: this row
   draws four full 5-bit fields, so every register encodes.
 * ORDER IS NOT ARBITRARY, and it is not a dependency either. The two ops
   read the same two sources and neither consumes the other's result, so
   they commute -- but the RISC-V M extension names one sequence as THE
   fusion idiom, and a microarchitecture told to detect fusable pairs is
   looking for that one: MULH[[S]U] rdh, rs1, rs2 ; MUL rdl, rs1, rs2
   DIV[U] rdq, rs1, rs2 ; REM[U] rdr, rs1, rs2 high half first, quotient
   first, "source register specifiers must be in the same order and rdh
   cannot be the same as rs1 or rs2" -- that last clause because the
   fused unit still needs both sources intact when it delivers the second
   result. `_reject_dependence` already enforces exactly that (`a.rd not
   in b.uses_regs`), so rules.py gets it for free.
 * The clusters are listed in the spec's sequence and the compiler
   already emits it (hi-first outnumbers lo-first 22:9 on musl-rv32, div-
   first is universal, and every measured occurrence satisfies the rdh-
   not-a-source rule). rules.py canonicalises, so both directions still
   pair; the canonical direction also gets the lighter dependence test,
   so keep it spec-ordered.
 * The gap is a toolchain one -- a compiler that knew this pairing were
   available would emit the two halves adjacently and in order. Treat the
   low score as a measurement of clang and GCC, not of the frame.

# dual-setup-pair

*Two independent small moves or constants -- argument marshalling.*

    mv      rda, rs1a
    mv/li   rdb, rs2b/immb

    li      rda, imma
    li      rdb, immb

    mv/li   rda, rs1a/imma
    li      ardb, immb

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│   rda   │g│  rs2b   │  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│   rda   │g│immb[4:0]│  rs1a   │ fn3 │   rdb   │ opcode5 │1 0│
│h│   rda   │g│immb[4:0]│imma[4:0]│ fn3 │   rdb   │ opcode5 │1 0│
│h│   rda   │g│immb[4:0]│  rs1a   │ fn3 │immb+rdb │ opcode5 │1 0│
│h│   rda   │g│immb[4:0]│imma[4:0]│ fn3 │immb+rdb │ opcode5 │1 0│
│h│imma+rda │g│immb[4:0]│imma[4:0]│ fn3 │   rdb   │ opcode5 │1 0│

 * THE WIDE BAND IS ARGUMENT-DESTINED, measured (2026-08-05). With the
   width caps relaxed to ten bits, wide `li` destinations are argument
   registers 86-89% of the time on musl-gcc-rv32 + sqlite-rv64 (at 7 bits
   404 arg vs 51 other, at 8 bits 938 vs 154) and 85%/65% on cpp-rv32 --
   the arg-call-pair effect, without the call. The band this replaces, 6
   bits at any rd, is nearly vacant: keeping it alongside the 8-bit band
   (re-measured, full scheduler) buys +18 pairs over three corpora for a
   doubled block. Dropped.
 * Swept with the real scheduler against the 6-bit-any-rd baseline (musl-
   gcc-rv32 / sqlite-rv64 / cpp-rv32, corpus TOTALS so displacement is
   netted): +169 / +429 / +216 pairs. Part of the gain is displacement --
   rsd-alu-pair gives back up to 110, li-branch-chain up to 50 -- which
   the totals already count.
 * `addi4spn` deliberately does NOT get the split: its wide destinations
   are a coin flip on musl+sqlite (129 arg vs 135 other at 7 bits) and
   splitting it regressed musl-gcc while paying on cpp (+375/-71
   relative) -- a C++-marshalling bet, not a win. Its 6-bit band stays as
   it was.
 * The a0-a7 restriction is enforced by scheduler/rules.py (`_ARG_REGS`,
   shared with arg-call-pair); the yaml states it as the 3-bit
   destination part of each split row, which is how arg-call-pair states
   it too. An `imm: {bits}` contract is an opcode property and must agree
   across slots (op_contracts), so both slots declare 8 and both get a
   split row; the rule caps the pair at ONE wide immediate, matching the
   one split per row.
 * PRICED 11 BY THE MODEL, ~19 BY HAND, in a 32-block either way.
   opcode_codepoints scores each op against the slot's WIDEST row, so
   with the split rows present (7-bit fields) it sees li at ext 1 and
   stops charging addi4spn's sixth bit, which in the full-rd rows still
   rides an opcode repeat -- the same widest-row coarseness arg-call-pair
   already lives with. A band-by-band hand count (li 5-any + 8-args, spn
   5 + rider, one spelling per unordered pair) is ~19. Both are inside
   the block; the gap is a known model artifact, not spare room to spend.

## rsd-alu-pair

*Two in-place ALU updates, each rewriting its own source register.*

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
   x0..x31. An earlier draft anticipated cutting them to 4 bits; that is
   not needed and was never adopted.
 * The two slots declare DIFFERENT op sets, and deliberately. Range past
   the row's five drawn bits is bought in opcode entries -- an op
   declaring N bits occupies 2^(N-5) of them -- so weight, not op count,
   is the budget, and one bit on `li` costs what four reg-reg opcodes
   cost. A weighs 16 as fifteen ops that are nearly all weight 1
   (breadth); B weighs 16 as six ops, of which `li` at eight bits is 8
   and `addi` at seven is 4 (depth). The block is 16 x 16 = 256, the same
   as the symmetric set it replaces.
 * The asymmetry is only purchasable because the pair is ORDER-FREE in
   87.1% of the corpus residue: rsd-alu-pair packs two independent
   results, so unless one reads the other's destination the scheduler may
   emit either orientation and only one need be encodable. The list
   scheduler already tries both (its tier-1 and tier-2 partner picks).
   See results/corpus/RSD-RESIDUE.md for the measurement and for the
   weighted optimisation that chose these two sets.

## prologue-pair

*Function prologue: reserve the stack frame and save the return address.*

    addi    sp, -16*imm
    store   rs1b, 16*imm-k(sp)

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1b   │g│imm[4:0] │imm[9:5] │ fn3 │ unused  │ opcode5 │1 0│

 * `rs1b` is a drawn 5-bit field: ANY register may be the one stored at
   the top of the new frame. ra is the overwhelmingly common case but not
   a constraint -- a leaf function that keeps its fp saves s0 there
   instead, and rules.py accepts it.

## epilogue-pair

*Function epilogue: release the stack frame and return.*

    addi    sp, 16*imm
    jr      rs1b

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1b   │g│imm[4:0] │imm[9:5] │ fn3 │ unused  │ opcode5 │1 0│

 * The row draws only the target register: the rs2+rs1 columns carry the
   sp adjustment, so a `jalr` here has a ZERO offset by construction --
   there is no field for one -- and rules.py rejects the nonzero-offset
   spelling.

## arith-jump-pair

*A last in-place computation, then a control transfer.*

    alu     rsda, rsda, rs2a/imma
    jr/jalr rs1b

    li     rsda, imma
    jr/jalr rs1b

┌─┬─────────┬─┬─────────┬─────────┬─────┬─────────┬─────────────┐
│h│ funct5  │g│   rs2   │   rs1   │ fn3 │   rd    │   opcode    │
└─┴─────────┴─┴─────────┴─────────┴─────┴─────────┴─────────────┘
│h│  rs1b   │g│  rs2a   │  rsda   │ fn3 │ unused  │ opcode5 │1 0│
│h│  rs1b   │g│imma[4:0]│  rsda   │ fn3 │ unused  │ opcode5 │1 0│

## setup-jump-pair

*Set up an argument or return value, then transfer control.*

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
│h│  rs1b   │g│imma[4:0]│imma[9:5]│ fn3 │   rda   │ opcode5 │1 0│
│h│  rs1a   │g│immb[4:0]│immb[9:5]│ fn3 │   rda   │ opcode5 │1 0│
│h│imma[4:0]│g│immb[4:0]│immb[9:5]│ fn3 │   rda   │ opcode5 │1 0│

 * `j` covers `jal x0`; a jal with a real destination is a call and is
   excluded from every jump frame.
 * Direct `j` (78-92% of this frame's packets) takes rows 3-4: `rs1b` is
   dropped -- a direct jump has no register operand -- and `immb` gets
   the rs2+rs1 span, a 10-bit displacement in PACKET units. Packets are
   4-byte aligned, so the low bit RVC must carry is dead and a
   displacement costs 0.54x its RVC bits. 10 bits covers 84.6% of direct
   `j` on sqlite and 97-98% on musl.
 * rules.py cannot range-check the displacement: corpus jump operands are
   unresolved labels, so a pairwise rule has nothing to test. The
   scheduled count includes the over-range tail (~15% on sqlite). See
   results/corpus/README.md.
 * On the direct-`j` rows a load has no offset field (offsets are zero in
   98.2% of chained cases anyway) and `li` narrows to 5 bits.
