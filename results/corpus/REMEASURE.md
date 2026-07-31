# Remeasurement of the standing claims

Every numeric claim in `TODO.md` and `ACCOUNTING.md` re-derived against the
current tree and the current 17-file corpus, plus corrections to figures
reported earlier in the session. The point is not the individual numbers — it
is that several load-bearing ones had drifted far enough to argue the wrong
way, so anything sized against them was being sized against a corpus and an
op-set that no longer exist.

Method notes are inline. Where a claim is now false, `TODO.md` has been edited
rather than annotated: a stale number in a live document is worse than no
number.

---

## 1. Corrections to earlier reported figures

### Register pressure / spill traffic — recount

The spill measurement reported earlier counted every tab-prefixed line as an
instruction, inflating totals ~1.8% and mismeasuring the callee-saved subset.
Recounted with the same instruction filter `util/corpus_scores.py` uses, so
these totals agree with the byte tables in `README.md`:

```
build                    insns  sp ld/st    rate   callee   s2-s11
musl-rv64               102040     13799   13.5%    10136    19139
musl-norvc-rv64         101828     13639   13.4%    10001    20028
musl-os-rv64             93289     12759   13.7%    10071    18306
musl-osnoc-rv64          93159     12700   13.6%     9958    19016
musl-rv32               119026     26236   22.0%    14710    23542
musl-norvc-rv32         118755     26032   21.9%    14591    24429
musl-os-rv32            109880     25299   23.0%    14648    22598
musl-osnoc-rv32         109664     25146   22.9%    14508    23392
```

The conclusion is unchanged and the corrected figures are milder: the no-C
build uses **4.6%** more `s2`–`s11` references (20028 vs 19139, previously
reported as 8%) while spilling *less* (13639 vs 13799). Freer register
allocation still costs nothing in save/restore traffic.

New from the recount: rv32 spills at **22%** of instructions against rv64's
13.5%. That is a much larger effect than anything in the RVC-tax discussion
and it is a property of the ISA, not the flags.

### Claims withdrawn earlier, recorded here so they stay withdrawn

- **The setup-call frame** (once ranked the biggest opportunity, ~14k pairs)
  was withdrawn on measurement: only 3.5–6.3% of `jal` displacements fit 8
  bits, against 74–83% for `j`.
- **`index-chain-mem-pair` costed as a 10-codepoint bundle** — wrong; 94% of
  the mass is `lbu`+`sb`, two codepoints.
- **`FINDINGS.md` §1** is refuted by its own §5 and should be read that way.

---

## 2. `TODO.md` claims that are now false

| Claim | Then | Now |
|---|---|---|
| A1.2 codepoint overflow | "1036 > 1024, `encoding_assign.py` exits non-zero" | 1004/1024 reserved, 20 spare, exit 0 |
| A1.3 `mvload-jump-pair` has no frame | spec gap, 936 pairs | frame exists (`encoding.yaml:816`); `rules_conform` reports no unframed rule |
| A1.7 accepted vs encodable | "79.7% of matched pairs carry an immediate that fits" | **99.2%** (82272 of 82946 checkable, over musl-rv64 + sqlite-rv64 + musl-rv32) |
| A6 comma-joined frame names | three frames | two (`deref-chain-load-pair, base-chain-load-pair` and `load-sp-branch, load-base-branch`) |
| A6 budget/assign contradiction | budget said "all 21 frames fit", assign disagreed | both now say it fits; the frame sets agree (`dual-mem-shadd-pair` is gone, `mvload-jump-pair` is in both) |
| B14 dead `mem_pair` branch | unreachable branch in `_dual_shared_ok` | the string no longer occurs in `rules.py` |
| ACCOUNTING §5 shared anchor | "`chain-alu-pair` and `rsd-alu-pair` share one `*rsd_alu` anchor" | separate anchors since the carve-out (`chain_alu`, `rsd_alu`, `rsd_alu_j`) |

### Still true

- **B3** `stamp_solo_reasons` is not called from `__main__.py`.
- **B7** `args.verbose` is defined and never read.
- **A6** `encoding_budget.py` still iterates `RULES` from `rules.py`, not the
  yaml, so `encoding_budget.md` is generated from a different source of truth
  than `encoding.md`.

---

## 3. Claims whose numbers moved without changing direction

### ACCOUNTING §5 — unary ops in chain vs independent slots

Claim: unary ops (`li`/`mv`/`addi4spn`) are 65.4% of independent-pair slot
occupancy and 2.9% of chain. Remeasured over scheduled packets in musl-rv64 +
sqlite-rv64:

```
independent   19196/46932 = 40.9%
chain          2803/22312 = 12.6%
```

The asymmetry is real and large (3.2×) but both ends have moved a long way,
and the conclusion drawn from it — that one shared op-set serves both badly —
no longer applies, because they no longer share one.

### ACCOUNTING §1 — corpus ISA mismatch

Claim: `lw`/`sw` counts are 70%/52% RV32-sourced, so the `ld/lw/sd/sw`
clusters are sized against a blend matching no single target. The corpus is
now 17 files and near-balanced by instruction count (47.3% rv32), but the
mnemonic skew got **worse**, not better:

```
lw   rv32  212737  rv64   32968   87% rv32-sourced
sw   rv32  165330  rv64   20810   89% rv32-sourced
ld   rv32       0  rv64  192240
sd   rv32       0  rv64  122776
```

