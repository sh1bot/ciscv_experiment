# RISC-V Instruction Scheduler — Design Plan

## Purpose

This tool reads RISC-V assembly source and produces annotated assembly output in
which instructions have been greedily paired into 32-bit packets.  A packet
contains either one solo instruction (occupying a full 32-bit word) or two
instructions sharing a single 32-bit word.  All instruction boundaries remain
32-bit aligned.  The pairing rules are the primary experimental artefact of the
project; they are deliberately isolated so that iterating on them requires only
local edits to one file.

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
│       ├── __init__.py       # public parse_line() factory, arch inference
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
ARG_REGS:     list[int]       # a0–a7 in order
RET_REGS:     frozenset[int]  # a0, a1

FCALLER_SAVED: frozenset[int] # ft0–ft11, fa0–fa7
FCALLEE_SAVED: frozenset[int] # fs0–fs11
FARG_REGS:     list[int]      # fa0–fa7 in order
FRET_REGS:     frozenset[int] # fa0, fa1
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
    bool,            # terminates_function
]:
```

Rules (based on the `rd` operand):

| Case | Detection | Implicit uses | Implicit defs | Terminates |
|---|---|---|---|---|
| Call with return | `jalr`/`jal` writing `ra` (x1) or `t0` (x5) | `ARG_REGS ∪ FARG_REGS` | `CALLER_SAVED ∪ FCALLER_SAVED` | False |
| Return | `jalr x0, ra, 0` | `RET_REGS ∪ FRET_REGS` | ∅ | True |
| Tail call | `jalr x0, rs1` where rs1 ≠ ra | `ARG_REGS ∪ FARG_REGS` | ∅ | True |
| Indirect jump | `jalr x0, rs1` used as computed goto (within a function, no tail-call heuristic match) | ∅ | ∅ | True (conservative) |

The discriminant is `rd`: a non-`x0` link destination means call-with-return, so
callee-saved registers survive the call.  An `x0` destination means no return
from the callee's perspective — only return registers matter downstream.

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

    # Populated by the liveness pass (initially empty):
    live_in:   frozenset[int] = field(default_factory=frozenset)
    live_out:  frozenset[int] = field(default_factory=frozenset)
    flive_in:  frozenset[int] = field(default_factory=frozenset)
    flive_out: frozenset[int] = field(default_factory=frozenset)
```

### Computed properties

All are derived from the stored fields; none are stored separately.

| Property | Definition |
|---|---|
| `is_rsd` | `rd is not None and (rd == rs1 or (is_commutative and rd == rs2))` — also True when the instruction is a commutative binop with rd in the rs2 position; operands are normalised at parse time so rs1 always holds the matched source |
| `reads_rd` | instruction reads `rd` before writing (e.g. atomics with `.aq`/`.rl`) |
| `writes_rd` | `rd is not None` |
| `writes_memory` | store instruction |
| `reads_memory` | load instruction |
| `is_branch` | conditional branch (two CFG successors) |
| `is_jump` | unconditional jump (JAL, JALR — one or zero successors) |
| `is_call` | JAL/JALR writing `ra` (x1) or `t0` (x5) |
| `is_return` | `jalr x0, ra, 0` |
| `is_csr` | accesses a CSR |
| `is_fence` | FENCE / FENCE.I |
| `is_atomic` | A-extension instruction |
| `has_side_effects` | `is_call or is_return or writes_memory or is_csr or is_fence or is_atomic` |
| `uses_int` | `frozenset` of integer registers read by this instruction |
| `defs_int` | `frozenset` of integer registers written by this instruction |
| `uses_float` | `frozenset` of float registers read |
| `defs_float` | `frozenset` of float registers written |
| `rvc_eligible` | see §5 |
| `rs1_in_rvc_range` | `rs1 is not None and rs1 in range(8, 16)` |
| `rs2_in_rvc_range` | `rs2 is not None and rs2 in range(8, 16)` |
| `rd_in_rvc_range` | `rd is not None and rd in range(8, 16)` |

