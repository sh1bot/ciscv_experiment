# Pairing Rules Reference

This document describes every pairing rule defined in
[`scheduler/rules.py`](rules.py): what each rule is for, which instructions it
accepts into the A-slot and B-slot, and — just as importantly — what it
*rejects* and why.

It is written to be read alongside the code. Each rule heading names the
`PairingRule` entry in the `RULES` list and the `check()` function that
implements it.

---

## 1. Background: where these rules sit

### 1.1 The problem being solved

This tool is a workbench for designing a **custom 32-bit packet format** that
packs *two* RISC-V instructions into a single 32-bit word. It is conceptually a
generalisation of the RISC-V **C (compressed) extension**, but instead of
shrinking one instruction into 16 bits, it fuses an ordered pair of
instructions into one 32-bit packet.

A pairing rule answers one question: *can these two specific instructions share
one 32-bit packet?* Each rule corresponds to a candidate packet encoding with a
fixed budget of bits for opcodes, register fields, and immediates. The
constraints in each rule are exactly the things that budget cannot afford —
registers that don't fit a narrowed register field, immediates too large for the
allotted bits, operand relationships the encoding can't express.

### 1.2 A quick RISC-V refresher (the parts these rules lean on)

The instructions referenced by the rules come from several RISC-V extensions.
RV64GC ("G" = IMAFD + Zicsr/Zifencei, "C" = compressed) is the assumed base,
plus the bit-manipulation extensions:

| Source | Instructions used by rules |
|---|---|
| **RV64I** (base integer) | `addi`, `addiw`, `andi`, `add`, `addw`, `sub`, `subw`, `and`, `or`, `xor`, `slli`, `srli`, `srai`, `lui`, loads (`lb/lbu/lh/lhu/lw/lwu/ld`), stores (`sb/sh/sw/sd`), branches (`beq/bne/blt/bge/bltu/bgeu`), `jalr` |
| **M** (mul/div) | `mul`, `mulh`, `mulhu`, `mulhsu`, `div`, `rem`, `divu`, `remu`, `divw`, `remw`, `divuw`, `remuw` |
| **Zba** (address gen) | `sh1add`, `sh2add`, `sh3add` (shift-by-N-then-add) |
| **Zbb** (basic bitmanip) | `min`, `max`, `minu`, `maxu`, `andn` |
| **Pseudo-ops** | `beqz`/`bnez` (`beq`/`bne` vs `x0`), `ret` (`jalr x0,ra,0`), `li`/`mv`/`j` (forms of `addi`/`jal`) |

Two RISC-V facts shape almost every rule:

* **The `x8`–`x15` register window.** RVC's three-bit register fields can only
  name eight registers, `x8`–`x15`. Several rules here narrow further to a
  *four-bit* window, `x0`–`x15`, when a packet must encode two register fields.
* **Scaled immediates.** Memory offsets in RISC-V (and especially in RVC, e.g.
  `c.lwsp`, `c.addi4spn`) are stored *scaled* by the access width — a word
  offset is divided by 4 before encoding. The rules reuse this trick: a "10-bit
  scaled" offset for an `ld` actually reaches `1023 × 8` bytes. The helper
  `uimm_fits(n, shift)` checks "does this immediate fit `n` bits once shifted
  right by `shift`, and is it a multiple of `1<<shift`?".

### 1.3 How a rule is evaluated (the mechanism)

The mechanism lives in [`scheduler/pairing.py`](pairing.py); this file holds
only policy. The key function is `can_pair(a, b)` where **`a` is the A-slot
(executes first)** and **`b` is the B-slot (executes second)**. Order matters.

A pair `(a, b)` is legal if **at least one** rule accepts it. For a given rule,
`a` and `b` are tested in this sequence:

1. **A-slot mnemonic.** `a.mnemonic` must be in `rule.a_mnemonic_set`
   (if the set is `None`, any mnemonic passes).
2. **A prerequisites.** Every property name in `rule.a_prerequisites` must be
   truthy on `a` (e.g. `"is_rsd"`, `"is_li"`, `"reads_stack"`).
3. **B-slot mnemonic.** `b.mnemonic` must be in `rule.b_mnemonic_set`.
4. **B prerequisites.** Every property in `rule.b_prerequisites` must hold on `b`.
5. **`check(a, b)`.** *Accepts* by returning (falling off the end, i.e.
   returning `None`); *rejects* by raising `NotPair(reason)`, where `reason` is a
   human-readable string. `pairing.py` catches `NotPair` at each `check()` call
   site; the reason is what shows up as a "solo reason" annotation in the output.

If no rule accepts, the instruction goes **solo** (it occupies a packet alone).

> **Note on how rejections read.** Because most per-slot constraints are applied
> by composable decorators (see §2) that wrap the rule's `check()`, the raised
> reason names the *first* failing constraint in the decorator stack, not
> necessarily the one you'd guess from the rule's core body.

