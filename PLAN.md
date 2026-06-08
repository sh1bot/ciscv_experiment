# RISC-V Instruction Scheduler — Design Plan

## Purpose

This tool reads RISC-V assembly source and produces annotated assembly output in
which instructions have been greedily paired into 32-bit packets.  A packet
contains either one solo instruction (occupying a full 32-bit word) or two
instructions sharing a single 32-bit word.  All instruction boundaries remain
32-bit aligned.  The pairing rules are the primary experimental artefact of the
project; they are deliberately isolated so that iterating on them requires only
local edits to one file.

**Execution model.**  Within a packet, the A-slot instruction executes first and
completes before the B-slot instruction begins.  B may freely read registers
written by A; A is unaffected by anything B does.  This is sequential execution
in hardware — the only difference from two consecutive unpaired instructions is
code density.  Consequently, register data-dependencies within a pair are not a
pairing constraint; they are already enforced by the dependency graph which
places B after A in the instruction ordering.  Pairing rules express only
*hardware structural constraints* — what instruction-type combinations the packet
format can physically encode — not register compatibility.

RVC eligibility is computed and annotated independently.  It has no effect on
pairing decisions — it is informational only.

---

## Project Layout

```
rv_scheduler/
├── __main__.py
├── isa/
│   ├── registers.py          # register indices, ABI names, calling-convention sets
│   ├── abi.py                # call_liveness_effect() — ABI-implied use/def sets
│   ├── instruction.py        # Instruction base class + all computed properties
│   └── decode/
│       ├── __init__.py       # public parse_line() factory
│       ├── rv_i.py           # RV32I / RV64I
│       ├── rv_m.py           # M extension
│       ├── rv_a.py           # A extension
│       ├── rv_f.py           # F extension
│       ├── rv_d.py           # D extension
│       ├── rv_c.py           # C extension + rvc_eligible logic
│       ├── rv_b.py           # B (Zba / Zbb / Zbc / Zbs)
│       ├── rv_v.py           # V extension
│       └── rv_zicsr.py       # Zicsr
├── analysis/
│   ├── cfg.py                # basic-block + function identification, CFG edges
│   ├── depgraph.py           # per-block RAW/WAR/WAW dependency graph
│   └── liveness.py           # iterative liveness dataflow, per-instruction sets
├── scheduler/
│   ├── pairing.py            # can_pair(a, b) -> None | reason_str; greedy pass
│   ├── rules.py              # PairingRule dataclass + all concrete rules
│   └── reorder.py            # list scheduling (default) + BnB (--thorough)
└── output/
    └── annotator.py          # formats annotated assembly output
```

---

## 1. `isa/registers.py`

Define integer and float register sets.  Map every ABI name and `x`/`f` numeric
alias to a canonical integer index (0–31 for each bank).

```python
REG_ALIASES: dict[str, int]   # "zero","ra","sp",…,"x0"–"x31" -> 0–31
FREG_ALIASES: dict[str, int]  # "ft0"–"ft11","fs0"–"fs11","fa0"–"fa7" -> 0–31

CALLER_SAVED: frozenset[int]  # ra, t0–t6, a0–a7  (integer)
CALLEE_SAVED: frozenset[int]  # sp, s0–s11        (integer)
ARG_REGS:     frozenset[int]    # a0–a7
RET_REGS:     frozenset[int]    # a0, a1

FCALLER_SAVED: frozenset[int] # ft0–ft11, fa0–fa7
FCALLEE_SAVED: frozenset[int] # fs0–fs11
FARG_REGS:     frozenset[int]   # fa0–fa7
FRET_REGS:     frozenset[int]   # fa0, fa1
```

Provide a helper `reg_name(index) -> str` returning the canonical ABI name.

---

## 2. `isa/abi.py`

A single function consumed by the liveness pass:

```python
def call_liveness_effect(insn: Instruction) -> tuple[
    frozenset[int],  # implicit integer uses
    frozenset[int],  # implicit integer defs (clobbered)
    frozenset[int],  # implicit float uses
    frozenset[int],  # implicit float defs
    frozenset[int],  # live_out_seed: integer registers conservatively live after this insn
    frozenset[int],  # live_out_seed: float registers conservatively live after this insn
    bool,            # terminates_function
]:
```

A call is modelled as a single very busy instruction: it reads all argument
registers (integer and float) and `sp`, clobbers all caller-saved registers
(integer and float), and after it returns, the caller must assume all
callee-saved registers and both return registers are live.  The `live_out_seed`
fields capture this post-call conservative assumption separately from the
`implicit uses` (which drive dep-graph edges *into* the call, not *out* of it).

For terminating instructions (`terminates_function = True`) there are no
successors and `live_out_seed` equals `implicit uses` — what the frame reads
on exit is what must be live.  For non-terminating calls, `live_out_seed` is
the post-return live set rather than the pre-call argument set.

Detection priority: mnemonic is checked first; structural `jalr`/`jal` patterns
are the fallback for assembler output that has already expanded pseudo-instructions.

| Case | Detection | Implicit int uses / float uses | Implicit int defs / float defs | Live-out seed (int / float) | Terminates |
|---|---|---|---|---|---|
| Call (pseudo) | mnemonic `"call"` | `ARG_REGS ∪ {sp}` / `FARG_REGS` | `CALLER_SAVED` / `FCALLER_SAVED` | `RET_REGS ∪ CALLEE_SAVED` / `FRET_REGS ∪ FCALLEE_SAVED` | False |
| Call (encoded) | `jal`/`jalr` writing `ra` (x1) or `t0` (x5) | same | same | same | False |
| Return (pseudo) | mnemonic `"ret"` | `RET_REGS ∪ CALLEE_SAVED` / `FRET_REGS ∪ FCALLEE_SAVED` | ∅ / ∅ | (same as uses) | True |
| Return (encoded) | `jalr x0, ra, 0` | `RET_REGS ∪ CALLEE_SAVED` / `FRET_REGS ∪ FCALLEE_SAVED` | ∅ / ∅ | (same as uses) | True |
| Tail call (pseudo) | mnemonic `"tail"` | `ARG_REGS ∪ {sp}` / `FARG_REGS` | ∅ / ∅ | (same as uses) | True |
| Tail call / indirect jump (encoded) | `jalr x0, rs1` (any form not matching Return above) | same as tail call pseudo | same | same | True |

`sp` (x2) is in `CALLEE_SAVED` and therefore already present in the return and
tail-call use sets; it is listed explicitly in the call row where it would
otherwise be absent.

Any `jalr x0` that is not an exact `jalr x0, ra, 0` return is treated
conservatively as a tail call — it may be a computed-goto (switch dispatch) or
a tail call; we cannot distinguish them from the assembly text alone, and the
tail-call treatment (ARG_REGS ∪ FARG_REGS ∪ {sp} as uses) is the safer choice.