Helper predicates (used internally by decoders and rules):

```python
def imm_bits(self, n: int) -> bool:   # imm fits in a signed n-bit field
def uimm_bits(self, n: int) -> bool:  # imm fits in an unsigned n-bit field
```

Each decoder subclass overrides `_decode()` to populate the register and
immediate fields and sets a `fmt` tag (R / I / S / B / U / J / CR / CI / CSS /
CIW / CL / CS / CA / CB / CJ).

---

## 4. `isa/decode/` — Decoders

Each module exports:

```python
def match(mnemonic: str) -> type[Instruction] | None
```

`decode/__init__.py` calls each module's `match()` in extension-priority order
and raises `UnknownInstruction` if none matches.  It also maintains a
module-level `inferred_arch: Literal["rv32", "rv64"] | None` that is updated as
instructions are parsed:

- Any `addiw`, `slliw`, `sd`, `ld`, or other RV64-only instruction sets it to
  `"rv64"`.
- Any RV32-only encoding (e.g. `c.jal`, which reuses the `c.addiw` slot on
  RV64) sets it to `"rv32"`.
- A conflict (both seen in the same file) is a hard parse error.

**Pseudo-instruction expansion:** `nop`, `mv`, `li`, `ret`, `call`, `tail`,
`la`, `not`, `neg`, `seqz`, `snez`, `sltz`, `sgtz` and similar assembler
pseudo-instructions are expanded to their canonical equivalents during parsing.
All downstream code sees only real instructions.

**C-extension mnemonic handling:** C-extension instructions fall into two
categories:
1. *Aliases* (e.g. `c.add`, `c.addi`, `c.srli`): the compressed form implies
   `rd == rs1`.  The decoder splices in the implicit `rs1 = rd` operand and
   delegates to the corresponding base-ISA table entry.
2. *Canonical forms* (e.g. `c.mv`, `c.li`, `c.jr`, `c.jal`): have their own
   table entries and are renamed to a canonical base-ISA mnemonic after decode
   (`c.mv` → `add`, `c.jr` → `jalr`, etc.).

Both categories ultimately produce a base-ISA `Instruction` with correct
`defs_int` / `uses_int`; no compressed mnemonic survives past `parse_line`.

**Non-barrier label attachment:** During parsing, assembler-internal labels that
do not appear as branch targets (`.Lpcrel_hi*`, `.Lfunc_end*`, `.Ltmp*`, etc.)
are not stored as separate items.  They are accumulated as `prefix_lines` on the
immediately following instruction.  This keeps them positionally anchored if the
instruction is reordered (e.g. by the BnB pass).  Labels that DO appear as
branch targets always split basic blocks regardless.

---

## 5. `isa/decode/rv_c.py` — RVC eligibility

The `rvc_eligible` property is computed by testing the instruction against every
RVC encoding rule from the RISC-V C extension specification and returning `True`
if any rule matches.

**Branch and jump offset ranges are not checked.**  Label addresses are unknown
at schedule time, so the offset constraints for `c.beqz` (±256 B), `c.bnez`
(±256 B), `c.j` (±2 KB), and `c.jal` (±2 KB) are skipped.  The annotation is
therefore an optimistic estimate for branches.

Key rules (exhaustive list per spec; `rd'`/`rs1'`/`rs2'` means register index
in `range(8, 16)`):

