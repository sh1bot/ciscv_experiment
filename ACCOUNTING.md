# Accounting considerations

Measurement conventions for the corpus analyses that drive `encoding.yaml`.

Every number this project quotes — pairing rate, pack rate, codepoint demand,
op-set yield — rests on a pile of bucketing decisions. Most of them are
defensible either way, and several are currently made implicitly by whichever
tool happened to be written first. This document is where those decisions get
written down, argued, and (where possible) settled.

**Status markers.** Each item is one of:

- **SETTLED** — decided, with the rationale recorded. Change it deliberately.
- **CONVENTION** — a choice we have made and applied consistently, but which is
  arbitrary enough that it should be restated whenever a number is quoted.
- **OPEN** — genuinely undecided; the current behaviour is an accident of
  implementation, not a decision.

A number quoted without its conventions is not a measurement. Where a
convention is optimistic, say so at the point of quoting — the RVC-eligibility
ceiling is the model here (see `CLAUDE.md`).

---

## 1. Corpus composition

**OPEN — the corpus mixes two ISAs, and the split is not neutral.**

| | instructions | ISA | evidence |
|---|---:|---|---|
| `tests/godot.s` | 97,790 | RV64 | 127 `addw`, 314 `addiw`, 11,871 `ld` |
| `tests/testcase0.s` | 21,876 | RV32 | zero `addw`/`addiw`/`subw`/`ld`/`sd` |

testcase0 contributes ~18% of instruction mass with a systematically different
vocabulary. This is not a uniform dilution — it lands hardest exactly where
frames are being sized:

| op | godot (RV64) | testcase0 (RV32) | RV32 share of total |
|---|---:|---:|---:|
| `ld` | 11,871 | 0 | 0% |
| `lw` | 1,027 | 2,374 | **70%** |
| `sw` | 2,093 | 2,258 | **52%** |
| `sd` | 9,387 | 0 | 0% |

So `lw`'s apparent popularity is mostly an artefact of including a 32-bit file.
Frames whose op clusters list `ld/lw/sd/sw` — `post-inc-pair`, `mem-base-pair`,
`pre-inc-pair` — are being sized against a blend that matches no single target.

**Remeasured on the current 17-file corpus (2.57M instructions, 47.3% RV32 by
count) — the skew GREW.** Balancing the corpus by instruction count does not
balance it per mnemonic and cannot, because an RV64 build uses `ld`/`sd` for
anything pointer-sized and leaves `lw`/`sw` to its 32-bit data alone:

| op | RV32 files | RV64 files | RV32 share of total |
|---|---:|---:|---:|
| `ld` | 0 | 192,240 | 0% |
| `lw` | 212,737 | 32,968 | **87%** |
| `sw` | 165,330 | 20,810 | **89%** |
| `sd` | 0 | 122,776 | 0% |

The concern is sharper than it was, not stale. See `results/corpus/REMEASURE.md`.

**Questions to settle:**

1. Is the packet ISA targeting RV64 only, RV32 only, or both? The original
   rule documentation assumed RV64GC; the scheduler now detects XLEN per input
   and the yaml declares XLEN-switchable ops (`opsets.xlen_switchable`), but
   RV32 corpus evidence still merges into shared tables without weighting.
2. If both, should the two corpora be weighted equally rather than by
   instruction count? Right now godot outvotes testcase0 4.5:1 purely by size,
   which is a statement about file size, not about importance.
3. Should per-corpus numbers be reported alongside the pooled figure so this
   distortion is visible by default?

**Interim guideline:** when a decision hinges on a `w`-form op or on
`ld/sd`-vs-`lw/sw`, report the godot-only figure as well as the pooled one.

---

## 2. ISA-mode-ambiguous ops (`add` vs `addw`)

**OPEN — proposed guideline below.**

In RV32 there is no `addw`: `add` is the only add. In RV64, `add` is 64-bit and
`addw` is 32-bit-with-sign-extend, and the compiler's choice carries real type
information. So a 32-bit corpus's `add` instances are *ambiguous* with respect to
the RV64 vocabulary — some would be `addw` (int arithmetic) and some `add`
(pointer arithmetic) had the same source been compiled for RV64.