**Unclassified `jalr`** (rd ≠ x0 and rd ≠ ra and rd ≠ t0): `call_liveness_effect()`
returns all-empty sets and `terminates_function = True`.  The dep-graph edges from
the encoded register operands still constrain ordering.  The CFG gives this block
no successors (register-computed target, treat as function exit), consistent with
`terminates_function = True`.

`t0` (x5) is treated as a link register alongside `ra` (x1) because the RISC-V
psABI designates it as the alternate link register for linker call stubs.

All argument registers are conservatively assumed live at every call site since
the precise argument count is not known from assembly text alone.

---

## 3. `isa/instruction.py` — `Instruction` base class

```python
@dataclass
class Instruction:
    mnemonic:      str
    operands:      list[str]
    raw:           str            # original source line, reproduced verbatim on output
    label:         str | None     # label on this line, if any

    # Non-instruction lines (assembler-internal labels such as .Lpcrel_hi*)
    # that immediately precede this instruction in the source are stored here
    # so they travel with the instruction if it is reordered.
    prefix_lines:  list[str] = field(default_factory=list)

    # Populated by the decoder subclass:
    rd:   int | None              # integer destination register index
    rs1:  int | None
    rs2:  int | None
    rs3:  int | None              # FMA etc.
    frd:  int | None              # float destination
    frs1: int | None
    frs2: int | None
    frs3: int | None
    imm:  int | None
    branch_target: str | None     # label string for branches / jumps
    is_unknown:    bool = False   # True for unrecognised mnemonics (best-effort decode)

    # Populated by the liveness pass (initially empty):
    live_in:   frozenset[int] = field(default_factory=frozenset)
    live_out:  frozenset[int] = field(default_factory=frozenset)
    flive_in:  frozenset[int] = field(default_factory=frozenset)
    flive_out: frozenset[int] = field(default_factory=frozenset)

    # Populated by the pairing pass (initially empty):
    solo_reasons: set[str] = field(default_factory=set)  # rejection strings from can_pair()
```

### Computed properties

All are derived from the stored fields; none are stored separately.

| Property | Definition |
|---|---|
| `is_rsd` | `rd is not None and (rd == rs1 or rd == rs2)` — True when the destination register also appears as a source operand.  Note: RVC CA-format eligibility checks (c.sub, c.xor, c.or, c.and, c.subw, c.addw, c.srli, c.srai, c.andi) require specifically `rd == rs1`; the `rv_c.py` checks test `rd == rs1` directly rather than relying on the broader `is_rsd`. |
| `is_commutative` | mnemonic is one of `add`, `mul`, `mulh`, `mulhu`, `mulhsu`, `and`, `or`, `xor`, `min`, `minu`, `max`, `maxu`, `fadd`, `fmul` and their `*w` variants |
| `reads_rd` | instruction reads `rd` before writing it — **not** AMO instructions; AMOs write `rd` with the old memory value but do not read `rd` |
| `writes_rd` | `rd is not None` |
| `writes_memory` | store instruction |
| `reads_memory` | load instruction |
| `access_width` | byte width of the memory access (1/2/4/8), or `None` for non-memory instructions — inferred from the mnemonic (lb/sb=1, lh/sh=2, lw/sw/flw/fsw=4, ld/sd/fld/fsd=8) |
| `access_shift` | `int.bit_length(access_width) - 1` when `access_width` is not None, else `None` — the natural alignment shift; use as the `shift` argument to `imm_fits`/`uimm_fits` when checking whether an offset is naturally aligned |
| `is_branch` | conditional branch (two CFG successors) |
| `is_jump` | unconditional jump (JAL, JALR — one or zero successors) |
| `is_call` | mnemonic `"call"`, or JAL/JALR writing `ra` (x1) or `t0` (x5) — covers both the unexpanded `call` pseudo and the encoded form |
| `is_tail` | mnemonic `"tail"` — the unexpanded tail-call pseudo; not expanded, not a jump or call in the encoded sense, but transfers control and must not be in A-slot |
| `is_return` | `jalr x0, ra, 0` |
| `is_csr` | accesses a CSR |
| `is_fence` | FENCE / FENCE.I |
| `is_atomic` | A-extension instruction |
| `has_side_effects` | `is_call or is_return or writes_memory or is_csr or is_fence or is_atomic` |
| `uses_int` | `frozenset` of integer registers read by this instruction, **excluding x0** |
| `defs_int` | `frozenset` of integer registers written by this instruction, **excluding x0** |
| `uses_float` | `frozenset` of float registers read |
| `defs_float` | `frozenset` of float registers written |
| `rvc_eligible` | see §5 |
| `rs1_in_rvc_range` | `rs1 is not None and rs1 in range(8, 16)` |
| `rs2_in_rvc_range` | `rs2 is not None and rs2 in range(8, 16)` |
| `rd_in_rvc_range` | `rd is not None and rd in range(8, 16)` |

**x0 is excluded from `uses_int` and `defs_int`** — writes to x0 are no-ops and
reads of x0 always produce zero, so x0 participates in no data dependencies and
must not appear in liveness sets (otherwise it would be "live" everywhere and
pollute every live_in/live_out set in the file).  However, the raw decoded fields
`rd`, `rs1`, `rs2` *may* still hold index 0 when the instruction architecturally
references x0 (e.g. `jalr x0, ra, 0` has `rd = 0`, `jalr x1, x0, target` has
`rs1 = 0`).  These raw fields are needed for instruction classification (e.g.
`is_return` checks `rd == 0`) and RVC eligibility checks; they are just excluded
from the `uses_int`/`defs_int` computed sets.

Helper predicates (used internally by decoders and rules):

```python
def imm_bits(self, n: int) -> bool:
    """True if self.imm fits in a signed n-bit two's-complement field."""

def uimm_bits(self, n: int) -> bool:
    """True if self.imm fits in an unsigned n-bit field (0 ≤ imm < 2**n)."""

def imm_multiple(self, shift: int) -> bool:
    """True if self.imm is a non-None multiple of (1 << shift).
    Equivalent to (imm & ((1 << shift) - 1)) == 0.
    shift=0 always returns True (every integer is a multiple of 1).
    shift=1 → 2-byte aligned, shift=2 → 4-byte aligned, etc."""

def imm_fits(self, n: int, shift: int = 0) -> bool:
    """Combined signed-range + alignment check.
    True iff imm_bits(n) and imm_multiple(shift).
    Equivalent to: imm fits in a signed n-bit field whose low `shift`
    bits are implicitly zero (the field encodes imm >> shift in n bits).
    This is the standard RISC-V load/store offset encoding:
      - lw/sw:  imm_fits(12, 0)  — full 12-bit signed, byte granular
      - c.lw:   imm_fits(7, 2)   — 7-bit unsigned × 4 (use uimm_fits)
    For unsigned variants use uimm_fits()."""

def uimm_fits(self, n: int, shift: int = 0) -> bool:
    """Combined unsigned-range + alignment check.
    True iff uimm_bits(n) and imm_multiple(shift)."""
```