| RVC encoding | Condition on the base instruction |
|---|---|
| `c.addi4spn rd', nzuimm` | `addi rd, x2, nzuimm` — rd' in 8–15, uimm ≠ 0, fits 10 unsigned bits |
| `c.lw rd', imm(rs1')` | `lw rd, imm(rs1)` — both in 8–15, uimm fits 7 bits (4-byte aligned) |
| `c.sw rs2', imm(rs1')` | `sw rs2, imm(rs1)` — both in 8–15, uimm fits 7 bits |
| `c.ld rd', imm(rs1')` | RV64: `ld rd, imm(rs1)` — both in 8–15, uimm fits 8 bits (8-byte aligned) |
| `c.sd rs2', imm(rs1')` | RV64: `sd rs2, imm(rs1)` — both in 8–15, uimm fits 8 bits |
| `c.addi rd, nzimm` | `addi rd, rd, imm` — `is_rsd`, rd ≠ x0, imm ≠ 0, fits 6 signed bits |
| `c.addiw rd, imm` | RV64: `addiw rd, rd, imm` — `is_rsd`, rd ≠ x0, fits 6 signed bits |
| `c.li rd, imm` | `addi rd, x0, imm` — rd ≠ x0, fits 6 signed bits |
| `c.addi16sp imm` | `addi x2, x2, imm` — `is_rsd`, rd = x2, imm ≠ 0, fits 10 signed bits (16-byte aligned) |
| `c.lui rd, nzimm` | `lui rd, nzimm` — rd ≠ x0, rd ≠ x2, fits 6 signed bits (×4096) |
| `c.srli rd', shamt` | `srli rd, rd, shamt` — `is_rsd`, rd' in 8–15, shamt ≠ 0 |
| `c.srai rd', shamt` | `srai rd, rd, shamt` — `is_rsd`, rd' in 8–15, shamt ≠ 0 |
| `c.andi rd', imm` | `andi rd, rd, imm` — `is_rsd`, rd' in 8–15, fits 6 signed bits |
| `c.sub rd', rs2'` | `sub rd, rd, rs2` — `is_rsd`, both in 8–15 |
| `c.xor rd', rs2'` | `xor rd, rd, rs2` — `is_rsd`, both in 8–15 |
| `c.or  rd', rs2'` | `or rd, rd, rs2` — `is_rsd`, both in 8–15 |
| `c.and rd', rs2'` | `and rd, rd, rs2` — `is_rsd`, both in 8–15 |
| `c.subw rd', rs2'` | RV64: `subw rd, rd, rs2` — `is_rsd`, both in 8–15 |
| `c.addw rd', rs2'` | RV64: `addw rd, rd, rs2` — `is_rsd`, both in 8–15 |
| `c.j offset` | `jal x0, offset` — (offset range not checked) |
| `c.jal offset` | RV32 only: `jal x1, offset` — (offset range not checked) |
| `c.beqz rs1', offset` | `beq rs1, x0, offset` — rs1' in 8–15 (offset range not checked) |
| `c.bnez rs1', offset` | `bne rs1, x0, offset` — rs1' in 8–15 (offset range not checked) |
| `c.slli rd, shamt` | `slli rd, rd, shamt` — `is_rsd`, rd ≠ x0, shamt ≠ 0 |
| `c.lwsp rd, imm` | `lw rd, imm(x2)` — rd ≠ x0, uimm fits 8 bits (4-byte aligned) |
| `c.ldsp rd, imm` | RV64: `ld rd, imm(x2)` — rd ≠ x0, uimm fits 9 bits (8-byte aligned) |
| `c.jr rs1` | `jalr x0, rs1, 0` — rs1 ≠ x0 |
| `c.mv rd, rs2` | `add rd, x0, rs2` — rd ≠ x0, rs2 ≠ x0 |
| `c.ebreak` | `ebreak` |
| `c.jalr rs1` | `jalr x1, rs1, 0` — rs1 ≠ x0 |
| `c.add rd, rs2` | `add rd, rd, rs2` — `is_rsd`, rd ≠ x0, rs2 ≠ x0 |
| `c.swsp rs2, imm` | `sw rs2, imm(x2)` — uimm fits 8 bits (4-byte aligned) |
| `c.sdsp rs2, imm` | RV64: `sd rs2, imm(x2)` — uimm fits 9 bits (8-byte aligned) |