Affected pairs: `add`/`addw`, `sub`/`subw`, `addi`/`addiw`, `sll`/`sllw`,
`srl`/`srlw`, `sra`/`sraw`, `mul`/`mulw`, `div`/`divw`, `rem`/`remw`,
`slli`/`slliw`, `srli`/`srliw`, `srai`/`sraiw`.

**The skew argument (proposed).** Where the choice is genuinely free, attribute
the ambiguous mass to whichever codepoint is *already more popular* in the
disambiguating (RV64) corpus. Rationale: the encoding pays per codepoint kept
alive, so concentrating mass lowers the distribution's entropy and lets a
smaller op set cover the same traffic. Splitting free mass across two codepoints
is pure loss — it keeps two opcodes alive to do one job.

This is sound, and it is legitimate precisely because an RV32 implementation can
define the less-popular codepoint as an alias of the popular one: in RV32 mode
the two are the same instruction, so nothing is misencoded.

**Note it is currently a no-op for the biggest case.** godot has `add` 423 vs
`addw` 127, so "attribute to the more popular" and "leave RV32 `add` as `add`"
agree. That makes the principle cheap to adopt now, before a case arises where
it bites.

**Caveats to record if adopted:**

- It is an *optimistic* convention, in the same family as RVC-eligibility: we
  are choosing the most favourable labelling available. A future RV32 workload
  whose real distribution differs would not achieve the measured skew.
- It should be applied per *context*, not globally — the popular form may differ
  between chain and independent populations, or between A and B slots. Decide
  whether the tie-break is global or per-frame.
- Quote both figures (skew-maximised and as-is) at least once, so the size of
  the assumption is on record.

**Alternative not chosen:** distributing RV32 mass proportionally to godot's
observed ratio (77:23 for add:addw). This is more "honest" in a modelling sense
but strictly worse for the encoding, and it invents precision we do not have.

---

## 3. Pseudo-op subforms

**OPEN — three definitions, and they disagree.**

`addi` is split into five encoding categories because they behave nothing alike
— different operand shapes, very different immediate distributions:

| subform | condition | note |
|---|---|---|
| `li` | `rs1 == x0` | unary; no input register |
| `mv` | `imm == 0` | unary; no immediate |
| `addi4spn` | `rs1 == sp` | unary in effect; sp is implicit |
| `addi_rsd` | `rd == rs1` | read-modify-write; the rsd-alu template's native shape |
| `addi_other` | otherwise | two distinct registers |

The split is load-bearing: §5 below shows `li`/`mv`/`addi4spn` are 65% of
independent-pair slot occupancy and 3% of chain, and `addi_rsd` alone drives the
6-bit immediate decision.

**The disagreement.** The predicates exist in three places:

- `isa/instruction.py` — `is_li` / `is_mv` / `is_addi4spn` properties;
- `analysis/encoding_budget.py:33` `subform()`;
- `analysis/alu_pair_cooccurrence.py:76` `addi_subform()`.

The last additionally requires `rd ∈ x8..x15` for `addi4spn`, so instructions the
other two call `addi4spn` it calls `addi_rsd` or `addi_other`. Order of tests
also matters (`li` before `mv` before `addi4spn`), and is replicated by hand.

**Consequence:** two analyses of the same corpus can legitimately disagree about
op shares. Current width analyses use `encoding_budget.subform`.

**To settle:** one definition, one place. This is decision 9 in the
`feedback.md` register (pseudo-op canonicalization placement, P1–P5) and a
prerequisite for the yaml's op vocabulary standing alone — `encoding.yaml` names
`li` and `addi4spn` as opcodes but cannot define them.

---

## 4. What counts as a "pair"

**CONVENTION, partly OPEN.**

Three different populations get called "pairs", and they differ by an order of
magnitude:

1. **Adjacencies a rule accepts** — every (i, i+1) the rule would take, counted
   before scheduling. Used by `encoding_budget.py`, `encoding_verify.py`, and the
   op-set searches. An upper bound: the scheduler will not realise all of them.