The `shift` parameter reflects how memory instructions encode their offsets: a
`lw` with a 4-byte-aligned offset can store `offset >> 2` in fewer bits (as in
the C-extension encodings) while still representing a wider byte range.  Rules
that test whether two adjacent loads/stores can share an encoding call
`imm_fits` / `uimm_fits` with the appropriate `n` and `shift` for the target
encoding.

**Access width from opcode.**  Load and store mnemonics encode the access size
in the opcode (`lb`/`sb` = 1, `lh`/`sh` = 2, `lw`/`sw` = 4, `ld`/`sd` = 8,
etc.).  An `access_width` property returns this as an integer, or `None` for
non-memory instructions.  Pairing rules that need the natural alignment shift
use `int.bit_length(access_width) - 1` to derive `shift` (e.g. width 4 →
shift 2).

---

## 4. `isa/decode/` — Decoders

### Two-pass parsing

The parser makes two passes over the source file before decoding any
instructions:

1. **Pre-scan:** collect all label definitions, classify each as a *barrier
   label* (appears as a branch/jump target anywhere in the file, or declared by
   `.globl` / `.weak`) or a *non-barrier label* (never a branch target and not
   globally visible).  Assembler-internal labels (`.Lpcrel_hi*`, `.Lfunc_end*`,
   `.Ltmp*`) are almost always non-barriers.

   **Limitation:** jump-table entries (e.g. `.word .Lcase3` in `.data` or
   `.rodata`) reference text labels but are not branch/jump instructions; the
   pre-scan does not read data sections and will not see these references.  A
   label that is a computed-goto target only via a jump table will be misclassified
   as non-barrier.  Mitigations: (a) function-boundary hard stops (§6) limit the
   blast radius — reordering cannot cross functions; (b) any label that is also
   `.globl` is a barrier; (c) this is a known limitation and may produce incorrect
   output for code that uses computed-goto / switch-dispatch tables without
   separate `.globl` labels on each case.
2. **Decode pass:** parse instructions.  Non-barrier labels encountered on their
   own line are accumulated as `prefix_lines` on the immediately following
   instruction.  Barrier labels split basic blocks.  If a barrier label is
   encountered while a non-barrier prefix buffer is non-empty, the buffered
   lines are attached as `prefix_lines` on the first instruction of the *new*
   block (not lost and not associated with the preceding block).  If a
   non-barrier label appears at the end of the file with no following instruction
   (orphaned), it is emitted verbatim at the end of the output as a trailing
   line — it has nothing to attach to and is not a pairing consideration.

This ordering is necessary because `prefix_lines` attachment requires knowing
barrier status upfront, and barrier status requires seeing all branch targets
before any instruction is decoded.

### Decoder interface

Each module exports:

```python
def match(mnemonic: str) -> type[Instruction] | None
```

`decode/__init__.py` calls each module's `match()` in extension-priority order.
If no module matches, an `UnknownInstruction` subclass is returned (not raised)
using best-effort decoding (see below) — unrecognised mnemonics are never fatal.

### Best-effort decoding of unknown instructions

When no decoder matches a mnemonic, the instruction is decoded as follows:

- Scan the operand list left-to-right, parsing each token as a register name if
  possible and ignoring any token that is not a valid register name.
- `defs_int` = the first register-like operand, if any, **excluding x0**.
- `uses_int` = all remaining register-like operands, **excluding x0**.
- `is_unknown = True` — the annotator will emit a `[?]` marker on output.
- The instruction is **not** treated as a barrier in the dependency graph.
  It participates via its inferred `defs_int` / `uses_int` (RAW/WAR/WAW edges
  only) and may be reordered if those edges permit.  Side-effect and memory
  behaviour is unknown, so the instruction is treated as both reading and
  writing memory (conservative memory ordering edge against all adjacent
  memory operations), but it does not anchor all other instructions to it the
  way a true barrier (AMO, fence, fence.i, ecall, ebreak, call, tail) does.
- A `[?]` annotation appears in the output for every unknown instruction.
- A warning is emitted on stderr: `warning: unknown mnemonic '<mnemonic>'`
  on the first occurrence of each unknown mnemonic.  If the inferred register
  effects are wrong, the instruction should be added to the known-instruction
  tables.

### Pseudo-instruction handling

`nop`, `mv`, `li`, `neg`, `not`, `seqz`, `snez`, `sltz`, `sgtz` and
similar single-instruction pseudo-instructions are expanded to their canonical
equivalents during parsing.  All downstream code sees only real instructions.

`ret`, `call`, and `tail` are **not** expanded — they are retained as single
instructions with their original mnemonic.  Their ABI effects (argument registers
used, caller-saved registers clobbered) are handled by `call_liveness_effect()`
in `isa/abi.py`, which detects them by mnemonic first.  Retaining `ret` as-is
ensures the mnemonic `"ret"` detection in `call_liveness_effect()` remains live
code, and avoids ambiguity with the `jalr x0, ra, 0` structural form (which is
also a valid detection path as the encoded fallback row).

### C-extension mnemonic handling

C-extension instructions fall into two categories:

1. *Aliases* (e.g. `c.add`, `c.addi`, `c.srli`): the compressed form implies
   `rd == rs1`.  The decoder splices in the implicit `rs1 = rd` operand and
   delegates to the corresponding base-ISA table entry.
2. *Canonical forms* (e.g. `c.mv`, `c.li`, `c.jr`, `c.jal`): have their own
   table entries and are renamed to a canonical base-ISA mnemonic after decode
   (`c.mv` → `add`, `c.jr` → `jalr`, etc.).

Both categories ultimately produce a base-ISA `Instruction`; no compressed
mnemonic survives past `parse_line`.

---

## 5. `isa/decode/rv_c.py` — RVC eligibility

The `rvc_eligible` property is computed by testing the instruction against every
RVC encoding rule from the RISC-V C extension specification and returning `True`
if any rule matches.

**Branch and jump offset ranges are not checked.**  Label addresses are unknown
at schedule time, so the offset constraints for `c.beqz` (±256 B), `c.bnez`
(±256 B), `c.j` (±2 KB), and `c.jal` (±2 KB) are skipped.  The annotation is
therefore an optimistic estimate for branches.

