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
│   └── decode/               # planned modular decoders (see §4 note)
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
│   ├── parser.py             # actual implementation: two-pass parsing + decode (see §4)
│   ├── cfg.py                # basic-block + function identification, CFG edges
│   ├── depgraph.py           # per-block RAW/WAR/WAW dependency graph
│   ├── liveness.py           # iterative liveness dataflow, per-instruction sets
│   └── annotator.py          # formats annotated assembly output
├── scheduler/
│   ├── pairing.py            # can_pair(); greedy_pair(); stamp_slot_eligibility()
│   ├── rules.py              # PairingRule dataclass; RULES; A/B_SLOT_DISQUALIFIERS
│   └── reorder.py            # list scheduling (default) + BnB (--thorough)
└── output/
    └── annotator.py          # planned output formatter (currently in analysis/annotator.py)
```

---

## 1. `isa/registers.py`

Map every ABI name and `x`/`f`/`v` numeric alias to a canonical integer index.
Integer registers occupy indices 0–31 (`x0`–`x31`); float registers occupy
indices 32–63 (`f0`–`f31`, equivalently `ft0`/`fs0`/`fa0` etc.); vector
registers occupy indices 64–95 (`v0`–`v31`).  A single flat namespace avoids
maintaining parallel register-file domains throughout the rest of the tool.
Vector registers are recognised so that the heuristic unknown-instruction
decoder can correctly skip them when extracting scalar dependencies.

```python
REG_ALIASES: dict[str, int]
# Integer: "zero","ra","sp","gp","tp","t0"–"t6","s0"–"s11","a0"–"a7",
#          "x0"–"x31"  -> 0–31
# Float:   "ft0"–"ft11","fs0"–"fs11","fa0"–"fa7",
#          "f0"–"f31"  -> 32–63
# Vector:  "v0"–"v31"  -> 64–95

VECTOR_REG_BASE = 64  # first vector register index

CALLER_SAVED: frozenset[int]  # ra, t0–t6, a0–a7, ft0–ft11, fa0–fa7
CALLEE_SAVED: frozenset[int]  # sp, s0–s11, fs0–fs11
ARG_REGS:     frozenset[int]  # a0–a7, fa0–fa7
RET_REGS:     frozenset[int]  # a0, a1, fa0, fa1
```

Provide helpers `reg_name(index) -> str` returning the canonical ABI name
(`"a0"` for 10, `"fa0"` for 42, `"v8"` for 72, etc.) and
`is_vector_reg(index) -> bool` returning True for indices ≥ 64.

---

## 2. `isa/abi.py`

A single function consumed by the liveness pass:

```python
def call_liveness_effect(insn: Instruction) -> tuple[
    frozenset[int],  # implicit uses  (integer indices 0–31, float indices 32–63)
    frozenset[int],  # implicit defs (clobbered)
    frozenset[int],  # live_out_seed: registers conservatively live after this insn
    bool,            # terminates_function
]:
```

A call is modelled as a single very busy instruction: it reads all argument
registers (integer and float) and `sp`, clobbers all caller-saved registers
(integer and float), and after it returns, the caller must assume all
callee-saved registers and both return registers are live.  The `live_out_seed`
captures this post-call conservative assumption separately from the
`implicit uses` (which drive dep-graph edges *into* the call, not *out* of it).

For terminating instructions (`terminates_function = True`) there are no
successors and `live_out_seed` equals `implicit uses` — what the frame reads
on exit is what must be live.  For non-terminating calls, `live_out_seed` is
the post-return live set rather than the pre-call argument set.

Detection priority: mnemonic is checked first; structural `jalr`/`jal` patterns
are the fallback for assembler output that has already expanded pseudo-instructions.

| Case | Detection | Implicit uses | Implicit defs | Live-out seed | Terminates |
|---|---|---|---|---|---|
| Call (pseudo) | mnemonic `"call"` | `ARG_REGS ∪ {sp}` | `CALLER_SAVED` | `RET_REGS ∪ CALLEE_SAVED` | False |
| Call (encoded) | `jal`/`jalr` writing `ra` (x1) or `t0` (x5) | same | same | same | False |
| Return (pseudo) | mnemonic `"ret"` | `RET_REGS ∪ CALLEE_SAVED` | ∅ | (same as uses) | True |
| Return (encoded) | `jalr x0, ra, 0` | `RET_REGS ∪ CALLEE_SAVED` | ∅ | (same as uses) | True |
| Tail call (pseudo) | mnemonic `"tail"` | `ARG_REGS ∪ {sp}` | ∅ | (same as uses) | True |
| Tail call / indirect jump (encoded) | `jalr x0, rs1` (any form not matching Return above) | same as tail call pseudo | same | same | True |

`ARG_REGS` includes both `a0–a7` (indices 10–17) and `fa0–fa7` (indices 42–49).
`CALLER_SAVED` includes both integer caller-saved and float caller-saved.
`CALLEE_SAVED` includes both integer callee-saved and float callee-saved.
`sp` (x2, index 2) is in `CALLEE_SAVED` and therefore already present in the
return and tail-call use sets; it is listed explicitly in the call row where
it would otherwise be absent.

Any `jalr x0` that is not an exact `jalr x0, ra, 0` return is treated
conservatively as a tail call — it may be a computed-goto (switch dispatch) or
a tail call; we cannot distinguish them from the assembly text alone, and the
tail-call treatment (`ARG_REGS ∪ {sp}` as uses) is the safer choice.

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

    # Non-instruction lines (blank lines, comments, assembler-internal labels
    # such as .Lpcrel_hi*, directives) that immediately precede this instruction
    # in the source are stored here so they travel with the instruction if it
    # is reordered.
    prefix_lines:  list[str] = field(default_factory=list)

    # Non-instruction lines that follow this instruction in the source but
    # precede the next instruction (or appear at end-of-file).  Used to attach
    # trailing blank lines, comments, and directives to the last instruction of
    # a block so they are emitted in the correct position in the output.
    suffix_lines:  list[str] = field(default_factory=list)

    # Populated by the decoder subclass:
    rd:   int | None              # destination register index (0–31 int, 32–63 float)
    rs1:  int | None
    rs2:  int | None
    rs3:  int | None              # FMA etc.
    imm:  int | None
    branch_target: str | None     # label string for branches / jumps
    is_unknown:    bool = False   # True for unrecognised mnemonics (best-effort decode)

    # Populated by the liveness pass (initially empty):
    live_in:   frozenset[int] = field(default_factory=frozenset)   # indices 0–63
    live_out:  frozenset[int] = field(default_factory=frozenset)

    # Populated by the pairing pass (initially empty):
    solo_reasons: set[str] = field(default_factory=set)  # rejection strings from can_pair()
```