RV32-only rules (`c.jal`) and RV64-only rules (`c.ld`, `c.sd`, `c.ldsp`,
`c.sdsp`, `c.addiw`, `c.subw`, `c.addw`) are gated on `inferred_arch`.

---

## 6. `analysis/cfg.py` — Basic blocks and functions

### Linear scan

1. Collect all labels and the instruction indices they precede.
2. Identify *barrier labels*: those that appear as branch/jump targets anywhere
   in the file, or that are globally visible (`.globl` / `.weak`).
   Assembler-internal labels that are never branch targets (`.Lpcrel_hi*`, etc.)
   are **not** barrier labels and do not split blocks.
3. Mark block-start indices: the first instruction, every instruction that
   follows a branch/jump/return, and every instruction preceded by a barrier
   label.
4. Group consecutive instructions between boundaries into `BasicBlock` objects.

```python
@dataclass
class BasicBlock:
    label:              str | None
    instructions:       list[Instruction]
    successors:         list[BasicBlock]
    predecessors:       list[BasicBlock]
    is_function_entry:  bool           # True when preceded by a .globl directive
```

### CFG edges

| Terminator | Successors |
|---|---|
| Conditional branch | fall-through block + branch-target block |
| `jal` / `jalr` (not a call) to a known label | one successor |
| `jalr` to unknown register | no successors (treat as function exit) |
| `ret` / `jalr x0, ra, 0` | no successors |
| `call` / `jal x1` | fall-through only (callee is opaque) |
| Fall-through (non-terminator last instruction) | next block |

### Function grouping

```python
@dataclass
class Function:
    name:    str
    entry:   BasicBlock
    blocks:  list[BasicBlock]
```

A function begins at each `.globl`-declared label.  Its blocks are all those
reachable from the entry block before the next `.globl` entry.  Blocks reachable
from multiple `.globl` entries (e.g. via tail calls) are assigned to all
reachable functions conservatively.

---

## 7. `analysis/liveness.py` — Register liveness

**Two-phase approach.**  The CFG liveness pass runs once over the whole file
before per-block scheduling begins, producing a `live_out` table keyed by block
label.  This global pass correctly handles conditional branches, loop back-edges,
and fall-throughs.  Per-block scheduling then consults this table for the block's
`live_out` seed before doing its local backward propagation.

### Global pass

Standard iterative backward dataflow over the CFG, per function, using a
worklist.

```
live_in[B]  = use[B]  ∪  (live_out[B] − def[B])
live_out[B] = ⋃  live_in[S]   for S in successors(B)
```

Integer and float register sets are maintained as parallel `frozenset[int]`
domains and updated together.

**ABI injection:** When computing `use[B]` / `def[B]`, any instruction in B for
which `call_liveness_effect()` returns non-empty sets has those sets merged in.
If `terminates_function` is True, the block has no successors for liveness
purposes regardless of the CFG edge list.

**ABI seeds for block terminals:**
- `ret` / `jalr x0, ra, 0` → `live_out = RET_REGS ∪ CALLEE_SAVED`
- `tail` / tail-call `jalr` → `live_out = ARG_REGS`
- `call` / `jal x1` → `live_out = ARG_REGS ∪ CALLEE_SAVED` (caller resumes with
  these live); `next_block live_in` seeds `CALLEE_SAVED ∪ {ra, a0, a1}`

### Local pass

After global fixed-point, a backward pass within each block assigns `live_in` /
`live_out` to every individual `Instruction`, using the block's global `live_out`
as the seed.

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
  `--same-base-reorder`, two mem-ops with the same unmodified base register and
  non-overlapping byte ranges have this edge dropped.
- **Barrier edges**: AMOs, `fence`, `fence.i`, `ecall`, `ebreak`, `call`,
  `tail`, and any unrecognised instruction get ordering edges from all preceding
  instructions in the block.