**No architecture gating.**  RV32-only rules (`c.jal`) and RV64-only rules
(`c.addiw`, `c.ld`, etc.) are checked unconditionally.  Since each RVC rule maps
to a distinct base-ISA mnemonic, the rules never conflict:  `c.jal` only fires
for `jal x1, offset` and `c.addiw` only fires for `addiw rd, rd, imm` — these
are different instructions.  The annotation may be slightly optimistic in edge
cases (e.g. `jal x1` annotated `[C]` on RV64 code where that encoding slot is
taken by `c.addiw`), but since `rvc_eligible` is informational only this is
acceptable.

Key rules (exhaustive list per spec; `rd'`/`rs1'`/`rs2'` means register index
in `range(8, 16)`):

| RVC encoding | Condition on the base instruction |
|---|---|
| `c.addi4spn rd', nzuimm` | `addi rd, x2, nzuimm` — rd' in 8–15, `4 ≤ imm ≤ 1020`, multiple of 4 (imm must be strictly positive; negative or zero values are not encodable) |
| `c.lw rd', imm(rs1')` | `lw rd, imm(rs1)` — both in 8–15, uimm fits 7 bits (4-byte aligned) |
| `c.sw rs2', imm(rs1')` | `sw rs2, imm(rs1)` — both in 8–15, uimm fits 7 bits (4-byte aligned) |
| `c.ld rd', imm(rs1')` | RV64: `ld rd, imm(rs1)` — both in 8–15, uimm fits 8 bits (8-byte aligned) |
| `c.sd rs2', imm(rs1')` | RV64: `sd rs2, imm(rs1)` — both in 8–15, uimm fits 8 bits (8-byte aligned) |
| `c.addi rd, nzimm` | `addi rd, rd, imm` — `rd == rs1`, rd ≠ x0, imm ≠ 0, fits 6 signed bits |
| `c.addiw rd, imm` | RV64: `addiw rd, rd, imm` — `rd == rs1`, rd ≠ x0, fits 6 signed bits (imm=0 encodes `sext.w`, which is valid) |
| `c.li rd, imm` | `addi rd, x0, imm` — rd ≠ x0, fits 6 signed bits |
| `c.addi16sp imm` | `addi x2, x2, imm` — `rd == rs1`, rd = x2, imm ≠ 0, fits 10 signed bits (16-byte aligned) |
| `c.lui rd, nzimm` | `lui rd, nzimm` — rd ≠ x0, rd ≠ x2, nzimm ≠ 0, fits 6 signed bits (×4096) |
| `c.srli rd', shamt` | `srli rd, rd, shamt` — `rd == rs1`, rd' in 8–15, 1 ≤ shamt ≤ 63 (RV32: shamt ≤ 31) |
| `c.srai rd', shamt` | `srai rd, rd, shamt` — `rd == rs1`, rd' in 8–15, 1 ≤ shamt ≤ 63 (RV32: shamt ≤ 31) |
| `c.andi rd', imm` | `andi rd, rd, imm` — `rd == rs1`, rd' in 8–15, fits 6 signed bits |
| `c.sub rd', rs2'` | `sub rd, rd, rs2` — `rd == rs1`, both in 8–15 |
| `c.xor rd', rs2'` | `xor rd, rd, rs2` — `rd == rs1`, both in 8–15 |
| `c.or  rd', rs2'` | `or rd, rd, rs2` — `rd == rs1`, both in 8–15 |
| `c.and rd', rs2'` | `and rd, rd, rs2` — `rd == rs1`, both in 8–15 |
| `c.subw rd', rs2'` | RV64: `subw rd, rd, rs2` — `rd == rs1`, both in 8–15 |
| `c.addw rd', rs2'` | RV64: `addw rd, rd, rs2` — `rd == rs1`, both in 8–15 |
| `c.j offset` | `jal x0, offset` — (offset range not checked) |
| `c.jal offset` | RV32 only: `jal x1, offset` — (offset range not checked) |
| `c.beqz rs1', offset` | `beq rs1, x0, offset` — rs1' in 8–15 (offset range not checked) |
| `c.bnez rs1', offset` | `bne rs1, x0, offset` — rs1' in 8–15 (offset range not checked) |
| `c.slli rd, shamt` | `slli rd, rd, shamt` — `rd == rs1`, rd ≠ x0, 1 ≤ shamt ≤ 63 (RV32: shamt ≤ 31) |
| `c.lwsp rd, imm` | `lw rd, imm(x2)` — rd ≠ x0, uimm fits 8 bits (4-byte aligned) |
| `c.ldsp rd, imm` | RV64: `ld rd, imm(x2)` — rd ≠ x0, uimm fits 9 bits (8-byte aligned) |
| `c.jr rs1` | `jalr x0, rs1, 0` — rs1 ≠ x0 |
| `c.mv rd, rs2` | `add rd, x0, rs2` — rd ≠ x0, rs2 ≠ x0 |
| `c.nop` | `addi x0, x0, 0` — rd = x0, imm = 0 |
| `c.ebreak` | `ebreak` |
| `c.jalr rs1` | `jalr x1, rs1, 0` — rs1 ≠ x0 |
| `c.add rd, rs2` | `add rd, rd, rs2` — `rd == rs1`, rd ≠ x0, rs2 ≠ x0 |
| `c.swsp rs2, imm` | `sw rs2, imm(x2)` — uimm fits 8 bits (4-byte aligned) |
| `c.sdsp rs2, imm` | RV64: `sd rs2, imm(x2)` — uimm fits 9 bits (8-byte aligned) |

**Float RVC encodings (`c.flw`, `c.fsw`, `c.fld`, `c.fsd`, `c.flwsp`, `c.fswsp`,
`c.fldsp`, `c.fsdsp`) are out of scope** — they require float-register range checks
that interact with the separate float-register bank, and their encoding slots
overlap with integer RVC encodings in a way that is arch-dependent (RV32D vs RV64C).
They are not included in `rvc_eligible` computation.

---

## 6. `analysis/cfg.py` — Basic blocks and functions

### Linear scan

1. Collect all labels and the instruction indices they precede.
2. Identify *barrier labels*: those that appear as branch/jump targets anywhere
   in the file, or that are globally visible (`.globl` / `.weak`).
   Assembler-internal labels that are never branch targets are **not** barrier
   labels and do not split blocks.  (See §4 two-pass parsing.)
3. Mark block-start indices: the first instruction, every instruction that
   follows a branch, jump, return, `call`, or `tail`, and every instruction
   preceded by a barrier label.
4. Group consecutive instructions between boundaries into `BasicBlock` objects.

