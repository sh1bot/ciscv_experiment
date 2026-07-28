# Migrating the scheduler onto encoding.yaml

Goal: make `encoding.yaml` the sole source of truth and generate the pairing
logic from it, retiring the hand-written checks in `scheduler/rules.py`. A
compiler reads the YAML (and the templates) and emits the matcher.

## Already captured in encoding.yaml

- op-sets as biclique clusters (`ops`);
- per-op immediate *range*: a bare mnemonic takes the frame's base range, an
  annotated entry (e.g. `slli_6u`) requests a wider range and pays extra opcode
  codepoints for it;
- the bit layout (`rows`);
- the reserved `rd` sentinel (x0/x2);
- the **templates**, which already encode the dataflow implicitly: a shared
  `tmp` is both the chain link (A's result → B's input) and the assertion that
  the intermediate is dead (`tmp` matches only `tmp`).

## Owned by the scheduler — NOT written up in encoding.yaml

- **Commutative operand fitting.** Swapping a commutative op's two sources so it
  fits the frame's field assignment (chain via `rd==rs2`, RSD `rd==rs2`). This
  is a property of the instruction, applied at match time; it stays in the
  scheduler. (`rules.py:114, 224, 233, 281`)
- **Dual-op pair ordering** — order-insensitive tuple match, `(a,b)` or `(b,a)`
  (`_canonical_dual`, `rules.py:684`). A pair-ordering concern; scheduler-owned.
- **Liveness analysis** (`live_out`) — the pass the generated dead-checks call.
- **The instruction model** (`isa/instruction.py`) — parsing and properties
  (`is_commutative`, `is_rsd`, `is_control_transfer`, `has_mem_operand`,
  `access_width`). The YAML describes frames *over* this model.

## Deferred / abandoned

- **4-bit low-register class** (x0..x15 RSD fields). Abandoned for now — the
  provisional system completes without the constraint, so those register-class
  limits fall on the floor today. May revisit later.

## Decisions recorded

- **Immediate zero.** Arithmetic ops **cannot** encode a zero immediate — a zero
  immediate is degenerate (canonicalises to `mv`/`li`), so arith immediate
  ranges exclude zero and may reclaim that codepoint. Memory ops **can** encode
  zero (`0(rs)` is an ordinary offset). This is inferred from instruction type,
  not declared per frame.

## Roadmap (priority order)

1. **[IN PROGRESS] Inter-immediate relationships.** The arithmetic tying the two
   instructions' immediates together. The relations are *already in the
   templates* as arithmetic on a shared immediate variable — the work is to
   parse/interpret them, not to invent syntax. `analysis/imm_relations.py` now
   parses the template expressions, derives each frame's relation, and checks it
   against the corpus. Findings: **prologue 100%**, **mem-pair 100%** (once the
   check is order-insensitive), **dual-mem 0%** — the contorted frame's offset-0
   forms don't fit the `b = -a` addi-template relation and need per-form
   handling (same special-case encoding_assign required).
   - `mem-pair`: `k*imm(rbase)` and `k*imm+k(rbase)` → B offset = A offset + one
     width. (`rules.py:570`, `abs(a.imm-b.imm)==width`)
   - `prologue`: `addi sp, -16*imm` and `store rs1b, 16*imm-k(sp)` → store offset
     ties to the frame adjustment. (`rules.py:936`, `b.imm+b.access_width+a.imm==0`)
   - `dual-mem` / `pre-inc`: `addi rsda, rsda, k*imma` and `load rdb, -k*imma(rsda)`
     → memory offset = −(stride).
   Approach: define the grammar for template immediate expressions (`k`, `k*imm`,
   `k*imm+k`, `-k*imma`, `16*imm-k`, with `k` = access width) so the compiler can
   emit the corresponding cross-instruction constraint.

2. **Equivalency pairs in the YAML** (list below) + extensions/syntax. Biased
   toward rewrites that reveal a form matching an encoding this document
   describes. A first-cut `equivalences:` schema now lives in `encoding.yaml`
   (canonical/spelled forms, named immediate-class guards, the `tmp` dead-
   intermediate convention) as a starting point — nothing consumes it yet.

3. **Signedness / zero / scale completion** — per-op signedness, the
   arith⇒nonzero deduction above, and the `scale: w` width-scaling semantics.

4. **The compiler** — reads the templates for the chain/dead/order structure and
   consumes the data above to emit the matcher.

---

## Equivalencies `rules.py` currently codifies implicitly

Prepared for review; we then look at extensions and the syntax to grow them.

### Scheduler-owned (commutativity & ordering) — excluded from the YAML

- Chain via commutative `rs2`: `must_chain` accepts `a.rd==b.rs2` when B is
  commutative (`rules.py:114`).
- RSD operand swap: `rd==rs2` realised by swapping sources, legal only for a
  commutative op (`rules.py:224, 233`).
- Low-register commutative variant (`rules.py:281`).
- Dual-op order-insensitive tuple match (`_canonical_dual`, `rules.py:684`).

### Equivalency pairs → belong in encoding.yaml

- **E1** `andi rd, rs, 2ⁿ−1 ; beqz/bnez` ≡ `slli rd, rs, XLEN−n ; beqz/bnez`
  (test low n bits zero). (`rules.py:852`)
- **E2** `andi rd, rs, −(2ⁿ) ; beqz/bnez` ≡ `srli rd, rs, n ; beqz/bnez`
  (test high bits zero). (`rules.py:854`)
- **E3** `beq rd, x0 ≡ beqz rd`, `bne rd, x0 ≡ bnez rd` — zero-compare branch is
  the zero-test pseudo. (`rules.py:872`)
- **(candidate, not yet in rules.py)** single-bit `and rd, rs, 1<<n ; bnez` ≡
  `slli rd, rs, XLEN−1−n ; blt rd, zero` — the sign-test form. Today a single-bit
  `andi` encodes directly with no rewrite (`rules.py:849`); this is a proposed
  extension.

Each pair follows the `tmp`-marked convention: the diverging destination is
`tmp` (the dead intermediate the frame already gates), so no separate liveness
test is needed. Guards that are context (E1/E2/E3 require a zero-test branch)
live in the pattern itself.

### Pseudo-op canonicalization — currently scattered; placement TBD

Single-instruction aliases (ISA facts), today in the analysis pipeline, not
`rules.py`. Decision pending on whether these live in the YAML or an ISA-side
normalization table.

- **P1** `addi rd, x0, imm ≡ li rd, imm` (`encoding_budget.subform`, `rs1==0`)
- **P2** `addi rd, rs, 0 ≡ mv rd, rs` (`subform`, `imm==0`)
- **P3** `addi rd, sp, imm ≡ addi4spn` (`subform`, `rs1==2`)
- **P4** `jalr x0, ra, 0 ≡ ret` (`isa/abi.py`)
- **P5** RVC `c.*` ≡ its base form (`analysis/parser.py:_expand_compressed`)
- likely also `add rd, x0, rs ≡ mv`, `jal x0, off ≡ j off`