### Computed properties

All are derived from the stored fields; none are stored separately.

| Property | Definition |
|---|---|
| `is_rsd` | `rd is not None and (rd == rs1 or rd == rs2)` — True when the destination register also appears as a source operand.  Note: RVC CA-format eligibility checks (c.sub, c.xor, c.or, c.and, c.subw, c.addw, c.srli, c.srai, c.andi) require specifically `rd == rs1`; the `rv_c.py` checks test `rd == rs1` directly rather than relying on the broader `is_rsd`.  Pairing rules that require rsd-form must additionally verify that `rd == rs2`-only cases (where `rd != rs1`) are commutative operations — non-commutative ops with `rd == rs2` cannot be re-encoded as `rd == rs1` by swapping operands. |
| `is_commutative` | mnemonic is one of `add`, `addw`, `mul`, `mulh`, `mulhu`, `mulhsu`, `and`, `or`, `xor`, `min`, `minu`, `max`, `maxu`, `fadd.s`, `fadd.d`, `fmul.s`, `fmul.d` |
| `reads_rd` | instruction reads `rd` before writing it — **not** AMO instructions; AMOs write `rd` with the old memory value but do not read `rd` |
| `writes_rd` | `rd is not None` |
| `writes_memory` | store instruction (mnemonic-based) |
| `reads_memory` | load instruction (mnemonic-based) |
| `has_mem_operand` | `reads_memory or writes_memory or _has_mem_operand` — True for any instruction that accesses memory, including unknown instructions whose operands contain a `(base-reg)` form.  Calls and other side-effecting instructions that implicitly touch memory are **not** included here; they are handled as full dep-graph barriers via `has_side_effects`. |
| `reads_stack` | load instruction whose base register is `sp` (x2) |
| `writes_stack` | store instruction whose base register is `sp` (x2) |
| `access_width` | byte width of the memory access (1/2/4/8), or `None` for non-memory instructions — inferred from the mnemonic (lb/sb=1, lh/sh=2, lw/sw/flw/fsw=4, ld/sd/fld/fsd=8) |
| `access_shift` | `access_width.bit_length() - 1` when `access_width` is not None, else `None` — the natural alignment shift; use as the `shift` argument to `imm_fits`/`uimm_fits` when checking whether an offset is naturally aligned |
| `is_branch` | conditional branch (two CFG successors) |
| `is_jump` | unconditional jump (JAL, JALR — one or zero successors) |
| `is_call` | mnemonic `"call"`, or JAL/JALR writing `ra` (x1) or `t0` (x5) — covers both the unexpanded `call` pseudo and the encoded form |
| `is_tail` | mnemonic `"tail"` — the unexpanded tail-call pseudo; not expanded, not a jump or call in the encoded sense, but transfers control and must not be in A-slot |
| `is_return` | mnemonic `"ret"`, or `jalr x0, ra, 0` (the encoded form) |
| `is_csr` | accesses a CSR |
| `is_fence` | FENCE / FENCE.I |
| `is_atomic` | A-extension instruction |
| `has_side_effects` | `is_call or is_return or writes_memory or is_csr or is_fence or is_atomic` |
| `uses_regs` | `frozenset` of registers read by this instruction (any index), **excluding x0** |
| `defs_regs` | `frozenset` of registers written by this instruction (any index), **excluding x0** |
| `rvc_eligible` | see §5 |
| `rs1_in_rvc_range` | `rs1 is not None and rs1 in range(8, 16)` |
| `rs2_in_rvc_range` | `rs2 is not None and rs2 in range(8, 16)` |
| `rd_in_rvc_range` | `rd is not None and rd in range(8, 16)` |