```python
@dataclass
class BasicBlock:
    labels:             list[str]      # all labels at this address (may be empty or multiple)
    instructions:       list[Instruction]
    successors:         list[BasicBlock]
    predecessors:       list[BasicBlock]
    is_function_entry:  bool           # True when preceded by a .globl or .weak directive
```

### CFG edges

| Terminator | Successors |
|---|---|
| Conditional branch | fall-through block + branch-target block |
| `jal` (not a call, rd = x0) | one successor (the target label) |
| `jal` (rd ∉ {x0, ra, t0}) | one successor (the target label); treated as a jump saving return address in an unusual register — not a recognised call, no ABI seed |
| `jalr` (not a call, rd = x0) | no successors — treated as tail call or indirect jump (see §2) |
| `jalr` (not a call, rd ≠ x0) | no successors — register-computed target, unclassified; `terminates_function = True` in §2 |
| `ret` / `jalr x0, ra, 0` | no successors |
| `call` / any JAL or JALR writing ra (x1) or t0 (x5) | fall-through only (callee is opaque) |
| `tail` | no successors — tail call terminates function |
| `ecall` / `ebreak` | fall-through only (treated as a side-effecting call with no register ABI) |
| Fall-through (non-terminator last instruction) | next block |

### Function grouping

```python
@dataclass
class Function:
    name:    str
    entry:   BasicBlock
    blocks:  list[BasicBlock]
```

**Function boundaries are hard.**  Each function is an independent unit of
analysis.  No CFG edge, liveness value, or scheduling decision ever crosses a
function boundary.  This makes analysis idempotent per function and allows all
functions in a file to be analysed in parallel across multiple threads.

A label is a **function-entry label** if any of the following hold:
1. It has an explicit `.type <name>, @function` directive in the source.
2. It is declared `.globl` or `.weak` and is followed by instructions (not data)
   — i.e. the label appears in a `.text` (or `.text.*`) section.  Data symbols
   exported as `.globl` (in `.data`/`.bss`/`.rodata` etc.) are not function
   entries.

A new `Function` begins at each function-entry label.  Its blocks are all those
reachable from its entry block via CFG edges **within the same function**.
Function boundaries are hard stops:

- A fall-through from the last block of one function directly into a
  function-entry label is **not** a CFG edge — it is treated as an implicit
  unconditional jump to the next function's entry.  The first function terminates
  there (with no successors from that block); the second function starts fresh.
  This prevents liveness leaking across functions even when a `ret` is absent.
- Tail calls (`tail`, `jalr x0, rs1`) that jump to a label in another function
  have no successors within the calling function (they are terminating by
  definition).
- Blocks unreachable from a function's entry (dead code between two functions,
  orphan blocks after `tail`) are collected into a synthetic anonymous function
  for scheduling purposes and are not merged into any named function.

Because each function is analysed independently, `compute_liveness` and
`schedule` accept a single `Function` object.  A driver iterates over all
functions and may dispatch them to a thread pool:

```python
functions: list[Function] = identify_functions(blocks)
# Each function is independent — safe to parallelise:
results = pool.map(analyse_and_schedule, functions)
```

---

## 7. `analysis/liveness.py` — Register liveness

**Two-phase approach.**  The CFG liveness pass runs once over the blocks of a
single `Function` before per-block scheduling begins, producing a `live_out`
table keyed by block.  Because function boundaries are hard (§6), the global
pass is strictly intra-function: no liveness information crosses into or out of
other functions.  This global pass correctly handles conditional branches, loop
back-edges, and fall-throughs within the function.  Per-block scheduling then
consults this table for the block's `live_out` seed before doing its local
backward propagation.

The table is keyed by `BasicBlock` identity (object reference), not by label
string, so blocks with zero labels or multiple labels are handled uniformly.

**Liveness must be recomputed after reordering.**  Pairing rules consult
per-instruction `live_in`/`live_out`.  After the reorder pass changes instruction
order, these fields are stale.  The local backward pass is re-run on the
post-schedule ordered list before the greedy-advance pairing pass runs.

### Global pass

Standard iterative backward dataflow over the CFG of one `Function`, using a
worklist.  The worklist is seeded with all blocks in the function; no block
outside the function is ever touched.  Integer and float register sets are
maintained as separate parallel `frozenset[int]` domains (`live_in`/`live_out`
for integer, `flive_in`/`flive_out` for float) and iterated together.

```
live_in[B]  = use[B]  ∪  (live_out[B] − def[B])
live_out[B] = ⋃  live_in[S]   for S in successors(B)
```

**ABI injection.**  `call_liveness_effect()` (§2) is the single source of truth
for all ABI-related liveness effects.  It is called on every instruction when
building `use[B]` / `def[B]`:

- `implicit uses` → merged into `use[B]` (integer and float separately).
- `implicit defs` → merged into `def[B]`.
- `terminates_function = True` → the block is treated as having no successors,
  so `live_out[B]` is determined entirely by its seed (see below).

**Initial seeds.**  Before iteration begins, each block's `live_out` is
pre-loaded with the `live_out_seed` from `call_liveness_effect()` applied to
the block's last instruction (if that instruction has ABI effects).  This
accounts for registers that are conservatively live after a call or on function
exit, independent of what the rest of the function observes.  The seed values
come directly from the §2 table; there is no separate seed specification.

**Mid-block call seeds.**  A `call` instruction may appear in the middle of a
block (it has a fall-through successor, so the block continues).  Its
`live_out_seed` (RET_REGS ∪ CALLEE_SAVED / FRET_REGS ∪ FCALLEE_SAVED) is not
applied at the block level — it is applied in the **local pass** when the
backward propagation reaches the call instruction.  At that point `live_out` for
the call is unioned with its `live_out_seed` before propagating further backward.

**Function-entry seed.**  Blocks with `is_function_entry = True` permanently
union `ARG_REGS` (integer) and `FARG_REGS` (float) into their `live_in` on
every dataflow iteration, since the caller may have passed values in any of
them.  This is an additive term in the fixed-point equation, not a one-time
pre-load:

```
live_in[B] = use[B]  ∪  (live_out[B] − def[B])  ∪  ENTRY_SEED[B]

where ENTRY_SEED[B] = ARG_REGS ∪ FARG_REGS   if is_function_entry(B)
                    = ∅                         otherwise
```

Pre-loading would be overwritten on convergence; the union must be re-applied
each iteration so the seed is never lost.  `is_function_entry` is set for
blocks headed by `.globl`-declared labels.  `.weak` symbols are also treated as
function entries for this purpose, since weak aliases are commonly used as
alternate entry points with the same calling convention.