### 1.4 Slot disqualifiers (apply to all rules)

Before any rule runs, two intrinsic filters apply:

```python
A_SLOT_DISQUALIFIERS = ["is_unknown", "is_control_transfer"]
B_SLOT_DISQUALIFIERS = ["is_unknown"]
```

An instruction with an **unknown opcode** (`is_unknown`) can never occupy either
slot, so it is always solo. (`is_unknown` is set by the decoder when it cannot
recognise the mnemonic.) A **control transfer** (`is_control_transfer` — any
branch, jump, call, tail, or return) can never occupy the **A** slot: the
hardware executes A before B, so a transfer in A would never reach B. Control
transfers are still valid in the B (last) slot — that is exactly where the
`*-branch` and `*-jump` rules place them. These lists are intentionally short —
the design keeps *structural* "this instruction can't go in this slot" reasons
here, and *relational* "this pair doesn't fit" reasons inside the rules.

### 1.5 The backward branch-rescue pass

After the main greedy forward pass, `_backward_pair()` makes a second sweep
looking only for **branch** B-slots that were left solo. For each solo branch it
scans *backwards* for a valid A-slot partner and pulls the pair together —
*only if* moving the candidate later past the intervening instructions is safe
(nothing in between reads the candidate's output or clobbers its inputs). This
is why branch-consuming rules (`*-branch`) can fire even when the producer and
branch weren't originally adjacent.

---

## 2. Shared concepts used across rules

These properties (defined on `Instruction` in
[`isa/instruction.py`](../isa/instruction.py)) recur throughout the rules:

* **RSD form (`is_rsd`)** — "read-source-destination": `rd` is also a source
  (`rd == rs1` or `rd == rs2`). This is the in-place / two-operand shape that
  compressed encodings favour (`c.add rd, rs2` is really `add rd, rd, rs2`).
* **`is_li`** — `addi rd, x0, imm`: load a constant.
* **`is_mv`** — `addi rd, rs1, 0`: register copy.
* **`is_addi4spn`** — `addi rd, sp, imm` with a nonzero multiple-of-4 immediate:
  materialise a stack-slot address (the `c.addi4spn` pattern).
* **`is_commutative`** — operation where operands can swap (`add`, `and`, `or`,
  `xor`, `mul*`, `min*`, `max*`, …). Lets a rule accept the chained operand in
  either source position.
* **`live_out`** — set of registers still needed *after* this instruction. A
  value is **dead** after `b` if it is not in `b.live_out` (or `b` overwrites
  it). Deadness is what licenses a packet to consume a register internally
  without exposing it as an output.
* **`access_width` / `access_shift`** — data width of a load/store (1/2/4/8) and
  its log2. Used for offset scaling.
* **`base_from_auipc`** — the load's base register came from an `auipc`; its
  "offset" is really a `%pcrel_lo` relocation, not a displacement, so it must
  not be treated as a packable immediate.
* **`uimm_fits(n, shift, nonzero)` / `imm_fits(...)`** — range + alignment
  checks for unsigned / signed immediates, with optional scaling and a
  `nonzero='remap'` mode (zero re-encoded as the top of range to balance it).

Most per-slot constraints are expressed as **composable decorators** that wrap a
rule's `check()`; each raises `NotPair(reason)` on failure and otherwise calls
through. A rule's decorator stack *is* most of its definition, and its body is
often empty. The common ALU constraints for the `rsd`/`chain` family are split
between:

* **register range** — the `uses_low_regs` family: `@uses_low_regs` (every
  encoded register in `x0`–`x15`), `@chain_uses_low_regs` (same, but the chain
  register that dies within the packet is exempt), and `@uses_low_regs_here(...)`
  (only the named fields);
* **immediate range** — `@a_imm_ok` / `@b_imm_ok`, backed by `_imm_in_range`:
  `addi/addiw/andi` immediate in `[-64, 64]` (zero allowed; it degenerates to a
  register move), and shift amounts `slli/srli/srai` in `[1, 32]`.

Other reusable decorators include `@must_chain*` (B consumes A's result),
`@no_escape` (that result is dead after B), `@exclusive_rd` (distinct
destinations), and `@a_is_rsd` / `@a_rsd_swappable` (RSD-encodability).

---

## 3. The rules

Rules are listed in `RULES`-list order. For each: its purpose, the slot
mnemonic sets, prerequisites, and concrete match / no-match examples.

---

### 3.1 `rsd-alu-pair`

Two independent in-place ALU operations packed together.

* **A-slot / B-slot mnemonics:** `_RSD_ALU_MN` =
  `{addi, addiw, andi, add, addw, sub, subw, and, andn, or, xor, slli, srli, srai, mul, mulhu}`
* **Prerequisites:** none.
* **`check` (`_rsd_alu_pair`):** the body is empty; the constraints are a
  decorator stack applied to *both* `a` and `b`:
  * `@uses_low_regs` — all encoded registers (`rd`, `rs1`, `rs2`) in `x0`–`x15`;
  * `@a_imm_ok` / `@b_imm_ok` — `addi/addiw/andi` immediate in `[-64, 64]`;
    shifts in `[1, 32]`;
  * `@a_is_rsd_or_li` / `@b_is_rsd_or_li` — each slot is RSD form, or the `li`
    form (`addi rd, x0, imm`), which is always allowed once the range checks pass;
  * `@a_rsd_swappable` / `@b_rsd_swappable` — if `rd == rs2` (not `rs1`) the op
    must be **commutative**, because the narrowed encoding writes back to the
    first source slot;
  * `@exclusive_rd` — the two slots must write **distinct destinations**
    (`a.rd != b.rd`).
    This rule packs two independent, both-live results; a shared destination
    means either B consumes A (a chain — handled, more capably, by
    `chain-alu-pair`, whose shared register need not be in `x0`–`x15`) or A's
    write is dead. Either way it belongs to a different rule, not here.

**Matches**

```asm
addi a0, a0, 5      ; RSD, x10, imm in range
add  a1, a1, a2     ; RSD, x11/x11/x12, all in x0..x15
```

```asm
add  a0, a2, a0     ; rd==rs2 but `add` is commutative — OK
li   a1, 7          ; addi a1, x0, 7 — li form always OK
```

**Does not match**

```asm
sub  a0, a1, a0     ; rd==rs2 and `sub` is NOT commutative → rejected
addi a0, a0, 100    ; immediate 100 > 64 → out of range
add  a6, a6, a7     ; a6=x16, a7=x17 → outside x0..x15
add  a0, a1, a2     ; rd != rs1 and != rs2, not li → not RSD form
li   a0, 5          ; paired with another `… a0, …` → same destination, rejected
                    ;   (a real chain belongs to chain-alu-pair)
```

---

### 3.2 `chain-alu-pair`

A producer/consumer ALU pair: `a` computes a value into a *chain register* that
`b` immediately consumes and which is then dead. Because the intermediate never
leaves the packet, the chain register is **not encoded** — so it is exempt from
the `x0`–`x15` window.

* **A/B mnemonics:** `_RSD_ALU_MN` (same set as above).
* **Prerequisites:** none.
* **`check` (`_chain_alu_pair`):**
  * `a` and `b` pass the register/immediate checks, but `a.rd` (the chain
    register) is *excluded* from the range check on both;
  * `a.rd` must exist;
  * `b` must consume it: `b.rs1 == a.rd`, or `b.rs2 == a.rd` when `b` is
    commutative;
  * the chain value must be dead after `b`: either `b.rd == a.rd` (overwritten)
    or `a.rd ∉ b.live_out`.

**Matches**

```asm
add  t0, a0, a1     ; t0 (chain reg) may be anything, even x5
xor  a2, t0, a3     ; consumes t0 as rs1; t0 dead afterwards
```

**Does not match**

```asm
add  t0, a0, a1
xor  a2, a3, a4     ; B does not use t0 → "B does not consume A's result"
```

```asm
add  t0, a0, a1
add  a2, t0, a3     ; ... but t0 is read again later → t0 in live_out
                    ; → "A's result escapes after B"
```

---

### 3.3 `load-chain-alu-pair`

Like `chain-alu-pair`, but the producer is an **sp-relative load** instead of an
ALU op. Load a value off the stack, immediately fold it into an ALU op, discard
it.

* **A mnemonics:** `_SP_LOAD_MN` = `{lw, lwu, ld}`.
* **B mnemonics:** `_RSD_ALU_MN`.
* **`check` (`_load_chain_alu_pair`):**
  * `a` is an sp-relative load: `a.rs1 == sp (x2)`, offset a nonnegative 8-bit
    *scaled* value (so up to `255 × width` bytes);
  * `b` passes the ALU register/immediate checks, with `a.rd` (loaded chain
    value) exempt from the window;
  * `b` consumes `a.rd` (rs1, or commutative rs2); the value is dead after `b`.

**Matches**

```asm
lw   t0, 16(sp)     ; sp-relative, offset 16 = 4×4 fits 8-bit scaled
add  a0, t0, a1     ; consumes loaded value; t0 dead after
```

**Does not match**

```asm
lw   t0, 16(a1)     ; base is a1, not sp → "memory base is not sp"
add  a0, t0, a2
```

```asm
ld   t0, 4096(sp)   ; 4096/8 = 512 > 255 → offset exceeds 8-bit scaled range
add  a0, t0, a1
```

---

### 3.4 `store-chain-alu-pair`

Mirror of the above: an ALU op produces a value that a **sp-relative store**
immediately writes to the stack, after which it is dead.

* **A mnemonics:** `_RSD_ALU_MN`.
* **B mnemonics:** `_SP_STORE_MN` = `{sw, sd}`.
* **`check` (`_store_chain_alu_pair`):**
  * `a` passes ALU checks with `a.rd` exempt from the window;
  * `b` is an sp-relative store with an 8-bit scaled offset;
  * the store's value register equals `a.rd` (`b.rs2 == a.rd`);
  * `a.rd` is dead after `b` (`a.rd ∉ b.live_out`).

**Matches**

```asm
add  t0, a0, a1
sw   t0, 8(sp)      ; stores A's result to the stack; t0 dead after
```

**Does not match**

```asm
add  t0, a0, a1
sw   t1, 8(sp)      ; storing t1, not A's result t0 → "store value is not A's result"
```

```asm
add  t0, a0, a1
sw   t0, 8(sp)      ; but t0 still live later → "stored value escapes after B"
```

---

### 3.5 `load-sp-branch`

Null-/zero-check on a value just loaded from a deep stack frame. The loaded
value is **kept alive** (the common case is a pointer that is tested and then
used on the non-null path), so there is *no* deadness requirement.

* **A mnemonics:** `_ALL_LOAD_MN` (`lb/lbu/lh/lhu/lw/lwu/ld`).
* **A prerequisites:** `["reads_stack"]` (a load whose base is sp).
* **B mnemonics:** `_ZERO_BRANCH_MN` = `{beqz, bnez}`.
* **`check` (`_load_sp_branch`):** base must be sp; base must not be
  `auipc`-derived; offset fits a **10-bit unsigned byte offset** (`uimm10`,
  *unscaled* — reaches deep frames); the branch must test the loaded value
  (`b.rs1 == a.rd`).

**Matches**

```asm
lw   a0, 100(sp)    ; sp-relative, 100 < 1024
beqz a0, .Lnull     ; tests the loaded value
```

**Does not match**

```asm
lw   a0, 2000(sp)   ; 2000 > 1023 → offset exceeds 10-bit unsigned range
beqz a0, .Lnull
```

```asm
lw   a0, 100(sp)
beqz a1, .Lnull     ; branch tests a1, not the loaded a0 → mismatch
```

---

### 3.6 `load-base-branch`

The shallow-struct-field cousin of `load-sp-branch`: a load from **any** base
register (typically a struct/object pointer) with a small offset, then a
zero-test branch. Wider base reach is traded for a tighter offset.

* **A mnemonics:** `_ALL_LOAD_MN`. **No** `reads_stack` prerequisite.
* **B mnemonics:** `_ZERO_BRANCH_MN` (`beqz/bnez`).
* **`check` (`_load_base_branch`):** any base; base must not be `auipc`-derived;
  offset fits a **5-bit unsigned byte offset** (`uimm5`, 0–31); branch tests the
  loaded value.

**Matches**

```asm
lw   a0, 4(a1)      ; load field at offset 4 of object a1
bnez a0, .Lhave     ; test it
```

**Does not match**

```asm
lw   a0, 64(a1)     ; 64 > 31 → offset exceeds 5-bit unsigned range
beqz a0, .L
```

```asm
lw   a0, 0(a1)      ; base produced by a preceding auipc (GOT access)
beqz a0, .L         ; → "load base is auipc-derived"
```

---

### 3.7 `deref-chain-load-pair`

A pointer chase packed into one word: load a pointer, then immediately
dereference it. `a` loads from `imm10(rb)`; `b` dereferences at `0(rtmp)`. The
intermediate pointer `rtmp` is dead after `b`.

* **A/B mnemonics:** `_CHAIN_LOAD_MN` (all loads).
* **`check` (`_deref_chain_load_pair`):**
  * `a` has base + dest; base must not be `auipc`-derived;
  * `a`'s offset fits a **10-bit width-scaled** unsigned value;
  * `b`'s base is `a`'s result (`b.rs1 == a.rd`); `b`'s offset is **zero**
    (or absent);
  * the pointer is dead after `b` (unless `b` overwrites it).

**Matches**

```asm
ld   t0, 16(a0)     ; load pointer at a0+16
ld   a1, 0(t0)      ; dereference it; t0 dead afterwards
```

**Does not match**

```asm
ld   t0, 16(a0)
ld   a1, 8(t0)      ; B offset must be zero → rejected
```

```asm
ld   t0, 16(a0)
ld   a1, 0(t0)      ; but t0 is used later → "pointer escapes after B"
```

---

### 3.8 `base-chain-load-pair`

Same pointer chase, but the nonzero offset rides on the **second** load
instead of the first: `a` loads at `0(rb)`; `b` dereferences at `imm10(rtmp)`.

* **A/B mnemonics:** `_CHAIN_LOAD_MN`.
* **`check` (`_base_chain_load_pair`):**
  * `a` has base + dest; not `auipc`-derived; `a`'s offset is **zero**/absent;
  * `b.rs1 == a.rd`; `b`'s offset fits a **10-bit width-scaled** unsigned value;
  * pointer dead after `b` (unless overwritten).

**Matches**

```asm
ld   t0, 0(a0)      ; load pointer at a0
ld   a1, 8(t0)      ; dereference at t0+8; t0 dead after
```

**Does not match**

```asm
ld   t0, 8(a0)      ; A offset must be zero → rejected
ld   a1, 8(t0)
```

---

### 3.9 `mem-pair`

Two adjacent memory accesses of the **same width and base**, whose offsets
differ by exactly one data width — i.e. consecutive array/struct elements. Packs
into a single word (think a synthesised load-pair/store-pair).

* **A/B mnemonics:** `_MEM_PAIR_MN` (all loads and stores).
* **`check` (`_mem_pair`):**
  * same mnemonic; same base register; both offsets present;
  * `|a.imm − b.imm| == access_width` (adjacent slots);
  * each offset fits a scaled field: **8-bit** when sp-relative
    (`is_local`), else **5-bit**;
  * for **loads**, the two destinations must differ; **stores** have no further
    constraint.

**Matches**

```asm
lw   a0, 0(a2)      ; consecutive words, same base a2
lw   a1, 4(a2)      ; offsets differ by 4 = width; a0 != a1
```

```asm
sw   a0, 0(sp)      ; sp-relative store pair (8-bit scaled reach)
sw   a1, 4(sp)
```

**Does not match**

```asm
lw   a0, 0(a2)
ld   a1, 8(a2)      ; mnemonic mismatch (lw vs ld)
```

```asm
lw   a0, 0(a2)
lw   a0, 4(a2)      ; same destination register on two loads → rejected
```

```asm
lw   a0, 0(a2)
lw   a1, 8(a2)      ; offsets differ by 8, not 4 → "must differ by exactly 4"
```

---

### 3.10 `arith-mem-pair`

An **independent** RSD arithmetic op alongside a small-offset memory op — no
producer/consumer relationship; they simply co-issue. (True dependencies are
caught by the dependency graph; this rule additionally refuses any pair where
`a` feeds `b`.)

* **A mnemonics:** `_ARITH_MEM_A_MN` = `{add, sub, and, or, addi}`.
* **A prerequisites:** `["is_rsd"]`.
* **B mnemonics:** `_MEM_PAIR_MN` (any load/store).
* **`check` (`_arith_mem_pair`):**
  * `a` is RSD; `a.rd` and `a.rs1` in `x0`–`x15`;
  * if `a` is `addi`, its immediate is in **`[-64, 64]` inclusive, excluding 0**
    (a zero immediate should be encoded as a move from `x0` instead);
  * `b`'s offset is nonnegative, aligned, and fits a **2-bit scaled** field
    (`0, 1×w, 2×w, 3×w`);
  * `a` must not feed `b` (`a.rd ∉ b.uses_regs`).

**Matches**

```asm
add  a0, a0, a1     ; RSD, in window
lw   a2, 8(a3)      ; offset 8 = 2×4, independent of A
```

**Does not match**

```asm
add  a0, a0, a1
lw   a2, 16(a3)     ; 16 = 4×4 > 3×width → offset out of 2-bit scaled range
```

```asm
add  a0, a0, a1
lw   a2, 0(a0)      ; B reads a0 = A's result → "A result feeds B"
```

---

### 3.11 dual-op family (`dual-*-pair`)

Two ops drawn from the **same canonical opcode tuple** that share their inputs
(or operands by role) and write distinct outputs. One packet, two results. The
legal tuples live in `_DUAL_TUPLES`; each family is its own PairingRule (so its
statistics stand alone) but all share one mechanism (`dual_family`):

| Rule | Tuples | Sharing requirement |
|---|---|---|
| `dual-arith2-pair` | `add/sub`, `addw/subw`, `min/max`, `minu/maxu`, `mul/mulh`, `mul/mulhu`, `mul/mulhsu`, `div/rem`, `divu/remu`, `divw/remw`, `divuw/remuw` | both R-type; share `rs1` *and* `rs2` positionally |
| `dual-load-addi-pair` | `ld/addi`, `lw/addi`, `lwu/addi` | load base == addi source; load offset 0; addi imm = nonzero width-scaled `uimm5` (the stride) |
| `dual-store-addi-pair` | `sd/addi`, `sw/addi` | store base == addi source; store offset 0; addi imm = nonzero width-scaled `uimm5` |
| `dual-load-shadd-pair` | `ld/sh3add`, `lw/sh2add`, `lwu/sh2add` | load base == shadd source; load offset 0 (register post-increment form) |
| `dual-store-shadd-pair` | `sd/sh3add`, `sw/sh2add` | store `{base, value}` == shadd's two sources; store offset 0 |
| `dual-indep-pair` | `addi/addi` | both must be `li` / `mv` / `addi4spn`; `addi4spn` imm must fit `uimm5×4` in `[4,128]` |

Common constraints applied to *every* family (via `dual_family` /
`_canonical_dual` / `_reject_dependence`):

* the pair must form a recognised tuple in **either** order;
* outputs must be distinct (`a.rd != b.rd` where both exist);
* **A must not feed B** (`a.rd ∉ b.uses_regs`) — these are independent ops;
* **canonical vs. reverse order:** when the instructions appear in tuple order
  the B-slot op may legally overwrite a shared source (a WAR that resolves
  because B runs second); in **reverse** order the pair must be *fully*
  independent (`b.rd ∉ a.uses_regs`).

**Matches**

```asm
mul  a0, a2, a3     ; arith2: share rs1=a2, rs2=a3
mulh a1, a2, a3     ; distinct dest; the classic 64×64→128 idiom
```

```asm
ld   a0, 0(a1)      ; load_addi: zero load offset, base a1
addi a1, a1, 8      ; addi imm 8 = 1×8 width-scaled stride; pointer post-increment
```

```asm
li   a0, 5          ; indep_pair: li + li
li   a1, 7
```

**Does not match**

```asm
add  a0, a2, a3
sub  a1, a2, a4     ; arith2 but rs2 differs (a3 vs a4) → "source operands differ"
```

```asm
ld   a0, 8(a1)      ; load_addi requires zero load offset → rejected
addi a1, a1, 8
```

```asm
mul  a0, a2, a3
mulh a0, a2, a3     ; same destination a0 → rejected
```

---

### 3.12 `chain-li-branch`

Materialise a small comparison constant, then branch comparing it against a
register. The constant register is dead after the branch (it only carried the
literal).

* **A mnemonics:** `{addi}`; **A prerequisite:** `["is_li"]`.
* **B mnemonics:** `_LI_BRANCH_B_MN` = `{beq, bne, blt, bge, bltu, bgeu}`
  (the two-register comparison branches — *not* `beqz/bnez`).
* **`check` (`_chain_li_branch`):** `a` is `li`; immediate fits **8-bit signed**
  (`-128..127`); `b` uses `a.rd` as `rs1` *or* `rs2`; `a.rd` is dead after `b`
  (`a.rd ∉ b.live_out`).

**Matches**

```asm
li   t0, 10
blt  a0, t0, .Lloop ; compares a0 < 10; t0 dead after
```

**Does not match**

```asm
li   t0, 500        ; 500 > 127 → out of 8-bit signed range
blt  a0, t0, .L
```

```asm
li   t0, 10
blt  a0, a1, .L     ; branch doesn't use t0 → "B does not use A's result"
```

---

### 3.13 `addi-branch-pair`

The loop-counter / pointer-stride idiom: bump a register in place, then a
comparison branch reads it. Unlike `chain-li-branch`, the register is *not*
required to be dead (a loop counter usually lives on).

* **A mnemonics:** `{addi, addiw}`; **A prerequisite:** `["is_rsd"]`.
* **B mnemonics:** the comparison branches `{beq, bne, blt, bge, bltu, bgeu}`.
* **`check` (`_addi_branch_pair`):** `a` is RSD; `a.rd` in `x0`–`x15`; immediate
  fits **8-bit signed**; `b` uses `a.rd` as `rs1` or `rs2`.

**Matches**

```asm
.Lloop:
  addi a0, a0, 1    ; in-place increment, a0 in window
  blt  a0, a1, .Lloop
```

**Does not match**

```asm
addi a0, a1, 1      ; not RSD (rd a0 != rs1 a1) → "A not RSD form"
blt  a0, a2, .L
```

```asm
addi a6, a6, 1      ; a6 = x16, outside x0..x15
blt  a6, a7, .L
```

---

### 3.14 `chain-bit-test-branch`

Isolate a bit (or a bit field) and branch on whether it is zero. `a`'s result is
dead after `b`. Notably, `andi` immediates that are not single bits but *are*
expressible as a shift (`2^N−1` low-mask or `~(2^N−1)` high-mask) are accepted,
because the emitter can rewrite them to `slli`/`srli` with the same zero/nonzero
outcome.

* **A mnemonics:** `_BIT_BRANCH_A_MN` = `{andi, slli, srli}` (note: **not**
  `srai`).
* **B mnemonics:** `_BIT_BRANCH_B_MN` = `{beqz, bnez, beq, bne}`.
* **`check` (`_chain_bit_test_branch`):**
  * `a.rd` exists;
  * if `b` is `beq`/`bne`, its `rs2` must be `x0` (so it is a true zero-test —
    the alias of `beqz`/`bnez`);
  * for `andi`, the immediate must be a power of two **or** shift-expressible
    (`2^N−1` / `~(2^N−1)`); `slli`/`srli` accept any shift amount;
  * `b.rs1 == a.rd`; `a.rd` dead after `b` (unless `b` overwrites it).

**Matches**

```asm
andi t0, a0, 8      ; isolate bit 3 (pow2)
beqz t0, .Lclear    ; t0 dead after
```

```asm
slli t0, a0, 4      ; shift accepted directly
bnez t0, .Lset
```

**Does not match**

```asm
andi t0, a0, 6      ; 6 is neither pow2 nor 2^N-1 / ~(2^N-1) → rejected
beqz t0, .L
```

```asm
andi t0, a0, 8
beq  t0, a1, .L     ; beq with rs2 != x0 → "requires rs2==zero"
```

---

### 3.15 `pre-inc-pair`

A pre-increment / accumulate: `a` updates a register in place (RSD), and `b`
*reads that updated register* — typically as a memory base (with zero offset) or
the LHS of a compare. Strictly canonical order (B depends on A).

* **A mnemonics:** `_PRE_INC_A_MN` = `{addi, sh2add, add}`;
  **A prerequisite:** `["is_rsd"]`.
* **B mnemonics:** `_PRE_INC_B_MN` = `{ld, sd, lw, sw, slt}`.
* **Legal tuples** (`_PRE_INC_TUPLES`): `addi/ld`, `addi/sd`, `sh2add/lw`,
  `sh2add/sw`, `add/slt`.
* **`check` (`_pre_inc_pair`):** tuple recognised; `a` is RSD with a dest;
  `b.rs1 == a.rd`; if `b` is a memory op its offset must be **zero**; and `b`
  must not write `a.rd` (`b.rd != a.rd`) — the updated value typically survives.

**Matches**

```asm
addi a0, a0, 8      ; pre-increment pointer by 8
ld   a1, 0(a0)      ; load through the updated pointer (zero offset)
```

**Does not match**

```asm
addi a0, a0, 8
ld   a1, 8(a0)      ; B memory offset must be zero → rejected
```

```asm
addi a0, a1, 8      ; not RSD → "A not in RSD form"
ld   a2, 0(a0)
```

---

### 3.16 `prologue-pair`

Function prologue: reserve the stack frame and save the return address in one
packet. Order-sensitive — A allocates the frame, then B stores `ra` into it, so
the store's landing address is only valid after the `addi` has run.

* **A mnemonics:** `{addi}`.
* **B mnemonics:** `{sw, sd}`.
* **`check` (`_prologue_pair`):**
  * A is `addi sp, sp, -N` (`a.rd == a.rs1 == sp`) with `N` a nonzero
    **7-bit `uimm×16`** (negative multiple of 16, `nimm_fits(7, 4)`);
  * B stores `ra` (`b.rs2 == x1`) with `sp` as base (`b.rs1 == sp`);
  * the store lands at the top of the freshly reserved frame:
    `b.imm + b.access_width + a.imm == 0`.

**Matches**

```asm
addi sp, sp, -16    ; reserve a 16-byte frame
sd   ra, 8(sp)      ; ra at the top: 8 + 8 (sd width) − 16 = 0
```

**Does not match**

```asm
addi sp, sp, -16
sd   ra, 0(sp)      ; delta 0 + 8 − 16 ≠ 0 → "B-bad-delta"
                    ;   (but this DOES pair via pre-inc-pair, §3.15)
```

```asm
addi sp, sp, -16
sd   a0, 8(sp)      ; stores a0, not ra → "B-not-RA-src"
```

---

### 3.17 `epilogue-pair`

Function epilogue: restore the stack pointer and return/tail-jump in one packet.
Order-sensitive: the packet runs A before B and B is a control transfer, so the
`addi` must be A (executes first) and the `jalr`/`ret` must be B (executes
last). The reverse would run the transfer first and skip the `addi`;
`is_control_transfer` also keeps `jalr`/`ret` out of the A slot.

* **A mnemonics:** `_EPILOGUE_A_MN` = `{addi}`.
* **B mnemonics:** `_EPILOGUE_B_MN` = `{jalr, ret}`.
* **`check` (`_epilogue_pair`):**
  * A is the `addi`, B is the `jalr`/`ret`;
  * the `addi` must be `addi sp, sp, +N` with `N` a nonzero **7-bit `uimm×16`**
    (positive multiple of 16, max `127×16 = 2032`);
  * the `jalr`/`ret` must write `x0` or `x1` (a return or a tail call) with a
    **12-bit signed** offset.

**Matches**

```asm
addi sp, sp, 16     ; pop a 16-byte frame
ret                 ; jalr x0, ra, 0
```

**Does not match**

```asm
addi sp, sp, 24     ; 24 is not a multiple of 16 → not a 7-bit uimm×16
ret
```

```asm
addi sp, sp, -16    ; negative (prologue, not epilogue) → not nonzero uimm
ret
```

```asm
addi a0, a0, 16     ; not adjusting sp → "A not addi sp, sp"
ret                 ;   (but this DOES pair via arith-jump-pair, §3.18)
```

---

### 3.18 `arith-jump-pair`

Pack a productive RSD ALU op into the same packet as a trailing unconditional
control transfer — the packet's B slot always executes last, so an ALU result
computed in A is available before control leaves. Ported from the legacy
scheduler's `arith_jump`; it is offset-independent and its single biggest win on
real code (function epilogues, tail jumps).

* **A-slot mnemonics:** `_RSD_ALU_MN` (as `rsd-alu-pair`).
* **B-slot mnemonics:** `_SMALL_JUMP_MN` = `{ret, jalr, j, jal}`.
* **`check` (`_arith_jump_pair`):**
  * `b` is a *small jump* (`_is_small_jump`): `ret`, a register-indirect jump
    (`jr` / `jalr` with zero offset), or a direct jump (`j` / `jal x0`).
    **Calls are excluded** (they save a link register).
  * `a` passes the `rsd-alu` per-slot check (RSD or `li` form, regs `x0`–`x15`,
    immediate in range).
* No register relationship between `a` and `b` is required — the dependency
  graph already orders them; this rule only co-locates.

> Direct jumps (`j`/`jal`) carry a target offset that is **not** range-checked
> here — the same optimism the `*-branch` rules apply (see `CLAUDE.md`). Returns
> and register-indirect jumps need no offset field and are always encodable.

**Matches**

```asm
addi a2, a2, 55     ; RSD ALU, in window
j    .Lexit         ; direct jump → paired
```
```asm
add  a0, a0, a1     ; compute return value
ret                 ; return → paired
```

**Does not match**

```asm
add  a6, a6, a7     ; a6=x16 outside x0..x15 → A rejected
ret
```
```asm
mv   a0, a1
jalr ra, a5, 0      ; a call (saves ra) → not a small jump
```

---

### 3.19 `mvload-jump-pair`

Same B slot as `arith-jump-pair`, but the A slot is a register move / load
immediate, or a small-offset load. Ported from the legacy `mv_load_jump`.

* **A-slot mnemonics:** `_MVLOAD_JUMP_A_MN` = `{addi}` ∪ all loads.
* **B-slot mnemonics:** `_SMALL_JUMP_MN` (as above).
* **`check` (`_mvload_jump_pair`):**
  * `b` is a small jump;
  * `a` is `mv`/`li` (an `addi` form), **or** a load whose offset fits the
    2-bit scaled field `0..3×width` (aligned, non-negative).

**Matches**

```asm
mv   a0, a1         ; register move
ret                 ; → paired
```
```asm
lw   a0, 8(a1)      ; offset 8 = 2×4, in range
jr   a5            ; → paired
```

**Does not match**

```asm
lw   a0, 64(a1)     ; 64 > 3×4 → offset out of range
ret
```

---

## 4. Summary table

| Rule | A-slot | B-slot | Relationship | Key limits |
|---|---|---|---|---|
| `rsd-alu-pair` | RSD ALU | RSD ALU | independent | regs x0–x15; imm −64..64; shift 1..32 |
| `chain-alu-pair` | ALU | ALU | A→B, dead | chain reg unencoded; others x0–x15 |
| `load-chain-alu-pair` | sp-load | ALU | A→B, dead | load offset uimm8×w |
| `store-chain-alu-pair` | ALU | sp-store | A→B, dead | store offset uimm8×w |
| `load-sp-branch` | sp-load | beqz/bnez | A→B, **alive** | offset uimm10 (bytes) |
| `load-base-branch` | any load | beqz/bnez | A→B, **alive** | offset uimm5 (bytes) |
| `deref-chain-load-pair` | load | load | pointer chase, dead | A offset uimm10×w; B offset 0 |
| `base-chain-load-pair` | load | load | pointer chase, dead | A offset 0; B offset uimm10×w |
| `mem-pair` | load/store | same | adjacent elements | offsets differ by width; uimm5/8×w |
| `arith-mem-pair` | RSD arith | load/store | independent | A regs x0–x15; B offset 0..3×w |
| `dual-*-pair` | tuple op | tuple op | shared inputs | per-family (see §3.11) |
| `chain-li-branch` | li | cmp-branch | A→B, dead | imm8 signed |
| `addi-branch-pair` | addi/addiw RSD | cmp-branch | A→B, alive | rd x0–x15; imm8 signed |
| `chain-bit-test-branch` | andi/slli/srli | zero-branch | A→B, dead | andi pow2 / shift-expressible |
| `pre-inc-pair` | RSD addi/sh2add/add | ld/sd/lw/sw/slt | A→B, alive | B mem offset 0 |
| `prologue-pair` | addi sp (−N) | sw/sd ra | A→B (alloc then save) | sp adj nimm7×16; ra at frame top |
| `epilogue-pair` | addi sp / ret-jalr | A→B (addi then transfer) | — | sp adj uimm7×16; ret rd x0/x1 |
| `arith-jump-pair` | RSD ALU | ret/jr/j/jalr | independent | A regs x0–x15; calls excluded |
| `mvload-jump-pair` | mv/li or small-offset load | ret/jr/j/jalr | independent | load offset 0..3×w; calls excluded |

*"Dead" / "alive" describes whether A's result must be unused after the B-slot
instruction.*