```python
@dataclass
class DepGraph:
    instructions: list[Instruction]
    # edges[i] = set of instruction indices that must come after instructions[i]
    edges: list[set[int]]

def build_dep_graph(block: BasicBlock, same_base_reorder: bool = False) -> DepGraph:
    ...
```

The scheduler and BnB both operate on a `DepGraph` and only emit orderings that
are topological sorts of it.

---

## 9. `scheduler/rules.py` — Pairing rule definitions

```python
@dataclass
class PairingRule:
    name: str

    # Names of Instruction boolean properties that must be True on BOTH
    # instructions before check() is called.  If any prerequisite fails on
    # either instruction the rule is skipped (not applicable — does not reject).
    prerequisites: list[str]

    # Returns None  -> this rule does not block the pair.
    # Returns str   -> the pair is rejected; the string is the reason.
    check: Callable[[Instruction, Instruction], str | None]
```

### Slot asymmetry

**Branches may only appear as the B-slot** (second instruction of a pair).  Any
rule check called with a branch as `a` must return a rejection string.  This
is enforced by a universal prerequisite rule that runs before all others.
Similarly, instructions with `has_side_effects` may have additional slot
restrictions imposed by individual rules.

### Rule evaluation contract

A rule with a non-empty `prerequisites` list is only *applicable* when every
named property returns `True` on **both** `a` and `b`.  An inapplicable rule is
silently skipped — it neither approves nor rejects.  Only an applicable rule
that returns a non-`None` string constitutes a rejection.

This means prerequisites are a filtering mechanism, not an approval mechanism.
The overall `can_pair()` function approves a pair only when no applicable rule
rejects it (see §10).

### Prerequisite grouping for efficiency

Rules that share the same `prerequisites` list are grouped internally.  The
property checks for a group are performed once; if any prerequisite fails, the
entire group is skipped.

### Adding / removing rules

Each rule is one `PairingRule(...)` entry in the `RULES` list in `rules.py`.
This is the only file that needs to change when iterating on pairing policy.

---

## 10. `scheduler/pairing.py`

### `can_pair()`

```python
def can_pair(a: Instruction, b: Instruction) -> str | None:
    """Return None if a and b may share a 32-bit packet,
    or a short reason string if not."""
    for rule in _grouped_rules():
        # Prerequisites evaluated once per group
        if not all(getattr(a, p) and getattr(b, p) for p in rule.prerequisites):
            continue
        result = rule.check(a, b)
        if result is not None:
            return result
    return None
```

### Greedy-advance pairing model

The fundamental pairing model is *greedy-advance*: maintain a single `free`
candidate (the last unmatched instruction).  For each instruction `curr` in
order:

```
if free is set and can_pair(free, curr) is None:
    emit (free, curr) as a pair packet; free = None
else:
    if free is set: emit free as a solo packet
    free = curr
if free is set: emit free as a solo packet
```

A solo instruction never blocks the following instruction from pairing.  The BnB
pass (§11) finds the instruction ordering over the block's dependency graph that
maximises the number of pairs under this exact model.

### Post-schedule greedy pass

After the BnB produces an ordered instruction list, the greedy-advance walk is
applied to that list to assign final packet groupings and collect rejection
reasons for solo instructions.

---

## 11. `scheduler/reorder.py` — Instruction reordering

Three scheduling strategies share the same interface:

```python
def schedule(block: BasicBlock, graph: DepGraph, mode: ScheduleMode) -> list[Instruction]:
    """Return a topological sort of graph that maximises pair count."""
```

All three feed their output into the same forward-scan pairing pass (§10).

---

### Mode 1 — Forward-scan (no reordering)

Instructions are emitted in source order.  No dependency graph is needed.
The forward-scan pairing pass runs directly over the original instruction list.
O(n).  Default when no scheduling flag is given.

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
2. If no pairable candidate exists for `free`, emit `free` as solo and pick the
   next instruction from the ready queue by secondary priority (longest
   remaining dependency chain first, to avoid creating bottlenecks).