**`ret` seed conservatism.**  The `live_out_seed` for a return instruction is
`CALLEE_SAVED ∪ FCALLEE_SAVED ∪ RET_REGS ∪ FRET_REGS`.  Including the full
callee-saved sets is intentionally conservative: it ensures that any instruction
that restores a callee-saved register (e.g. `lw s0, 0(sp)`) has a dep-graph edge
to the `ret`, preventing reordering that would place the restore after the return.
It does not prevent dead callee-saved registers from being scheduled freely
earlier in the function — the scheduler does not eliminate dead writes, only
orders instructions.

### Local pass

After global fixed-point, a backward pass within each block assigns `live_in` /
`live_out` to every individual `Instruction`, using the block's global `live_out`
as the seed.  The same `call_liveness_effect()` injection is applied at the
instruction level during this pass.

---

## 8. `analysis/depgraph.py` — Dependency graph

Per basic block, build a directed acyclic graph of ordering constraints needed
for correctness.

**Edge types:**
- **RAW** (read-after-write): instruction B reads a register that A writes.
- **WAR** (write-after-read): instruction B writes a register that A reads.
- **WAW** (write-after-write): both write the same register.
- **Memory ordering**: by default, each memory operation has a dependency edge
  from the previous memory operation in program order (conservative).  With
  `--same-base-reorder`, two mem-ops with the same base register — provided that
  register is not written between them **and no unknown instruction (`is_unknown`)
  appears between them** (unknown instructions may have undeclared memory effects)
  — and non-overlapping byte ranges have this edge dropped.  Non-overlap is
  tested as disjoint half-open intervals: `[off_a, off_a+width_a)` and
  `[off_b, off_b+width_b)` are disjoint iff `off_a+width_a ≤ off_b` or
  `off_b+width_b ≤ off_a`.  Access width is inferred from the mnemonic (lb/sb=1,
  lh/sh=2, lw/sw/flw/fsw=4, ld/sd/fld/fsd=8; default 4 for unrecognised).
- **Barrier edges**: AMOs, `fence`, `fence.i`, `ecall`, `ebreak`, CSR
  instructions (`is_csr`), `call`, and `tail` instructions get ordering edges
  from all preceding instructions in the block.  Unknown instructions (`is_unknown`) are **not** treated as
  full barriers; they receive conservative memory ordering edges against all
  adjacent memory operations (see below), but other non-memory instructions
  may still be reordered around them if the register-dependency edges permit.
- **Unknown-instruction memory edges**: `build_dep_graph` checks `is_unknown`
  directly — it does not rely on `reads_memory` or `writes_memory`, which are
  only set for recognised load/store mnemonics.  When an unknown instruction
  is present, a memory ordering edge is inserted between it and **every**
  memory operation that precedes or follows it within the block, conservatively
  treating it as both a load and a store.  "Adjacent" is not the right word —
  it is all preceding and all following memory ops, not just the nearest one.

```python
@dataclass
class DepGraph:
    instructions: list[Instruction]
    # edges[i] = set of instruction indices that must come after instructions[i]
    edges: list[set[int]]

def build_dep_graph(block: BasicBlock, same_base_reorder: bool = False) -> DepGraph:
    ...
```

The list scheduler and BnB both operate on a `DepGraph` and only emit orderings
that are topological sorts of it.  Because pairs execute sequentially (A then B),
all intra-pair data dependencies are automatically satisfied by the ordering;
the dep-graph does not need to be consulted again by pairing rules.

---

## 9. `scheduler/rules.py` — Pairing rule definitions

```python
@dataclass
class PairingRule:
    name: str

    # Properties that must be True on the A-slot instruction (first of pair).
    # If any fails, this rule is skipped (not applicable — does not reject).
    a_prerequisites: list[str] = field(default_factory=list)

    # Properties that must be True on the B-slot instruction (second of pair).
    b_prerequisites: list[str] = field(default_factory=list)

    # Returns None  -> this rule does not block the pair.
    # Returns str   -> the pair is rejected; the string is the reason.
    check: Callable[[Instruction, Instruction], str | None]
```

### Slot asymmetry

The A and B slots have fundamentally different constraints and many rules apply
asymmetrically.  `a_prerequisites` and `b_prerequisites` are separate lists to
make this a first-class design consideration rather than an afterthought.

### Per-slot disqualifiers

Some instructions are ineligible for a given slot regardless of what the other
instruction is.  These are expressed as two configurable lists of instruction
property names in `rules.py`:

```python
A_SLOT_DISQUALIFIERS: list[str]  # if any is True on a, a cannot be A-slot
B_SLOT_DISQUALIFIERS: list[str]  # if any is True on b, b cannot be B-slot
```

`can_pair()` checks these lists first, before evaluating any `PairingRule`.  If
`a` is disqualified, the function returns immediately without examining `b` at
all — there is no point searching for a B-slot partner when the A-slot candidate
is already known to be ineligible.

Initial contents (subject to change as rules evolve):

```python
A_SLOT_DISQUALIFIERS = [
    "is_branch",    # branch in A-slot: the processor leaves before B executes
    "is_jump",      # same applies to unconditional jumps and returns
    "is_return",
    "is_call",      # calls belong in B-slot only (see below)
    "is_tail",      # tail-call pseudo transfers control; not a JAL/JALR so not caught by is_jump
]

B_SLOT_DISQUALIFIERS = [
    # none initially — to be populated as constraints are discovered
]
```

Calls are A-slot disqualified because placing a call in A-slot would transfer
control before the B-slot instruction executes.  In B-slot a call executes after
the A-slot instruction completes, which is the intended sequencing.

**Branches and jumps in B-slot** are explicitly permitted by the packet format:
the B-slot instruction executes after A-slot, so a branch or jump in B-slot
redirects control after A has committed — which is correct.  This is why
`is_branch`, `is_jump`, and `is_return` appear only in `A_SLOT_DISQUALIFIERS`
and not in `B_SLOT_DISQUALIFIERS`.  Pairing an ALU instruction in A-slot with a
branch in B-slot is a meaningful optimisation target.

### Rule evaluation contract

A rule is only *applicable* when every property in `a_prerequisites` is True on
`a`, and every property in `b_prerequisites` is True on `b`.  An inapplicable
rule is silently skipped — it neither approves nor rejects.  Only an applicable
rule that returns a non-`None` string constitutes a rejection.

Prerequisites are a filtering mechanism, not an approval mechanism.  The overall
`can_pair()` function uses a **blocklist model**: a pair is approved unless at
least one applicable rule explicitly rejects it.  A pair for which no rule's
prerequisites match is therefore approved by default.  This is intentional —
the rule set is expected to be complete enough for the instruction combinations
the project targets, and new rules are added when a pairing that should be
rejected slips through.

### Example rule — RSD ALU pair