**x0 is the only register excluded from `uses_regs` and `defs_regs`** — writes
to x0 are no-ops and reads always produce zero, so it participates in no data
dependencies and must not appear in liveness sets.  The raw fields `rd`, `rs1`,
`rs2` may still hold index 0 for classification purposes (e.g. `is_return`
checks `rd == 0`); it is simply not emitted into the computed sets.

Vector register indices (64–95) flow through `uses_regs`/`defs_regs` and
liveness sets without special treatment.  They sit alongside scalar indices in
the same frozensets and cost nothing — no scheduling or pairing decision
consults them, and no calling-convention set includes them, so their presence
is inert.

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
    shift=1 → 2-byte aligned, shift=2 → 4-byte aligned, etc.
    Note: zero is always a multiple of any power of 2."""

def imm_fits(self, n: int, shift: int = 0, nonzero: bool | str = False) -> bool:
    """Combined signed-range + alignment check.

    nonzero=False   — [-(2**(n-1))<<shift, (2**(n-1)-1)<<shift]  (normal)
    nonzero=True    — same range with zero excluded (nzimm)
    nonzero='remap' — [-(2**(n-1))<<shift, 2**(n-1)<<shift] excluding zero
                      The all-zero bit pattern encodes 2**(n-1)<<shift,
                      balancing the range symmetrically around zero.

    Examples:
      lw/sw:       imm_fits(12)                — [-2048..2047], zero allowed
      c.addi:      imm_fits(6, nonzero=True)   — [-32..-1, 1..31], nzimm
      hypothetical: imm_fits(6, nonzero='remap') — [-32..-1, 1..32], balanced
    For unsigned variants use uimm_fits()."""

def uimm_fits(self, n: int, shift: int = 0, nonzero: bool | str = False) -> bool:
    """Combined unsigned-range + alignment + nonzero/remap check.

    For an n-bit unsigned field with the low `shift` bits implicit, the
    representable byte values are multiples of 2**shift.  There are three
    range shapes depending on how the all-zero bit-pattern is treated:

      nonzero=False   — start-inclusive  [0,      (2**n - 1) << shift]
                        Zero is a valid encoding.  Normal case.

      nonzero=True    — both-exclusive   [1<<shift, (2**n - 1) << shift]
                        Zero bit-pattern is reserved/illegal (nzuimm).
                        The slot is wasted; the top of the range is unchanged.

      nonzero='remap' — end-inclusive    [1<<shift,  2**n      << shift]
                        Zero bit-pattern is repurposed to encode the value
                        2**n << shift (one beyond the normal maximum).
                        Zero is still unrepresentable, but the top of the
                        range gains one extra step.

    Examples:
      c.lw/c.sw:    uimm_fits(7, 2)                — [0..124], 4-aligned, zero ok
      c.addi4spn:   uimm_fits(10, 2, nonzero=True)  — [4..1020], 4-aligned, nzuimm
      c.slli shamt: uimm_fits(6, nonzero=True)       — [1..63], nonzero shamt
      hypothetical: uimm_fits(6, nonzero='remap')    — [1..64], zero→64"""
```

The three range shapes, applicable to both signed and unsigned immediates:

```
                 unsigned imm_fits                   signed imm_fits
nonzero=False    [0,        (2**n−1)×step]           [−half×step, (half−1)×step]
nonzero=True     [step,     (2**n−1)×step]           [−half×step, (half−1)×step] \ {0}
nonzero='remap'  [step,      2**n   ×step]           [−half×step,  half   ×step] \ {0}
```
where `step = 2**shift`, `half = 2**(n-1)`.

For unsigned, `'remap'` repurposes the zero slot to extend the top of the range by
one step.  For signed, `'remap'` repurposes the zero slot to extend the *positive*
side by one step, making the range symmetric: both ends reach `half × step` in
magnitude.

**Access width from opcode.**  Load and store mnemonics encode the access size
in the opcode (`lb`/`sb` = 1, `lh`/`sh` = 2, `lw`/`sw` = 4, `ld`/`sd` = 8,
etc.).  An `access_width` property returns this as an integer, or `None` for
non-memory instructions.  Pairing rules that need the natural alignment shift
use `access_width.bit_length() - 1` to derive `shift` (e.g. width 4 →
`(4).bit_length() - 1` = 2).

---

## 4. `isa/decode/` — Decoders

> **Implementation note:** The modular `isa/decode/` structure described in this
> section reflects the target design.  The current implementation consolidates
> two-pass parsing and instruction decode into a single file, `analysis/parser.py`,
> rather than the per-extension module layout shown above.  All behaviours
> described in this section (two-pass label classification, best-effort decode,
> pseudo-instruction handling, etc.) are implemented there.  The `isa/decode/`
> directory does not yet exist; `analysis/parser.py` is the authoritative source.

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

### Common positional decoding

Both known and unknown instructions share a common positional decoding path
(`_generic_decode`): operands are scanned left-to-right; register-name tokens
are assigned to `rd`, `rs1`, `rs2`, `rs3` in order; a `offset(base)` token sets
`imm` and `rs1` and marks `_has_mem_operand = True`; a bare integer sets `imm`.
Instructions whose operands deviate from the default positional pattern (stores,
branches, atomics, pseudo-instructions) have explicit special-case handling
before the common path.

### Best-effort decoding of unknown instructions

When no decoder matches a mnemonic, the instruction falls through to the common
positional decoding path.  Additionally:

- `is_unknown = True` — the annotator will emit a `[?]` marker on output.
- A warning is emitted on stderr: `warning: unknown mnemonic '<mnemonic>'`
  on the first occurrence of each unknown mnemonic.

`is_unknown` is set **only** for mnemonics not found in the known-instruction
tables.  Instructions that are recognised but decoded via the common positional
path (e.g. most arithmetic instructions) are **not** marked `is_unknown`.

The instruction is **not** treated as a barrier in the dependency graph.
Memory ordering for unknown instructions follows the same rule as known
instructions: a memory ordering edge is added only if `has_mem_operand` is True
(i.e. a `offset(base)` operand was detected).  Unknown instructions with no
detectable memory operand may be freely reordered around other memory operations.

### Pseudo-instruction handling

`nop`, `mv`, `li`, `neg`, `not`, `seqz`, `snez`, `sltz`, `sgtz` and
similar single-instruction pseudo-instructions are expanded to their canonical
equivalents during parsing.  All downstream code sees only real instructions.

Branch condition pseudo-instructions (`beqz`, `bnez`, `bltz`, `bgez`, `blez`,
`bgtz`) are **not** expanded — they are retained with their original mnemonic.
`is_branch` and the CFG-edge builder recognise them directly.

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
| `c.addi4spn rd', nzuimm` | `addi rd, x2, nzuimm` — rd' in 8–15, `uimm_fits(10, 2, nonzero=True)` (encodes bits [9:2] of a 10-bit unsigned offset; zero is reserved) |
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
    label:              str | None     # primary label (used as dict key in older passes; None for unlabelled blocks)
    labels:             list[str]      # all labels at this address (may be empty or multiple)
    instructions:       list[Instruction]
    successors:         list[BasicBlock]
    predecessors:       list[BasicBlock]
    is_function_entry:  bool           # True when preceded by a .globl or .weak directive
```

> **Implementation note:** The current code retains both `label` (a single primary label, historically used as a dict key in the liveness pass) and `labels` (the full list of all labels at the same address).  The liveness pass now keys by `id(bb)` (object identity) rather than `bb.label`, so `labels` can safely contain multiple entries and `label` is no longer load-bearing for analysis.  Both fields are kept for compatibility with tests and tools that reference `bb.label` directly.

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

Because each function is analysed independently, `compute_global_liveness` and
`schedule` accept a single `Function` object.  The driver achieves parallelism
by splitting the raw source text at function boundaries before parsing, so each
worker process receives a plain string and constructs its own independent object
graph.  See §13 for details.

---

## 7. `analysis/liveness.py` — Register liveness

**Two-phase approach.**  The CFG liveness pass runs once over the blocks of a
single `Function` before per-block scheduling begins, producing a `LivenessResult`
keyed by block.  Because function boundaries are hard (§6), the global pass is
strictly intra-function: no liveness information crosses into or out of other
functions.  This global pass correctly handles conditional branches, loop
back-edges, and fall-throughs within the function.  Per-block scheduling then
consults this table for the block's `live_out` seed before doing its local
backward propagation.

```python
@dataclass
class LivenessResult:
    live_in:  dict   # id(BasicBlock) -> frozenset[int]
    live_out: dict   # id(BasicBlock) -> frozenset[int]
```

The table is keyed by `id(bb)` (object identity), not by label string, so
blocks with zero labels or multiple labels are handled uniformly.

`compute_global_liveness(blocks)` runs the global pass and returns a
`LivenessResult`.  `compute_local_liveness(block, result)` runs the local pass
for a single block, writing `live_in`/`live_out` onto each `Instruction`.

**Liveness must be recomputed after reordering.**  Pairing rules consult
per-instruction `live_in`/`live_out`.  After the reorder pass changes instruction
order, these fields are stale.  The local backward pass is re-run on the
post-schedule ordered list before the greedy-advance pairing pass runs.

### Global pass

Standard iterative backward dataflow over the CFG of one `Function`, using a
worklist.  The worklist is seeded with all blocks in the function; no block
outside the function is ever touched.  A single `frozenset[int]` domain covers
all registers (indices 0–31 integer, 32–63 float); there is no separate float
domain.

```
live_in[B]  = use[B]  ∪  (live_out[B] − def[B])  ∪  ENTRY_SEED[B]
live_out[B] = (⋃  live_in[S]  for S in successors(B))  ∪  EXIT_SEED[B]
```

**ABI injection.**  `call_liveness_effect()` (§2) is the single source of truth
for all ABI-related liveness effects.  It is called on every instruction when
building `use[B]` / `def[B]`:

- `implicit uses` → merged into `use[B]`.
- `implicit defs` → merged into `def[B]`.
- `terminates_function = True` → the block is treated as having no successors,
  so the `⋃ live_in[S]` term is ∅; `live_out[B]` is determined entirely by
  `EXIT_SEED[B]` (see below).

**Exit seeds (terminating blocks).**  For blocks whose last instruction has
`terminates_function = True` (returns, tail calls, unclassified jalr), the
`live_out_seed` from `call_liveness_effect()` is an **additive per-iteration
term** unioned into `live_out[B]` on every dataflow iteration:

```
EXIT_SEED[B] = live_out_seed from last instruction   if terminates_function
             = ∅                                      otherwise
```

This must be re-applied each iteration — not pre-loaded once — for exactly the
same reason as `ENTRY_SEED`: the dataflow equation would overwrite a one-time
pre-load to ∅ on the first pass (since `⋃ live_in[S]` = ∅ for no-successor
blocks).  The seed values come directly from the §2 table.

**Mid-block call seeds.**  A `call` instruction may appear in the middle of a
block (it has a fall-through successor, so the block continues).  Its
`live_out_seed` (`RET_REGS ∪ CALLEE_SAVED`) is not
applied at the block level — it is applied in the **local pass** when the
backward propagation reaches the call instruction.  At that point `live_out` for
the call is unioned with its `live_out_seed` before propagating further backward.

**Function-entry seed.**  `ENTRY_SEED[B]` in the equation above:

```
where ENTRY_SEED[B] = ARG_REGS ∪ CALLEE_SAVED   if is_function_entry(B)
                    = ∅                           otherwise
```

This permanently unions argument registers and callee-saved registers into
`live_in` of the entry block on every iteration.  Argument registers are live
because the caller may have passed values in any of them.  Callee-saved
registers are live because they hold the caller's values on function entry and
the callee must preserve them — without this seed, an instruction that saves a
callee-saved register early in the function would have no dep-graph path to the
return, allowing the scheduler to incorrectly move it after the return.
`is_function_entry` is set by the function-identification pass (§6) for blocks
headed by `.globl`-declared or `.type @function` labels.

**`ret` seed conservatism.**  The `live_out_seed` for a return instruction is
`CALLEE_SAVED ∪ RET_REGS`.  Including the full
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
  register is not written between them and no unknown instruction (`is_unknown`)
  appears between them — and non-overlapping byte ranges have this edge dropped.
  Non-overlap is tested as disjoint half-open intervals: `[off_a, off_a+width_a)`
  and `[off_b, off_b+width_b)` are disjoint iff `off_a+width_a ≤ off_b` or
  `off_b+width_b ≤ off_a`.  Access width is inferred from the mnemonic (lb/sb=1,
  lh/sh=2, lw/sw/flw/fsw=4, ld/sd/fld/fsd=8; default 4 for unrecognised).
  **An instruction participates in memory ordering only if `has_mem_operand` is
  True** — i.e. it is a recognised load/store, or it has an `offset(base)`
  operand syntax detected during decoding.  Unknown instructions with no
  detectable memory operand are transparent to memory ordering and may be
  freely reordered around loads and stores.
- **ABI-implied register effects**: `call_liveness_effect()` is also consulted
  per instruction in `build_dep_graph`.  Its `implicit_uses` and `implicit_defs`
  (caller-saved clobbers at call sites) generate additional RAW/WAR/WAW edges,
  ensuring that e.g. a value held in a caller-saved register has a dep-graph edge
  to any subsequent call that clobbers it.
- **Barrier edges**: AMOs, `fence`, `fence.i`, `ecall`, `ebreak`, CSR
  instructions (`is_csr`), `call`, `tail`, and the control-flow terminators
  (`is_return`, `is_branch`, `is_jump`) get ordering edges from all preceding
  instructions in the block.  Terminators are always the last instruction of
  their block (the parser flushes there), so the barrier edge simply pins them
  last and prevents independent instructions from floating past them.  Unknown
  instructions are **not** treated as full barriers; they participate only via
  their inferred register dependencies and any detected memory operand.

```python
@dataclass
class DepGraph:
    instructions: list[Instruction]
    # edges[i] = set of instruction indices that must come after instructions[i]
    edges: list[set[int]]

    @property
    def n(self) -> int:
        return len(self.instructions)

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

    # Returns None  -> this encoding accepts the pair (success).
    # Returns str   -> this encoding cannot represent the pair; the string
    #                  is the reason.  Other encodings (rules) may still accept.
    # The reason string should be prefixed with the rule name, e.g.
    # "rsd-alu-pair: mnemonic not in supported set".
    check: Callable[[Instruction, Instruction], str | None]

    # Per-slot mnemonic allowlists.  If set, a candidate is rejected for that
    # slot before prerequisites are tested.  None means "any mnemonic".
    a_mnemonic_set: Optional[frozenset] = None
    b_mnemonic_set: Optional[frozenset] = None

    # Properties that must be True on the A-slot instruction (first of pair).
    # If any fails, this rule is skipped (not applicable — does not reject).
    a_prerequisites: list[str] = field(default_factory=list)

    # Properties that must be True on the B-slot instruction (second of pair).
    b_prerequisites: list[str] = field(default_factory=list)
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

Because disqualifiers depend only on the instruction's own intrinsic properties
— never on neighbours or position — they can be evaluated once, immediately
after parsing, and stamped onto each instruction as two boolean flags:

```python
insn.a_slot_ok  # False if any A_SLOT_DISQUALIFIER property is True
insn.b_slot_ok  # False if any B_SLOT_DISQUALIFIER property is True
```

The `stamp_slot_eligibility(instructions)` function in `pairing.py` performs
this pass.  It must be called once per block after parsing, before scheduling
or pairing.  Scheduling rules and `can_pair()` read the pre-computed flags
directly rather than re-evaluating the disqualifier lists on every call.

Slot eligibility is checked by the caller before `can_pair()` is invoked.
`can_pair()` itself is a pure rule-check that assumes both instructions are
already slot-eligible.  This means disqualified instructions are short-circuited
at the scheduling layer, never reaching the encoding tests at all.

Current contents:

```python
A_SLOT_DISQUALIFIERS = [
    "is_unknown",   # side effects unknown — always disqualified from both slots
]

B_SLOT_DISQUALIFIERS = [
    "is_unknown",
]
```

This is the minimal starting point.  The disqualifier lists are the primary
mechanism for expressing slot constraints as the encoding design evolves.
Branches, calls, and returns are not currently in these lists — whether they
require slot restrictions depends on the specific packet encoding being
explored and is left to future rule iteration.  The `is_branch`, `is_call`,
etc. properties exist on `Instruction` so they can be added to either list
without any other code changes.

### What a rule represents

Each `PairingRule` represents a **hardware packet encoding**: a specific way
two instructions can share a single 32-bit word.  The packet format defines a
finite set of such encodings.  A pair of instructions can be placed in a packet
if and only if at least one encoding exists that can represent both of them.

This framing makes the evaluation model self-evident:

- **Prerequisites** describe the instruction types an encoding can hold in each
  slot.  If an instruction doesn't satisfy the prerequisites, it simply can't go
  in that slot of that encoding — the encoding doesn't apply.
- **`check`** verifies any remaining constraints for the encoding (e.g. that an
  immediate fits in the field width the encoding provides).
- **`can_pair()` returns success** only when at least one encoding's
  prerequisites are satisfied and its `check` passes.  If no encoding can
  represent the pair, it cannot be packed — there is no hardware way to do it.
- **`A_SLOT_DISQUALIFIERS` / `B_SLOT_DISQUALIFIERS`** are fast-exit checks
  evaluated before any encoding is tested.  They short-circuit the search when
  an instruction is structurally incompatible with a slot regardless of encoding
  (e.g. a branch cannot be in A-slot because the packet format always executes
  A before B — the hardware would never reach B).  These are an optimisation;
  the same conclusion would be reached by exhausting all encodings.

A rule is only *applicable* to a pair when every property in `a_prerequisites`
is True on `a` and every property in `b_prerequisites` is True on `b`.  An
inapplicable rule is silently skipped — the encoding simply doesn't cover this
instruction combination.  Only an applicable rule whose `check` returns a
non-`None` string constitutes a rejection of that specific encoding.

### Example rule — RSD ALU pair

The rule currently in `scheduler/rules.py` is a deliberately conservative
starting point, chosen to establish the mechanics of the rule framework rather
than to represent the final encoding design.  It serves as a concrete example
for testing the tooling; the supported mnemonic set and the `rsd` form
requirement are expected to be revised as the encoding space is explored.

As a worked example, consider pairing two instructions where both have the form
`{add, sub, and, or, xor, addw, subw} rsd, rs2` — i.e. both are two-register
ALU ops where the destination is also a source (no immediate, no load/store).

Because packets execute sequentially, register data-dependencies between A and B
are not a pairing constraint — they are already handled by the dep-graph.  The
only question a pairing rule needs to answer is whether the hardware can encode
and execute this instruction-type combination in a packet.  For this rule the
check is a mnemonic filter combined with a commutativity check: both instructions
must be from the supported set, and any instruction where `rd == rs2` (not `rs1`)
must be commutative — otherwise the operands cannot be swapped in the encoding to
produce the required `rd == rs1` form.

```python
SUPPORTED = {"add", "sub", "and", "or", "xor", "addw", "subw"}

PairingRule(
    name="rsd-alu-pair",
    a_prerequisites=["is_rsd"],
    b_prerequisites=["is_rsd"],
    check=_rsd_alu_pair,   # see scheduler/rules.py
)

def _rsd_alu_pair(a, b):
    for slot, insn in (("A", a), ("B", b)):
        if insn.mnemonic not in SUPPORTED:
            return f"rsd-alu-pair: {slot}-slot mnemonic not in supported set"
        if insn.rd != insn.rs1 and not insn.is_commutative:
            return f"rsd-alu-pair: {slot}-slot rd==rs2 but {insn.mnemonic} is not commutative"
    return None
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

### `stamp_slot_eligibility()` and `stamp_solo_reasons()`

Two preprocessing passes run once per block immediately after parsing:

`stamp_slot_eligibility(instructions)` precomputes `a_slot_ok` / `b_slot_ok`
on each instruction from the `A_SLOT_DISQUALIFIERS` / `B_SLOT_DISQUALIFIERS`
lists.  This is a fast O(n) pass that avoids re-evaluating the disqualifier
lists on every scheduling or pairing query.

`stamp_solo_reasons(instructions)` precomputes each instruction's intrinsic
rejection reasons — those that arise from the instruction's own properties
independent of any pairing partner.  For each rule it checks mnemonic-set
membership and per-slot prerequisites and records reasons on the instruction.
This gives `solo_reasons` meaningful content even for instructions that are
never offered a pairing candidate (e.g. the sole instruction in a block).

### `_a_eligible_rules()` and `find_b_partners()`

The inner pairing loop tests one A candidate against many B candidates.
Re-evaluating A's mnemonic-set and prerequisite checks for every B candidate
is wasteful.  Two helpers factor out the A-side work:

```python
def _a_eligible_rules(a: Instruction) -> list[PairingRule]:
    """Rules for which a passes a_mnemonic_set and a_prerequisites.
    Computed once per A; reused across all B candidates."""

def find_b_partners(a: Instruction, candidates: list[Instruction]
                    ) -> list[tuple[Instruction, PairingRule]]:
    """Return [(b, rule), ...] for each candidate that forms a valid pair
    with a.  If a is ineligible for all rules, returns [] immediately and
    no B candidate is annotated."""
```

`find_b_partners` is the primary query used by the list scheduler and BnB.
`can_pair` is a thin wrapper over the same logic for callers that want a
single yes/no answer with a reason string.

### `can_pair()`

`can_pair()` is a pure rule-check: it assumes both instructions are already
slot-eligible (`a.a_slot_ok` and `b.b_slot_ok` are both True).  Callers are
responsible for checking slot eligibility before calling it; disqualified
instructions must never be passed here.

```python
def can_pair(a: Instruction, b: Instruction) -> str | None:
    """Return None if a and b may share a 32-bit packet,
    or a short reason string if not."""
    eligible = _a_eligible_rules(a)
    if not eligible:
        return "no applicable encoding"
    reasons: list[str] = []
    for rule in eligible:
        if rule.b_mnemonic_set is not None and b.mnemonic not in rule.b_mnemonic_set:
            continue
        if not all(getattr(b, p) for p in rule.b_prerequisites):
            continue
        result = rule.check(a, b)
        if result is None:
            return None
        reasons.append(result)
    return "; ".join(reasons) if reasons else "no applicable encoding"
```

### Greedy-advance pairing model

The fundamental pairing model is *greedy-advance*: maintain a single `free`
candidate (the last unmatched instruction).

```
free = None
for curr in instructions:               # ← loop over ordered instruction list
    if free is not None and find_b_partners(free, [curr]):
        emit (free, curr) as pair packet   # ← includes matched rule name
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

Each pair packet records the matched rule name: `('pair', a, b, rule_name)`.
Solo packets are `('solo', insn)`.

### Rejection reason collection

Slot disqualification is checked before `find_b_partners()` is called.  If
`free` is A-slot disqualified, it is emitted solo immediately and the
disqualifier reason is added only to `free.solo_reasons`.  Likewise, if `curr`
is B-slot disqualified, the reason is added only to `curr.solo_reasons`.

For rule-check rejections, the reason is added to `curr.solo_reasons` only —
and only when A is actually eligible for at least one rule (i.e.
`_a_eligible_rules(free)` is non-empty).  If A is ineligible for all rules, B
gets no pair-attempt annotation because the failure is entirely A's fault; B
should not accumulate spurious reasons from a pairing attempt it had no chance
of succeeding.

Intrinsic reasons (those arising from an instruction's own properties) are
pre-populated by `stamp_solo_reasons()` before any pairing is attempted.

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
WINDOW_SIZE   = 16      # blocks longer than this are split into windows
NODE_BUDGET   = 50_000  # total search nodes before giving up
STAGNATION    = 5_000   # nodes without improvement before giving up
```

Blocks longer than `WINDOW_SIZE` are split into consecutive fixed-size windows,
each scheduled independently.  Cross-window dependency edges (edges from an
instruction in window N pointing to one in window N+1) are **not** added to the
window's sub-graph because they are trivially satisfied: window N is fully emitted
before window N+1 begins, so any instruction in window N always precedes any
instruction in window N+1 in the output regardless of intra-window reordering.
Only intra-window edges are needed in the sub-graph.

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

**Pair counting (`_pair_count`):** BnB evaluates candidate orderings by running
`greedy_pair()` on each complete ordering and counting the pairs it produces.
`greedy_pair()` has the side effect of populating `solo_reasons` on each
instruction.  To prevent BnB from polluting `solo_reasons` with reasons from
orderings that were explored but not chosen, `_pair_count()` snapshots each
instruction's `solo_reasons` before calling `greedy_pair()` and restores them
afterward.  Only the final chosen ordering's reasons are kept.

**Best-seen tracking:** BnB tracks the best solution found so far during search.
On budget exhaustion (`NODE_BUDGET` or `STAGNATION` exceeded), the best-seen
solution is returned rather than the list-scheduling result — unless no
improvement over list scheduling was found, in which case the list-scheduling
result is returned as the fallback.

**Stale liveness during BnB search:** The per-instruction `live_in`/`live_out`
fields are computed for the *source* instruction order and become stale as BnB
explores different orderings.  Pairing rules evaluated inside BnB must not
consult `live_in`/`live_out`.  They may only use register-field properties
(`uses_regs`, `defs_regs`, `rd`, `rs1`, etc.) which are order-independent.
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

Each instruction is emitted as:
1. Its `prefix_lines` (blank lines, comments, directives, non-barrier labels
   that precede it in the source), verbatim.
2. Its original source line (verbatim `raw` field), with a scheduler comment
   appended.
3. Its `suffix_lines` (trailing lines that follow this instruction and precede
   the next instruction, or appear at end-of-file), verbatim.

All non-instruction source lines — blank lines, comments (`# ...`), assembler
directives, section switches — travel as `prefix_lines` or `suffix_lines` and
are reproduced verbatim in the output.  No source line is dropped.

The comment format is:

```asm
    add  a0, a1, a2     # [C]  {packet 7a, rsd-alu-pair}
    slli t0, t0, 3      # [C]  {packet 7b, rsd-alu-pair}
    lw   s0, 0(sp)      # [C]  {solo: RAW hazard on t1, not RSD}
    mul  a1, a2, a3     # {solo}
    foo  a0, a1         # [?]  {solo}
```

Fields in the comment:

- `[C]` — RVC-eligible (present only when true).  `[?]` for unknown instructions.
- `{packet Na, rule_name}` / `{packet Nb, rule_name}` — paired; `a` is the
  first instruction of the packet, `b` the second.  Packet numbers are
  sequential per output file, starting from 1.  `rule_name` is the name of the
  `PairingRule` that accepted the pair.
- `{solo}` — unpaired with no rejection reason recorded.
- `{solo: reason1, reason2}` — unpaired; the reasons are the deduplicated union
  of all rejection strings collected for this instruction across all adjacent
  candidates examined during the pairing pass.  Reasons are sorted
  lexicographically before rendering for deterministic output.

With `--annotate-liveness`, the live-in set is also appended to the comment.

### Statistics

After each function's instructions, a comment block is emitted reporting:
- Instruction count, packet count, pair count, solo count.
- Pair rate (percentage of instructions that were paired).
- RVC-eligible rate (percentage of instructions marked `[C]`).

After all functions, a file-level totals block repeats the same metrics and
adds a per-rule hit count breakdown showing how many pairs each rule matched.

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

### Parallel processing

The driver splits the raw source text into per-function chunks at
`.globl` / `.type @function` boundaries before any parsing.  Each chunk is a
self-contained string.  A `ProcessPoolExecutor` dispatches each chunk to a
worker process that runs the full parse → stamp → liveness → schedule → pair
pipeline independently.  Workers share no state and communicate only plain
strings (in) and flat packet lists (out), so there is no pickling of complex
object graphs.

Chunks are sorted by descending size before dispatch so the heaviest functions
start first.  Results are reassembled in original source order before
annotation.

This design provides near-linear scaling for BnB mode (3× on 4 cores on a
large file) and modest gains for list-scheduling mode, where per-function work
is typically too small to overcome inter-process overhead.

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