3. Update the ready queue as new instructions become unblocked.

O(n log n) in queue operations.  Handles the practical failure mode of greedy
forward-scan (premature commitment to a pair that blocks two better pairs)
without exhaustive search.

---

### Mode 3 — Branch-and-bound (exhaustive)

The BnB finds the topological sort of the block's dependency graph that
maximises pair count under the forward-scan model.  Used to establish an upper
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
1. Tier 1 — ready instructions that pair with `free` (A=free, B=cand).
2. Tier 2 — ready instructions that pair with `free` reversed (A=cand, B=free),
   provided no dependency edge from `free` to `cand`.
3. Tier 3 — remaining ready instructions in index order.

**Fallback:** if `NODE_BUDGET` or `STAGNATION` is exceeded, the list-scheduling
result is used instead.

---

## 12. `output/annotator.py`

Each instruction is emitted as its original source line (verbatim `raw` field),
preceded by any `prefix_lines`, followed by a comment.  The comment format is:

```asm
    add  a0, a1, a2     # [C]  {packet 7a}
    slli t0, t0, 3      # [C]  {packet 7b}
    lw   s0, 0(sp)      # [C]  {solo: RAW hazard on s0}
    mul  a1, a2, a3     # [~C] {solo}
```

Fields in the comment:

- `[C]` / `[~C]` — RVC eligibility.
- `{packet Nb}` / `{packet Na}` — paired; `a` is the first instruction of the
  packet, `b` the second.
- `{solo}` — unpaired with no candidate found.
- `{solo: <reason>}` — unpaired because the best candidate was rejected; the
  reason string comes from `can_pair()`.

With `--annotate-liveness`, the live-in set is also appended to the comment.

---

## 13. `__main__.py` — CLI

```
usage: python -m rv_scheduler [options] input.s

Options:
  --arch rv32 | rv64       Override inferred architecture (error on conflict)
  --output FILE            Output file (default: stdout)
  --fast                   Forward-scan only, no reordering (fastest; good for
                           iterating on rules)
  --thorough               BnB reordering within windows (slowest; establishes
                           upper bound on pairs)
  --same-base-reorder      Relax memory ordering between provably-independent
                           same-base loads/stores (opt-in; no effect with --fast)
  --annotate-liveness      Include live-in register sets in comments
  -v, --verbose            Include rejection reasons for all attempted pairs
```

### Scheduling modes

All three modes use identical `can_pair()` rules.  Only the instruction ordering
fed into the forward-scan pairing pass differs.

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
   instruction lines.
4. Remaining decoders (`rv_m`, `rv_a`, `rv_f`, `rv_d`, `rv_b`, `rv_zicsr`,
   `rv_v`) — add one extension at a time.
5. `isa/decode/rv_c.py` — RVC eligibility; test against a table of
   known-eligible / known-ineligible examples covering every rule in §5.
6. `analysis/cfg.py` — test with small synthetic assembly snippets covering
   straight-line, branch, loop, and call patterns; verify non-barrier label
   attachment.
7. `analysis/liveness.py` — test with known liveness examples including
   cross-call callee-saved survival and conditional-branch join points.
8. `analysis/depgraph.py` — test RAW/WAR/WAW edge construction and
   `--same-base-reorder` independence checks.
9. `scheduler/rules.py` + `scheduler/pairing.py` — start with one trivial rule
   (e.g. reject if `a.is_branch`) so the iteration harness exists before real
   rules are authored.
10. `scheduler/reorder.py` — test list scheduling on small hand-crafted blocks
    with known optimal pair counts; verify BnB agrees with list scheduling on
    those same blocks.
11. `output/annotator.py` + `__main__.py` — integration tests with complete
    assembly files; verify output line count equals input line count (modulo
    comment-only changes).
