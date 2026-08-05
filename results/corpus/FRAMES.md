# Frames: the graveyard, the prior art, and the unevaluated

Three registers.  §1 is every frame (or frame-shaped idea) this project
considered and abandoned, with the reason and the commit or record that
carries the evidence.  §2 cross-references the current roster against
industry fusion/code-size proposals — most notably Qualcomm's October 2023
code-size extension deck (provisional name **Zics**), the closest prior art
to this whole project: 32-bit instructions that each replace a
two-instruction sequence, proposed as an alternative to C, with
AArch64-flavoured operations — `ldp`/`stp`-style load/store pair,
pre/post-indexed addressing, conditional ops:
<https://lists.riscv.org/g/tech-profiles/attachment/332/0/code_size_extension_rvi_20231006.pdf>.
(Qualcomm's Xqci* vendor extensions are a DIFFERENT lineage — their
microcontroller ISA — cited below only where an Xqci op independently
documents the same idiom, not as the deck's contents.)
§3 is the queue: suggested by that prior art, not yet measured here.

Citations marked **[recall]** could not be re-fetched this session (the
sandbox blocks lists.riscv.org/arxiv directly) and rest on secondary
sources or model recall; every other link was search-confirmed.

## Frame names: the 2026-08 regularisation

Names now follow one scheme: **A-then-B order**, suffix **`-chain`** if and
only if the pair passes a value through the unencoded temporary, **`-pair`**
otherwise.  No name is a substring of another (`encoding_schema.py` enforces
this, so a bulk grep or sed cannot silently corrupt one name while editing
another).

| old | new | why |
|---|---|---|
| `chain-alu-pair` | `alu-alu-chain` | chain suffix; both slots named |
| `load-chain-alu-pair` | `load-alu-chain` | chain suffix |
| `store-chain-alu-pair` | `alu-store-chain` | **was B-A order** (A is the alu) |
| `czero-select-pair` | `czero-or-chain` | names both ops; is a temp user |
| `addi-store-pair` | `addi-store-chain` | is a temp user |
| `deref-chain-load-pair` | `deref-load-chain` | chain suffix |
| `base-chain-load-pair` | `base-load-chain` | chain suffix |
| `chain-li-branch` | `li-branch-chain` | chain suffix |
| `chain-bit-test-branch` | `bit-test-branch-chain` | chain suffix |
| `li-czero-pair` | `li-czero-chain` | is a temp user |
| `index-chain-mem-pair` | `index-mem-chain` | chain suffix |
| `load-base-branch` | `load-base-branch-pair` | missing suffix |
| `load-sp-branch` | `load-sp-branch-pair` | missing suffix |
| `mem-pair` | `mem-base-pair` | says which base, symmetric with the sp form |
| `mem-pair-sp` | `mem-sp-pair` | qualifier before the suffix |
| `dual-arith2-pair` | `macro-op-pair` | it is the macro-op family |
| `dual-indep-pair` | `indep-pair` | `dual-` was a family prefix with one member left |
| `mvload-jump-pair` | `setup-jump-pair` | `mvload` was a portmanteau, not an op |
| `post-inc-addi-pair` (rule) | `post-inc-pair` | rule name now matches its frame |
| `indep-pair` | `dual-setup-pair` | named a constraint, not an operation |

Unchanged, already regular: `inc-branch-pair`, `pre-inc-pair`,
`post-inc-pair`, `rsd-alu-pair`, `prologue-pair`, `epilogue-pair`,
`arith-jump-pair`.  `pre-inc`/`post-inc` keep their addressing-mode names
rather than becoming `inc-mem`/`mem-inc`: they are standard terms, and `inc`
already means the +/-1 opcode in `inc-branch-pair`.

Names in `results/pairing_comparison.md` and `results/main_vs_ours_gap.md`
are deliberately NOT updated — they record a comparison against another
branch as it stood.

## 1. Considered and abandoned

| frame / idea | fate | why | evidence |
|---|---|---|---|
| `arith-mem-pair` (independent RSD-ALU + memory op) | **killed** | worst pairs-per-codepoint in the roster once immediates were honest (−468 pairs for −64 codepoints, portfolio-floor money better spent elsewhere); its real ceiling needed a B-offset column the rows could not draw | commit 6dc45c0; IMMEDIATES.md §structural |
| `addi-branch-pair` (addi imm6 + compare-branch) | **replaced** by `inc-branch-pair` | the rule scheduled ONLY phantoms: 438/438 B slots compared against a register, and the row drew no rs2 field; the true vs-zero population was ~45/corpus | commit 81d6f68 (A10); 11ed4c0 |
| step ±2/4/8 widening of inc-branch | **rejected** | +8–9 points of site coverage for a 4× block (~1.3 pairs/cp); the pow2-step population is LSR's pointer bumps — migrated by cost-model tuning (measured: `-mllvm -disable-lsr` doubles unit-step capture at 0.26% *smaller* code), not encoded | TODO A11 |
| `incw`/`decw` forms | **rejected** | signed counters are provably width-equivalent (overflow is UB); the unprovable unsigned residue forgoes the pairing, never correctness; halves the block | TODO A11; inc-branch yaml note |
| `dual-mem-pair` / `dual-mem-shadd-pair` families | **retired / cut** | post-inc semantics subsumed the useful half; the shadd variant scheduled zero pairs on every corpus under both compilers for 8 codepoints | commits 58dd0cc, 25ab322 |
| `post-inc` shXadd clusters (register-stride writeback) | **cut** | real idiom, but neither clang nor GCC ever emits it adjacent to the access — zero scheduled pairs everywhere | post-inc-pair yaml note |
| `macro-op-pair` mv/li members | **dropped from that frame** | outgrew the slot (immediate demand did not fit the paired-ALU rows); the population went to `dual-setup-pair` | session record; macro-op note |
| `const-store-pair` (li + store) | **generalised away** | subsumed by `addi-store-chain`: `addi tmp, rs1a, imma` covers li (rs1a=x0), mv (imma=0), addi4spn (rs1a=sp) as register choices, not opcodes | commit a8c64ef |
| `[czero, czero]` dual-select | **refuted as a frame** | exists at scale (~1464 pooled) but both temporaries stay live into the following `or` — five live register fields against 20 bits; serve the 3-insn select by pairing the second czero with the `or` instead (`czero-or-chain`) | FINDINGS.md §4 |
| `li-czero` addi/rsd form, and a surviving-constant 5-bit variant | **not built** | the addi addends are wide (3–16% fit 6 bits) and the surviving-constant minority (16–25%) mostly crosses a branch; two populations, no shared width | li-czero yaml note |
| `addi` in `index-mem-chain` | **deliberately absent** | with the sum dead, `addi t,b,k ; ld d,0(t)` folds into `ld d,k(b)` — 95–100% foldable; the surviving-sum case is `pre-inc-pair`'s | index-chain yaml note |
| pre-inc two-field rows (bump + offset) | **redesigned** | at genuine non-prologue sites the access offset is 0 at 68–78%; the offset column re-spent on a 10-bit scaled bump | pre-inc yaml note; this session |
| `near-jump` immc layout (mvload direct-j with retained load offset) | **withdrawn** | unfunded — the layout was invisible to codepoint pricing (156 demanded vs 20 paid); the honesty pass killed it | commit ee8874a era; mvload yaml note |
| g/h bits as immediate capacity (8 frames) | **purged** | violated the one widening mechanism (opcode duplication); g/h are opcode bits | commit ee8874a |
| SP-relative rows in load-chain/store-chain/addi-store-chain | **dropped** | not earning their width once row discrimination was priced (A9); sp traffic went to dedicated frames (`mem-sp-pair`) or the base rows | commit 363f8a2 |
| `mem-base-pair` lb/lh/lwu ops | **cut** | 12 of 37,816 scheduled slots across two corpora | commit 900f8a1; mem-base-pair note |
| `lwu` in load-alu-chain | **cut** | negligible population; freed the namespace reservation | commit 0b3fcde |
| `load-alu-chain` + `load-sp-branch-pair` sized for godot | **downsized ambition** | the "C++ character" defence failed: a second C++ corpus (leveldb+protobuf) uses load-alu-chain *less* than any other corpus; godot is an outlier binary | commit 59a0109 |
| `setup-call-pair` (mv/li/addi4spn + `call`) | **parked on a policy decision** | largest idiom in the corpus (~4× anything else, ~14k est. pairs, 4 codepoints) but its whole yield rests on extending the jump-displacement optimism to calls; `ra = packet+4` works, the target has nowhere to live in the packet | FINDINGS.md §4 candidate 1 + caveat |
| `auipc+jalr` call frame | **rejected** | godot/testcase0-only spelling (non-PIC), needs a 20-bit immediate: artifact *and* unencodable | FINDINGS.md §4 |
| auipc-fed anything | **excluded by policy** | `%pcrel_lo`/auipc-fed loads never pair (relocation policy) | A5; rules.py |
| float RVC counterparts | **out of scope** | PLAN §5 | CLAUDE.md |
| BnB scheduler root-prune gate | **reverted** (not a frame, recorded for completeness) | the root bound already skips solved windows; gate bought nothing | session record |

## 2. The current roster against the prior art

Every surviving frame has at least one independent industry endorsement:

| frame here | prior art | reference |
|---|---|---|
| `mem-base-pair`, `mem-sp-pair` | Qualcomm Zics `ldp`/`stp`-style load/store pair (Oct-2023 deck, AArch64 LDP/STP analogue); NXP Zilsp / ratified-track Zilsd register-pair load/store; T-Head XTheadMemPair (`th.lwd/ldd/swd/sdd` **[recall]**); GCC aligned store-pair fusion | deck above; <https://github.com/NXP/riscv-zilsp>; <https://github.com/riscvarchive/riscv-zilsd/blob/main/zilsd.adoc>; <https://llvm.org/docs/RISCVUsage.html> |
| `pre-inc-pair`, `post-inc-pair` | Qualcomm Zics pre/post-indexed (writeback) addressing; T-Head XTheadMemIdx | deck above; <https://llvm.org/docs/RISCVUsage.html> |
| `index-mem-chain` (incl. scale-1 `add`) | Zba shNadd rationale ("extremely common… pointer arithmetic"); Celio 2016 fused indexed load (`add`+`ld`); GCC `RISCV_FUSE_LDINDEXED`; Qualcomm Xqcisls scaled-index load/store | <https://github.com/riscv/riscv-bitmanip/blob/main/bitmanip/zba.adoc>; <https://arxiv.org/abs/1607.02318>; <https://gcc.gnu.org/pipermail/gcc-patches/2022-November/605961.html>; <https://github.com/llvm/llvm-project/pull/117987> |
| `li-branch-chain` | VRoom! fused compare-immediate-and-branch (`li` + bXX, exactly this frame) | <https://moonbaseotago.github.io/2023/03/05/instruction-fusion.html> |
| `inc-branch-pair` | same family (fused compare-and-branch); no direct twin found — the direction×mode joint enumeration appears novel | ibid. |
| `macro-op-pair` | ISA manual M-extension: `MULH*; MUL` and `DIV; REM` same-operand sequences named so "microarchitectures can then fuse these into a single operation" — the frame's premise verbatim | <https://github.com/riscv/riscv-isa-manual/blob/main/src/m-st-ext.adoc> |
| `czero-or-chain`, `li-czero-chain` | Qualcomm Zics conditional move/select; the same idioms documented precisely in the separate Xqci vendor line (Xqcicm/Xqcics/Xqcicli); SiFive short-forward-branch predication | deck above; <https://github.com/llvm/llvm-project/pull/121752>, /119504, /121292; <https://reviews.llvm.org/D135814> |
| `prologue-pair`, `epilogue-pair` | ratified Zcmp `cm.push`/`cm.popret` (10–15% on stack-heavy code); Qualcomm push/pop & frame-pointer proposal | <https://github.com/riscvarchive/riscv-code-size-reduction/blob/main/Zc-specification/Zc.adoc>; <https://lists.riscv.org/g/tech-unprivileged/attachment/812/0/Qualcomm%20RISC-V%20Push&Pop&FP%20Proposal.pdf> |
| `dual-setup-pair` (mv+mv…) | Zcmp `cm.mva01s`/`cm.mvsa01` paired moves — ours is register-agnostic | Zc.adoc above |
| `arith-jump-pair`, `setup-jump-pair` | argument-setup-before-transfer; SiFive auipc/jalr CallImm patent family (US10996952B2) is the call-side cousin | <https://patents.google.com/patent/US10996952B2/en> |

## 3. Suggested by the prior art, NOT yet evaluated here

Each entry: the idiom, who endorses it, and what a measurement would look
like on this workbench.

1. **Predicated-ALU packet (short-forward-branch fusion) — MEASURED and
   REJECTED; two better shapes surfaced.**  SiFive ships SFB fusion on the
   7-series (<https://reviews.llvm.org/D135814>); as a packet the branch
   would be a condition field, not a transfer, needing no displacement (the
   skip is structurally one instruction).  Census of conditional branches
   skipping exactly one instruction, six corpora:

   | corpus | over-1 | alu | store | jump | load | other |
   |---|--:|--:|--:|--:|--:|--:|
   | musl-rv32 (clang, zicond) | 342 | 12 | 160 | 60 | 6 | 104 |
   | musl-gcc-rv32 (no zicond) | 290 | 86 | 45 | 37 | 8 | 114 |
   | musl-gcc-rv64 (no zicond) | 289 | 104 | 46 | 32 | 10 | 97 |
   | sqlite-rv64 (clang, zicond) | 1130 | 15 | 277 | 637 | 82 | 119 |
   | sqlite-rv32 (clang, zicond) | 1139 | 16 | 269 | 656 | 81 | 117 |
   | cpp-rv64 (clang, zicond) | 952 | 41 | 123 | 507 | 9 | 272 |

   The ALU column — the frame's whole premise — is 12–41 on every clang
   corpus: **zicond already predicated the profitable cases** (czero counts
   1281–1530 there), and our `czero-or-chain`/`li-czero` frames already
   package the result.  GCC's 86–104 survivors are li/mv/addi — exactly the
   czero shapes — so they are the fourth "corpus is shaped by its compiler"
   instance (GCC without zicond in `-march`), not frame demand.  The
   predication-vs-prediction debate is moot at this layer anyway: a packet
   is an encoding, not a microarchitecture — an OoO core may crack it back
   into a predicted branch exactly as SiFive's fusion does the reverse.

   What the census DID surface, both unreachable by zicond:
   * **Conditional store** (`bXX ; store`, the skip): 44–277 per corpus.
     zicond cannot predicate a store.  Fit: branch condition rs1+rs2 is 10
     bits, store src+base is 10 — offset must be 0, which holds 82% on musl
     but only 5% on sqlite (whose cond-stores carry small struct offsets).
     A zero-compare condition (beqz form) frees 5 bits for offset.  Marginal
     on today's numbers; re-measure if the offset column can be funded.
   * **Inverted-condition far jump** (`bXX ; j L`, branch over exactly one
     unconditional jump — the if/else diamond head): 24–656 per corpus,
     heavy in sqlite/cpp, and REFUTED on measurement.  The packing is
     pretty (rs1+rs2 10 bits + 10-bit packet displacement = 20 exactly) but
     the population is far-by-construction: the compiler emits this diamond
     precisely when the conditional target exceeds B-type range (+/-4KiB),
     so a 10-bit packet field — SMALLER than the solo branch's own 12-bit
     displacement — reaches 0% of resolved targets (609 sqlite / 29 musl
     diamonds; even 12 bits reaches 22%/59%).  A cautionary twin of the
     RVC-eligibility caveat: an adjacency census means nothing for a
     displacement idiom until the displacements are resolved.
   * Curiosity: **conditional return** (`bXX ; ret`): 11–88 per corpus,
     10 bits + implicit ret — nearly free to encode if a home exists.
2. **`lui+addi` 32-bit constant (LI32).**  Celio 2016; SiFive patent
   US10996952B2; GCC `RISCV_FUSE_LUI_ADDI`; VRoom
   (<https://arxiv.org/abs/1607.02318>,
   <https://moonbaseotago.github.io/2023/03/07/instruction-fusion-2.html>).
   This is TODO A1.5's "lui-split" option with three independent
   endorsements.  Blocker is fundamental: 32 bits of immediate cannot fit a
   20-bit operand budget — the honest frame is `lui+addi` where the PAIR is
   recognised but the immediate is capped (~15 bits), or a chain form
   `lui tmp; addi rd, tmp, low` with tmp dead.  Measure the census of
   lui+addi adjacencies by combined-constant width before drawing anything.
3. **`lui+load` / absolute-addressed access.**  Celio; GCC `RISCV_FUSE_LUI_LD`;
   VRoom.  Same width problem as #2, same measurement.
4. **`auipc+addi` PC-relative address.**  GCC `RISCV_FUSE_AUIPC_ADDI`.
   Currently excluded by the relocation policy (auipc never pairs);
   revisiting means deciding what a packet's auipc means when the pair moves
   the pc anchor.  Note before spending time: Celio measured auipc idioms as
   "executed incredibly rarely" under their compiler options, and our own
   auipc+jalr census matched (§1).
5. **Multiply-accumulate (`qc.muladdi` / mul+add).**  Qualcomm Xqciac
   (<https://github.com/llvm/llvm-project/pull/121037>).  We have never
   censused mul→add chains.  Measure: chain adjacency mul/mulw → add/addw
   with dead intermediate.
6. **Zero-extend idioms (`slli 32; srli 32`, zext+scale).**  Celio; GCC
   `RISCV_FUSE_ZEXTW/ZEXTH/ZEXTWS`.  Likely already captured generically by
   `alu-alu-chain` (slli→srli chains); worth one census to confirm the
   capture rate rather than a new frame — and Zba/Zbb corpora already have
   `add.uw`/`zext.h`.
7. **Table-jump (`cm.jt`/`cm.jalt`).**  Zcmt, ratified
   (<https://docs.riscv.org/reference/isa/v20240411/unpriv/zc.html>).  Out
   of pair scope (needs a jump-vector-table CSR), recorded so nobody
   re-derives that conclusion.
8. **Load/store-multiple (Xqcilsm; Zcmp push beyond 2 registers).**  A
   packet holds exactly two ops, so multi-register forms are reachable only
   as *chains of packets*; the phase-pairing idea (pair long store runs at
   the right parity) is the local answer.  Recorded as out of scope.
10. **Same-source two-result pairs — SET ASIDE from macro-op-pair, not yet
   measured.**  Two instructions reading the SAME arguments and wanting both
   results.  This is a weaker claim than `macro-op-pair`'s: there, one unit
   pass already forms both results and the second instruction only recovers
   what was being discarded (the multiplier's high half, the divider's
   remainder).  Here the two results are two computations that happen to
   share operands, so fusing them is an ALU-width argument, not a
   don't-throw-it-away one.  Cut from `macro-op-pair` for that reason.

   The shapes set aside, all `op rda, rs1, rs2 ; op rdb, rs1, rs2`:

   | pair | what it computes | note |
   |------|------------------|------|
   | `add` / `sub` | sum and difference | one adder, two output muxes |
   | `addw` / `subw` | same, RV64 word forms | |
   | `min` / `max` | both order statistics | one comparison decides both |
   | `minu` / `maxu` | unsigned forms | |

   **Add-with-carry was set aside here and then BUILT into `macro-op-pair`
   instead** (2026-08-04) — see that frame.  It never fitted this shape: the
   `sltu` reads the sum, so it is a dependence, not two reads of one argument
   list.  It fitted macro-op-pair's ROW, which is what mattered.

   **First census (2026-08-04, textual, nine corpora, both halves
   register-register and dests distinct).**  Adjacent pairs reading the same
   two registers:

   | pair | count | |
   |------|------:|--|
   | `add` / `sltu` | 301 | sum, and an unsigned compare of the same operands |
   | `xor` / `sltu` | 84 | |
   | `xor` / `slt` | 55 | |
   | `sub` / `xor` | 26 | |
   | `czero.nez` / `czero.eqz` | 25 | already `czero-or-chain`'s |
   | `add` / `sub` | 0 | **the tuple this frame was cut for never occurs** |
   | `min` / `max` | 0 | likewise |

   So the two shapes actually set aside score ZERO adjacently, and the
   same-source population that does exist is a different pairing — an
   arithmetic result beside a comparison of its inputs.  If this frame is
   ever built it should be fitted to that, not to sum/difference.

   **Add-with-carry is the real population, and it is much larger.**  Census
   of `op rd, x, y ; slt[u] rc, rd, y` — B reading A's RESULT, which is the
   carry-out idiom proper:

   | pair | count |
   |------|------:|
   | `add` / `sltu` | 431 |
   | `addw` / `sltu` | 36 |
   | `add` / `slt` | 26 |
   | `add.uw` / `sltu` | 5 |
   | others (`sub`, `sh1add`, `xor`) | 9 |
   | **total** | **507** |

   507 against zero for the same-source tuples.  It also needs no new frame
   shape: A's destination feeding B is exactly a CHAIN, except that here the
   temporary does not die — both results are wanted — so it is a chain frame
   with rda encoded.  Operands are rda, rs1, rs2 and rdb: four registers, the
   full 20-bit budget, no immediate.  That is the shape to price first.

9. **Already-covered suggestions, for the record**: load/store pair →
   `mem-base-pair`; writeback addressing → `pre/post-inc`; scaled-index →
   `index-mem-chain`; conditional select → `czero-or-chain`/`li-czero`;
   compare-immediate-branch → `li-branch-chain`; push/pop → `prologue`/
   `epilogue`; paired moves → `indep`; mulh/mul, div/rem →
   `macro-op` (now with the ISA manual's blessing to cite).

The candidate list from FINDINGS.md §4 (chain-alu-load-pair ~2.9k pairs at
~24 codepoints, mem-copy-pair ~1.2k at 5–7) predates the honesty pass and
needs remeasurement before any of it is drawn; setup-call-pair stays parked
on the call-optimism decision (§1).