As a worked example, consider pairing two instructions where both have the form
`{add, sub, and, or, xor, addw, subw} rsd, rs2` — i.e. both are two-register
ALU ops where the destination is also a source (no immediate, no load/store).

Because packets execute sequentially, register data-dependencies between A and B
are not a pairing constraint — they are already handled by the dep-graph.  The
only question a pairing rule needs to answer is whether the hardware can encode
and execute this instruction-type combination in a packet.  For this rule the
check is purely a mnemonic filter: both instructions must be from the supported
set.  No register conflict checks are needed or appropriate.

```python
PairingRule(
    name="rsd-alu-pair",
    a_prerequisites=["is_rsd"],
    b_prerequisites=["is_rsd"],
    check=lambda a, b: (
        None
        if (a.mnemonic in {"add","sub","and","or","xor","addw","subw"}
            and b.mnemonic in {"add","sub","and","or","xor","addw","subw"})
        else "rsd-alu-pair: mnemonic not in supported set"
    ),
)
```

Note that this rule is intentionally narrow.  Widening it (e.g. allowing one
side to have an immediate, or allowing load/ALU pairs) is done by adding more
rules, not by expanding this one.

### Adding / removing rules

All of `RULES`, `A_SLOT_DISQUALIFIERS`, and `B_SLOT_DISQUALIFIERS` live in
`rules.py`.  This is the only file that needs to change when iterating on
pairing policy.

---

## 10. `scheduler/pairing.py`

### `can_pair()`

```python
def can_pair(a: Instruction, b: Instruction) -> str | None:
    """Return None if a and b may share a 32-bit packet,
    or a short reason string if not."""
    # Per-slot disqualifiers: fast-out without examining the other instruction.
    for prop in A_SLOT_DISQUALIFIERS:
        if getattr(a, prop):
            return f"A-slot disqualified: {prop}"
    for prop in B_SLOT_DISQUALIFIERS:
        if getattr(b, prop):
            return f"B-slot disqualified: {prop}"

    for rule in RULES:
        if not all(getattr(a, p) for p in rule.a_prerequisites):
            continue
        if not all(getattr(b, p) for p in rule.b_prerequisites):
            continue
        result = rule.check(a, b)
        if result is not None:
            return result
    return None
```

### Greedy-advance pairing model

The fundamental pairing model is *greedy-advance*: maintain a single `free`
candidate (the last unmatched instruction).

```
free = None
for curr in instructions:               # ← loop over ordered instruction list
    if free is not None and can_pair(free, curr) is None:
        emit (free, curr) as pair packet
        free = None
    else:
        if free is not None:
            emit free as solo packet
        free = curr
if free is not None:                    # ← after loop: flush last unmatched
    emit free as solo packet
```

A solo instruction never blocks the following instruction from pairing.  The
list scheduler and BnB (§11) find the instruction ordering over the block's
dependency graph that maximises the number of pairs under this exact model.

### Rejection reason collection

The greedy-advance model always tests `can_pair(free, curr)` — `free` is A-slot
and `curr` is B-slot.  `solo_reasons` records the rejection reasons from those
exact calls:

```
for each (free, curr) pair tested during the pairing pass:
    reason = can_pair(free, curr)
    if reason is not None:
        free.solo_reasons.add(reason)   # always: free was rejected in this attempt
        if reason not from A_SLOT_DISQUALIFIERS:
            curr.solo_reasons.add(reason)  # only when the reason is not A-slot-specific
```

A-slot disqualifier reasons (e.g. `"is_call"`, `"is_tail"`) describe a property
of `free`, not of `curr`.  Adding them to `curr.solo_reasons` would falsely
imply that `curr` was itself the problem.

In `--fast` (forward-scan) mode each instruction is tested against at most one
adjacent candidate.  In list-scheduling and BnB modes the ready queue may cause
an instruction to be tested against multiple candidates, and all rejection
reasons are accumulated.  Using a set deduplicates identical reasons.

---

## 11. `scheduler/reorder.py` — Instruction reordering

Three scheduling strategies share the same interface:

```python
class ScheduleMode(enum.Enum):
    FORWARD  = "forward"   # --fast: source order, no reordering
    LIST     = "list"      # default: list scheduling with lookahead
    BNB      = "bnb"       # --thorough: branch-and-bound

def schedule(block: BasicBlock, graph: DepGraph | None, mode: ScheduleMode) -> list[Instruction]:
    """Return a topological sort of graph that maximises pair count.
    graph may be None in FORWARD mode (unused)."""
```

All three feed their output into the same greedy-advance pairing pass (§10).

---

### Mode 1 — Forward-scan (no reordering)

Instructions are emitted in source order.  The `graph` parameter is ignored; no
dependency graph needs to be built for this mode.  The greedy-advance pairing
pass runs directly over the original instruction list.  O(n).

---

### Mode 2 — List scheduling (default reordering)

Maintains a ready queue of instructions whose dependencies are all satisfied.
At each step:

1. If there is a `free` candidate (an instruction already emitted but not yet
   paired), check the ready queue for any instruction that pairs with it.
   - If exactly one candidate pairs with `free`, emit it immediately.
   - If multiple candidates pair with `free`, prefer the one with the fewest
     alternative pairing options elsewhere in the ready queue (i.e. the one that
     would be hardest to pair later).  This is the one-step lookahead that
     catches the common bad-decision case: A pairs with B, but B could have
     paired with C while A pairs with D.
2. If no pairable candidate exists for `free`, emit `free` as solo.  The
   emitted-solo instruction becomes the new `free` only if it came from the
   ready queue — in practice this step picks the next instruction from the
   ready queue by: **primary criterion** — the instruction with the **fewest**
   pairing candidates currently in the ready queue (hardest to pair later,
   chosen first so easier instructions remain available for it);
   **tie-breaker** — longest critical path to the end of the block, measured
   in dependency-graph hops, computed once before scheduling begins.  No
   instruction latency model is used — all hop edges are unit weight.  The
   picked instruction becomes the new `free`.
3. Update the ready queue as new instructions become unblocked.

Liveness is recomputed (local backward pass) after the final instruction
ordering is determined and before the greedy-advance pairing pass runs.

O(n log n) in queue operations.

---

### Mode 3 — Branch-and-bound (exhaustive)

The BnB finds the topological sort of the block's dependency graph that
maximises pair count under the greedy-advance model.  Used to establish an upper
bound on what any reordering strategy can achieve with the current rule set.

```python
WINDOW_SIZE   = 16      # blocks longer than this are split into independent windows
NODE_BUDGET   = 50_000  # total search nodes before giving up
STAGNATION    = 5_000   # nodes without improvement before giving up
```