An rv64 build uses `ld`/`sd` for anything pointer-sized, so its `lw`/`sw` are
only its 32-bit data. Balancing the corpus by instruction count does not
balance it per mnemonic and cannot. **The concern is sharper than TODO records
it, not stale.**

---

## 4. New gaps found while remeasuring

### `pre-inc-pair` immediates do not fit — 35.1%

`encoding_verify` over three corpora:

```
frame                     matched  checkable   fit
pre-inc-pair                 1021        650  35.1%   addi_rsd imm=-1506 needs 12b vs 5b field
post-inc-pair                3152       3152  93.1%   addi_rsd imm=176   needs 6b vs 5b field
arith-mem-pair                458        344  93.6%   addi_rsd imm=-64   needs 7b vs 5b field
dual-indep-pair             17071       4339  99.7%   addi_rsd imm=192   needs 9b vs 5b field
TOTAL                      109770      82946  99.2%
```

Two thirds of scheduled `pre-inc-pair` packets carry an immediate too wide for
the field the row draws. Corpus-wide this is small (99.2% overall), but it lands
on the frame GCC leans on hardest — `pre-inc-pair` doubles under GCC (see
`GCC.md`) — so the two findings interact and the frame should be re-costed
before either is acted on.

### `rules_conform` cannot see row-derived field widths

The four frames above are exactly the ones `rules_conform` reports clean,
because its immediate check compares against a declared `imm: {bits}` op
contract and these fields get their width from the ROW layout instead. That is
the next gap of the same shape as the `@a_sp_mem` one: a constraint the yaml
states in a form the checker does not read.

### `chain-li-branch` A immediate

`rules_conform` now reports it accepts −128..127 against a declared 6 bits,
reconcilable only if `g`/`h` widen it by two — which is TODO A1 item 1, still
unsettled. Flagged, not fixed: fixing it narrows the rule and costs pairs, so
it belongs with the A1 decision rather than ahead of it.

---

## 5. Scheduler headroom (measured, for the record)

The list scheduler against exhaustive branch-and-bound in the same 16-
instruction window:

```
corpus       list   bnb    bnb+overlap4   fast    no-stall
testcase0    4217   4361   4376  (+3.8%)  3786    4219
musl-rv32   27896  28384  28569  (+2.4%)  24365   27942
```

**Where the gain is.** Instrumenting `_bnb_single_window` against its own list
seed over testcase0's 5565 windows: BnB improves on LIST in **165 of them
(3.0%)**, for +167 pairs. By window size, cumulated from the largest down:

```
size  windows  gain  cum gain%   windows >= size
  16      184     9      5.4%          3.3%
  11       65     9     18.6%          6.9%
  10       58    29     35.9%          7.9%
   9      103    33     55.7%          9.8%
   5      308    15     88.0%         24.5%
   4      535    19     99.4%         34.1%
   3     1259     1    100.0%         56.7%
```

Blocks of 1–3 instructions are 56.7% of all windows and contribute **one
pair**. The largest windows are the expensive ones and contribute 5.4%. The
money is in the middle — sizes 9 and 10 alone are 53% of the gain.

Gating BnB by window size (single-process harness, its own LIST baseline):

```
gate      pairs   time    gain captured
none       4195    3.2s        —
4..10      4330   39.8s        82%
4..12      4342   51.2s        89%
all        4361   82.6s       100%
```

So ordering — not the pairing model — leaves 2.4–3.8% on the table, and BnB
already demonstrates it is reachable. `STALL_FOR_PAIR` is worth ~0.1%
(27896 → 27942 without it, i.e. slightly negative). Note also that with a
fixed order the greedy matcher is already optimal: only adjacent pairs are
legal, which makes it maximum matching on a path, where left-to-right greedy
is exact. There is nothing to win in `greedy_pair`; all of the headroom is in
the order handed to it.

---

## 6. Best case from ordering alone — measured

Every corpus scheduled twice on the same tree: the default list scheduler, and
branch-and-bound with `--overlap 4` (the strongest setting we have). `vs RVC`
is under BnB; `parity` columns are pairs still needed to beat real RVC, so
negative means already past.

```
corpus         insns    list   bnb+ov4    gain    vs RVC   parity(list)  parity(bnb)
testcase0      21876    4217      4376   +3.8%     98.0%          -199         -358
godot          90172   13527     13963   +3.2%    110.8%         +7841        +7405
musl-rv64     102040   22291     22878   +2.6%    106.7%         +5525        +4938
musl-rv32     119026   27896     28569   +2.4%    101.5%         +1975        +1302
sqlite-rv64   189677   43115     44233   +2.6%    106.4%         +9840        +8722
sqlite-rv32   192768   46325     47480   +2.5%     94.5%*        +7445        +6290
TOTAL                 157371    161499   +4128                  +32427       +28299
```

*(sqlite-rv32's 104.5% as printed; the run's own figure, not recomputed here.)*

**No corpus changes side.** testcase0 was the only one past RVC and still is —
its margin nearly doubles, −199 to −358 pairs. Every other corpus stays behind,
and the aggregate gap closes from 32427 pairs to 28299: **the best ordering we
can compute is worth 12.7% of the remaining distance to parity.**

The gain is remarkably uniform — 2.4% to 3.8%, tightest on the largest corpora —
which says it is a property of the scheduler, not of any particular code.
Cost is 8-10x wall clock (sqlite-rv64: 51s to 401s).

So ordering is real, cheap in codepoints, and not a category change. Frames
remain where parity has to come from.