2. **Scheduled pairs** — what `greedy_pair` actually emits after reordering.
   This is what the headline pairing rate reports.
3. **Encodable pairs** — scheduled pairs whose immediates also fit the declared
   field. `encoding_verify` reports 81.4%; the gap to (2) is real.

**SETTLED:** these must never be compared across categories without saying which
is which. The op-set yields in this document are all category (1), so they are
ceilings.

**OPEN — first-accepting-rule attribution.** `encoding_budget.py` and
`encoding_verify.py` both `break` at the first rule that accepts a pair, so
**rule order determines every per-frame number**. Frame priority lives in the
`RULES` list order in `rules.py` and has no yaml representation. Under
single-source-of-truth this needs to become an explicit yaml property, or the
docs must state that attribution order is significant and where it is defined.
(feedback.md §D5, decision 8.)

**OPEN — double counting.** An adjacency acceptable to three frames contributes
to one frame's tally under first-match, but to all three if a tool iterates
without breaking. Both patterns exist in the codebase. The op-set analyses here
use a single population with no rule attribution at all, which sidesteps the
issue but means their totals are not comparable to per-frame tallies.

---

## 5. Chain vs independent populations

**SETTLED (measured) — they are nearly disjoint, and should not share an op set.**

Over both corpora, ALU-eligible adjacencies:

| population | pairs | unary share (`li`/`mv`/`addi4spn`) |
|---|---:|---:|
| chain (B consumes A's result, dead after) | 1,301 | **2.9%** |
| independent (distinct dests, no cross-feed) | 13,537 | **65.4%** |

Top ops by slot occupancy:

- **chain:** `add` 12.7%, `slli` 11.6%, `or` 8.3%, `srli` 8.2%, `addi_rsd` 6.2%,
  `xor` 4.8%, `mul` 4.3% — genuinely binary ops that use both inputs.
- **independent:** `li` 27.2%, `mv` 21.1%, `addi4spn` 17.1%, `addi_rsd` 16.8% —
  overwhelmingly unary.

**Remeasured over scheduled packets in musl-rv64 + sqlite-rv64:** independent
slots are 40.9% unary (19196/46932), chain slots 12.6% (2803/22312). The
asymmetry stands at 3.2x, but both ends moved a long way from 65.4%/2.9%, and
the *conclusion* below — that one shared op set serves both badly — has since
been acted on: the carve-out gave the frames separate `alu_chain` and `rsd_alu`
anchors, so they no longer share one.

This is a structural fact, not a sampling artefact: a chain frame's whole
purpose is to feed A's result into B's input, so an op that ignores that input
wastes the link. A unary op in a chain slot leaves an encoded register field
unused. Independent pairs have no link to waste, so they can lean into unary
ops — which is also why `dual-setup-pair` exists.

**Consequence for `encoding.yaml`:** `alu-alu-chain` and `rsd-alu-pair`
currently share one anchored list (`*rsd_alu`). That list must serve two
populations with almost no overlap, so it necessarily serves both badly.
Splitting the anchors is a prerequisite for either frame being well-sized.

**Note on the boundary:** the classification above is `is_chain` vs
`is_independent` from `alu_pair_cooccurrence.py`. Adjacencies that are neither
(e.g. B partially overwrites a source A needs) fall out of both tallies. Their
count should be reported rather than silently dropped.

---

## 6. Immediate width measurement

**SETTLED** — centralized in `analysis/imm_traits.py`. Restated here because
these choices define what "fits" means:

- **Signedness is per-op-class**, not per-frame (`SIGNED`).
- **Arithmetic ops cannot encode a zero immediate** — it degenerates to `mv`/`li`
  — so the zero codepoint is reclaimed for one more magnitude (`NO_ZERO`). This
  was worth ~1.1pp of pack rate.
- **Memory offsets are width-scaled**: an `ld` offset is divided by 8 before
  width is measured, matching RVC practice.
- **An unaligned offset returns its *unscaled* width**, so it overflows and gets
  flagged rather than silently rounding.
- **Scale comes from the template coefficient** in `encoding.yaml` (`k*imm`,
  `16*imm`), not from a per-frame declaration — single source of truth.

**CONVENTION:** widths are measured as *significant bits required*, so the
reported demand is what the corpus actually needs, not what the compiler's
encoding happened to allocate.

**CHECK TOP-CONTRIBUTOR CONCENTRATION BEFORE BELIEVING ANY DISTRIBUTION.**
A single function can be most of a population. Before designing a frame around
a measured distribution, report how many sites the top contributing function
owns, per band as well as overall. If one function owns a band, that band is
not evidence.

The case that produced this rule: "store a materialised constant to memory"
looked like 1643 sites with a striking step at 12 bits (26.5% of the mass) that
seemed to justify a 12-bit immediate field. All 443 of those 12-bit sites were
in ONE function -- `KeyMappingX11::initialize()` -- storing consecutive X11
keysym literals (ranges like 1025-1036, 1038-1103, 1185-1247) into a table.
That function owned 100% of the 9-, 10- and 12-bit bands, 98% of the 11-bit
band, and 55.5% of the entire population.

Excluding it leaves 731 sites where **8 bits covers 99.7% of constants** and the
binding constraint is the store offset, not the immediate. Every field split
from imm8 upward captures the same ~56%. A frame designed on the raw numbers
would have spent 12 of 20 operand bits serving one keyboard-mapping function.

Corollary: a large, sharply-bounded cluster of *consecutive* values is a table
initialiser, not a distribution. Check for runs.

**CONVENTION — a 12-bit immediate is UNACHIEVABLE; discount it.** A full 12-bit
immediate is the whole RISC-V I-type field: an instruction carrying one is
already as wide as it can be, so half a packet cannot improve on it and no
frame should be designed around it. Where 12-bit cases dominate a statistic,
report the achievable remainder (<= 11 bits) instead of the raw total, and say
how many were discounted. Two live examples:

- `li` + sp-relative store: 539 pairs raw, but 276 need a full 12-bit constant.
  The achievable population is **263**, and 11 bits covers all of it.
- alu-alu-chain's immediate demand above the 5-bit base: 223 slots raw, **182**
  achievable.

Do NOT read the disassembler's `# symbol+offset` comments as evidence that a
constant is relocation-derived. objdump annotates any value that happens to
land within a section's address range, so it labels ordinary masks as symbol
references: `lui a5,0x1000` + `addi a5,a5,-1` computes 2^24-1 and is annotated
`.Lline_table_start1+0xfb3eaa`. In godot.s `addi ...,-1` occurs 847 times
(2.75%, less often than `-32`) and none of them are lui-fed. The corpus is
post-link, so genuine relocations are already resolved and carry no marker at
all; `imm_expr` only catches pre-link `%hi`/`%lo` syntax.

---

## 7. Codepoint cost model

**SETTLED** — but frequently miscounted, so stated explicitly:

- The unit is the **encoding token, not the mnemonic**. `addi` is five tokens
  (§3). A frame listing 16 mnemonics including `addi` costs **20 tokens**, hence
  20 × 20 = **400 codepoints**, not 256. This has bitten at least once.
- An op's slot cost is `2^ext` where `ext = max(0, width − base_field)`. With a
  5-bit base field, a 6-bit immediate costs 2 slots and a 7-bit costs 4. The
  base field is 5 bits per register column the immediate consumes (10 from
  two); there is no other widening mechanism — `g`/`h` are opcode bits.
- Frame cost is `Σ weight(a) × Σ weight(b)`, so a symmetric frame with a 16-slot
  list costs 256. EXCEPT when the frame draws one SHARED `imm` field serving
  both slots (mem-base-pair): one field, one extension, so a cluster costs
  `|a| × |b| × 2^maxext`, not the product of per-slot extensions.
- Register-form ops always cost 1.
- Total namespace is 1024 (`opcode5:funct3:g:h`).

**Guideline:** widening the *base field* charges the extra bit to every row,
including register-register rows that cannot use it. Widening an *individual op*
through the opcode list is pay-per-use. Measured: per-op widening reached 65.9%
capture vs 66.5% for a 6-bit base field at the same 256 codepoints — within
0.6pp, without the dead bit.

---

## 8. Optimism conventions

**CONVENTION — all of these make numbers look better than reality.** Quote them
when quoting the numbers.

**This section is the canonical register of every deliberate approximation the
scheduler makes.** The task is to measure what the encoding scheme *could*
achieve if a compiler and linker targeted it deliberately, so the scheduler is
allowed to relax constraints that are artifacts of the corpus build (RVC
linkage, the linked layout, the compiler's cost model) — but each relaxation
must be listed here, at the point where a rule applies one it says so in a
comment, and where the yaml declares one it uses a named key
(`accepts_pcrel_lo`, `measures_also`).

- **Branch/jump displacements are never range-checked.** Labels are unresolved
  in the corpus, so branch immediates are neither measured nor failed. The yaml
  marks this `unbounded: true` for branches; direct `j`/`jal` targets are also
  unchecked but not so marked. Several frame notes quote the label-distance
  study (10-bit fit near 100% for branches; ~15% over-range tail for direct
  `j` on sqlite) — quote it with them.
- **Targets are assumed packet-aligned (4-byte), because the corpus is not.**
  The corpus was compiled and linked with RVC, so half its function entries
  and labels sit on 2-byte boundaries. Under the packet ISA every target is
  4-byte aligned by construction, so displacement checks test only the RANGE
  and ignore the low two bits (`arg-call-pair` is the live case: its far-call
  displacements are odd×2 in the corpus, and an alignment check would have
  measured the frame at zero on the corpus it was built for). The same
  assumption defines the link value `ra = packet + 4` used by the jump frames.
- **`%pcrel_lo`/auipc-fed offsets: refused where the field is narrow,
  accepted UNMEASURED where it spans the residue.** An offset whose base
  register came straight from an `auipc` is `(target - pc_of_auipc) mod
  4096` — a fact about the layout that binary was linked for, not a
  displacement the program chose. Two facts decide what a rule may do with
  it. (1) The *magnitude* does not survive relinking, so range-checking the
  corpus value measures the old layout: a slot whose field CANNOT span the
  full 12-bit residue refuses the tainted base outright (the code is
  excluded from the numerator rather than counted as encodable). This
  includes the lo-half `addi` reaching the ALU-immediate frames
  (rsd-alu-pair, alu-alu-chain, arith-jump-pair, arg-call-pair's addi_rsd
  row, addi-store-chain, addi-store-off-chain) — their 5–7-bit fields can
  never hold "whatever lo the new layout produces". (2) A slot whose field
  DOES span the residue — declared bits + log2(scale) ≥ 12 — accepts the
  pair and skips the magnitude check entirely: any lo the new link step
  computes fits by construction. Signedness imposes nothing, because the
  toolchain biases the auipc's hi half to land the residual in whatever
  range the field has (RISC-V's own +0x800 bias, generalised). The one fact
  that DOES survive relinking is the target's *alignment* — a property of
  the object — so a scaled field's alignment requirement is still checked
  against the corpus value; the rv64 "0–7% not 8-aligned" residue is
  thereby excluded per-site rather than assumed away. Frames on the accept
  path declare `accepts_pcrel_lo` in the yaml (`load-call-chain`,
  `pre-inc-pair`'s addi rows), and `tests/test_conformance.py` pins each
  declaration to the field arithmetic that justifies it. The accepted
  pairs remain a *relaxation* in the §8 sense — the packed layout is
  assumed to exist and link — but not an unmeasured-immediate gamble: the
  field provably holds every value the claim needs.
- **`measures_also` mnemonics are billed to the frame without a codepoint.**
  Declared per-frame in the yaml and honoured by `rules_conform`. The live
  cases: `addiw` counted as the full-width `inc`/`dec`/`addi`
  (inc-branch-pair, arg-call-pair) — provable for signed counters (overflow
  is UB), optimistic for unsigned ones on rv64 (defined wrap is not
  width-equivalent); and the signed loads `lb`/`lh`/`lwu` in
  load-store-chain — pure canonicalisation, not optimism: feeding a
  same-width store, signed and unsigned loads store identical bytes.
- **Dead-temporary rewrites are counted as encodable.** When the chain
  temporary dies inside the packet, a value-changing rewrite is licensed
  structurally (the `equivalences` section's `tmp` rule): bit-test-branch-chain
  encodes `andi tmp, 2^n-1` / `-(2^n)` masks as shifts (E1/E2), and
  li-branch-chain encodes a constant on the left of an asymmetric compare by
  the K→K+1 swap (`blt K, rs` → `bge rs, K+1`). These are canonicalisations —
  the packet computes the same predicate — but the emitted asm is not the
  corpus asm.
- **RVC-eligibility is a ceiling**, not achieved compression: no offset-range
  check, no RV32/RV64 gating, float RVC out of scope. See `CLAUDE.md`.
- **Packets claim the RVC encoding quadrant** (2-bit `10` marker), so packets and
  literal RVC compete for the same space rather than composing. The RVC rate is a
  comparison baseline, not additive headroom.
- **§2's skew-maximising attribution**, if adopted, joins this list.
- **A11's compiler-cost-model artifacts** (LSR-disguised pointer bumps, RVC
  register clustering, the clang/GCC gap) are the converse: reachable value
  the corpus HIDES. They make the measured numbers pessimistic, and are
  recorded in `TODO.md` §A11 rather than here.

---

## 9. Statistical hygiene

**SETTLED:**

- Op-set selection is scored **held-out**: counts are Poisson-thinned into two
  halves, the set is chosen on half A, and yield is reported on half B. In-sample
  yield carries a winner's curse and is not quotable on its own.
- Report ±std across thinnings. Differences inside ~1 std are not differences —
  the symmetric-vs-asymmetric 16×16 comparison (97.96% vs 98.00%, ±0.30%) is a
  tie, not a win.

**OPEN:** the two corpora are thinned together, so held-out scoring measures
generalisation across *instructions*, not across *programs*. A set chosen on
godot and scored on testcase0 would be a much stronger test — and would also
surface the §1 ISA mismatch instead of averaging over it.

---

## 10. Open questions register

| # | Question | Section | Status |
|---|---|---|---|
| 1 | RV64-only, RV32-only, or both? Corpus weighting? | §1 | OPEN |
| 2 | Adopt skew-maximising attribution for `w`-form ambiguity? Global or per-context? | §2 | OPEN — guideline proposed |
| 3 | One home for the pseudo-op subform predicates | §3 | OPEN (decision 9) |
| 4 | Frame priority: explicit yaml property, or documented as `rules.py` order? | §4 | OPEN (decision 8) |
| 5 | Report the neither-chain-nor-independent residue | §5 | OPEN |
| 6 | Split the `*rsd_alu` anchor into chain and dual lists | §5 | measured; yaml change pending |
| 7 | Cross-*program* held-out scoring | §9 | OPEN |
| 8 | Do ops outside the target ISA subset (`czero.*`, `andn`, `maxu`) belong in op-set searches at all? | — | OPEN |
| 9 | Zicond (`czero.eqz`/`czero.nez`) looks worth its own frame with its own partner set, not a slot in alu-alu-chain | §6 | OPEN — to explore |
| 10 | Carve `li` + store out of store-chain: ~410 capturable sites for ~2-4 codepoints, imm8 + base + offset | §6 | measured (outlier-corrected); frame not yet written |
| 11 | Corpus is one function away from unrepresentative — `KeyMappingX11::initialize()` alone is 55.5% of constant-stores. Worth a standing per-function concentration report, or a third corpus. | §1, §6 | OPEN |

Item 8 note: `czero.eqz`/`czero.nez` appear in optimiser-chosen op sets purely
because the corpus contains them and nothing excludes them. Whether the target
ISA includes Zicond is a scope question that has never been stated — `GOALS.md`
sets no target for which extensions are in scope (already noted in `TODO.md` §15).