**Upper bound:**
```python
def bound(remaining: int, prev_free: bool) -> int:
    if prev_free:
        return 1 + (remaining - 1) // 2
    else:
        return remaining // 2
```

**Candidate ordering heuristic** (when `prev_free` is True):
1. Tier 1 — ready instructions that pair with `free` as B-slot (A=free, B=cand).
2. Tier 2 — ready instructions that pair with `free` as A-slot (A=cand, B=free),
   provided there is no dep-graph edge from `free` to `cand` (i.e. `free` does
   not require `cand` to have already executed — it is safe to emit `cand` before
   `free` in the output ordering).
3. Tier 3 — remaining ready instructions in index order.

**Best-seen tracking:** BnB tracks the best solution found so far during search.
On budget exhaustion (`NODE_BUDGET` or `STAGNATION` exceeded), the best-seen
solution is returned rather than the list-scheduling result — unless no
improvement over list scheduling was found, in which case the list-scheduling
result is returned as the fallback.

**Stale liveness during BnB search:** The per-instruction `live_in`/`live_out`
fields are computed for the *source* instruction order and become stale as BnB
explores different orderings.  Pairing rules evaluated inside BnB must not
consult `live_in`/`live_out`.  They may only use register-field properties
(`uses_int`, `defs_int`, `rd`, `rs1`, etc.) which are order-independent.
Liveness-dependent rules (e.g. "rd is dead after B, so WAW is safe") are only
applied during the final greedy-advance pass, after liveness has been
recomputed for the chosen ordering.

**Known limitation — PCREL `auipc`/load pairs:** Assembler-generated
`.Lpcrel_hi*` labels associated with `auipc`/`lw` or `auipc`/`addi` PCREL
pairs are non-barrier labels and travel as `prefix_lines` on the `auipc`.  If
the scheduler separates `auipc` from its paired `lw`/`addi` (which is legal
from a dependency-graph perspective if no register edge connects them), the
`.Lpcrel_hi` label will follow the `auipc` while the `lw` remains at its
original offset — breaking the assembler-internal relocation.  To avoid this,
`auipc` instructions are conservatively treated as having an implicit data
dependency on their immediately following instruction when that instruction
references the same destination register (i.e. when `auipc rd, ...` is
immediately followed by an instruction reading `rd`), preventing separation.
This is a best-effort heuristic; a complete fix would require assembler support.

---

## 12. `output/annotator.py`

Each instruction is emitted as its original source line (verbatim `raw` field),
preceded by any `prefix_lines`, followed by a comment.  The comment format is:

```asm
    add  a0, a1, a2     # [C]  {packet 7a}
    slli t0, t0, 3      # [C]  {packet 7b}
    lw   s0, 0(sp)      # [C]  {solo: RAW hazard on t1, not RSD}
    mul  a1, a2, a3     # [~C] {solo}
    foo  a0, a1         # [?]  {solo}
```

Fields in the comment:

- `[C]` / `[~C]` — RVC eligibility.  `[?]` for unknown instructions.
- `{packet Nb}` / `{packet Na}` — paired; `a` is the first instruction of the
  packet, `b` the second.  Packet numbers are sequential per output file,
  starting from 1.
- `{solo}` — unpaired with no rejection reason recorded.
- `{solo: reason1, reason2}` — unpaired; the reasons are the deduplicated union
  of all `can_pair()` rejection strings collected for this instruction across
  all adjacent candidates examined during the pairing pass.  Reasons are sorted
  lexicographically before rendering for deterministic output.

With `--annotate-liveness`, the live-in set is also appended to the comment.

---

## 13. `__main__.py` — CLI

```
usage: python -m rv_scheduler [options] input.s

Options:
  --output FILE            Output file (default: stdout)
  --fast                   Forward-scan only, no reordering (fastest; good for
                           iterating on rules)
  --thorough               BnB reordering within windows (slowest; establishes
                           upper bound on pairs)
  --same-base-reorder      Relax memory ordering between provably-independent
                           same-base loads/stores (opt-in; no effect with --fast)
  --annotate-liveness      Include live-in register sets in comments
  -v, --verbose            Show all candidate pairs tested during scheduling,
                           not just the ones that appear in final output
                           (default: only solo_reasons on unpaired instructions);
                           verbose output goes to stderr
```

If both `--fast` and `--thorough` are given, `--thorough` takes precedence.

### Scheduling modes

All three modes use identical `can_pair()` rules.  Only the instruction ordering
fed into the greedy-advance pairing pass differs.

| Mode | Algorithm | Reorders? | Cost | Use for |
|---|---|---|---|---|
| `--fast` | Forward-scan, source order | No | O(n) | Iterating on rules |
| (default) | List scheduling + lookahead | Yes | O(n log n) | Normal use |
| `--thorough` | Branch-and-bound | Yes | Exponential (budgeted) | Measuring rule quality |

`--thorough` pair counts are an upper bound on what any reordering strategy can
achieve with the current rule set, making it useful for evaluating whether list
scheduling is leaving significant pairs on the table.

---

## Implementation Order

1. `isa/registers.py`, `isa/abi.py` — pure data tables, fully unit-testable
   immediately.
2. `isa/instruction.py` — base class and properties; test with hand-constructed
   objects.
3. `isa/decode/rv_i.py` — RV32I/RV64I decoder; test with a corpus of known
   instruction lines including unknown mnemonics to verify best-effort decode.
4. Remaining decoders (`rv_m`, `rv_a`, `rv_f`, `rv_d`, `rv_b`, `rv_zicsr`,
   `rv_v`) — add one extension at a time.
5. `isa/decode/rv_c.py` — RVC eligibility; test against a table of
   known-eligible / known-ineligible examples covering every rule in §5.
6. `analysis/cfg.py` — test with small synthetic assembly snippets covering
   straight-line, branch, loop, and call patterns; verify non-barrier label
   attachment and two-pass label classification.
7. `analysis/liveness.py` — test with known liveness examples including
   cross-call callee-saved survival and conditional-branch join points.
8. `analysis/depgraph.py` — test RAW/WAR/WAW edge construction, barrier edges
   for unknown instructions, and `--same-base-reorder` independence checks.
9. `scheduler/rules.py` + `scheduler/pairing.py` — start with one trivial rule
   to verify the A-slot branch short-circuit and asymmetric prerequisite
   evaluation before authoring real rules.
10. `scheduler/reorder.py` — test list scheduling on small hand-crafted blocks
    with known optimal pair counts; verify BnB agrees; verify liveness
    recomputation after reordering.
11. `output/annotator.py` + `__main__.py` — integration tests with complete
    assembly files; verify output line count equals input line count; verify
    `[?]` annotation appears for unknown mnemonics.
