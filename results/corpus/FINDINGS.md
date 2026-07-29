# Findings from the corpus analysis

> §1's defence of two frames was later refuted by a second C++ corpus.
> Read §5 before acting on §1.

Four Fable 5 subagents analysed the corpus set after musl and SQLite landed.
Numbers below are theirs unless marked VERIFIED, which means reproduced
independently. Nothing here has been applied to `encoding.yaml` or
`scheduler/rules.py`.

Scores that prompted all of this are in `README.md`. The break-even line is
`P > C/2`.

---

## 1. godot was the outlier, and some frames were sized on it

Pair rate by corpus: godot 29.3%, testcase0 39.4%, musl 42.9–43.1%,
sqlite 43.9–45.9%. **godot is the hardest corpus in the set, and most tuning
to date was done against it.** That is conservative rather than wrong, but it
means several budgets and field widths were derived from the least
representative binary.

### Frames whose budget looks reclaimable

| frame | budget | evidence |
|---|---|---|
| `dual-mem-shadd-pair` | 4 of post-inc's 16 | **0 hits on all six corpora.** Not because Zba is missing — `sh2add`/`sh3add` occur up to 2687 times and `pre-inc-pair` does consume them. Compilers form the address *before* the access; a post-access shXadd base update is an idiom nothing emits. |
| `dual-arith2-pair` | 11 used of 16 | Raw hits 1 / 13 / 30 / **0** / 89 / **0**. Only the `mul`/`mulh*` clusters ever fire; add/sub, addw/subw, min/max and every div/rem tuple are zero on all six. Keeping just `{mul}×{mulh,mulhu,mulhsu}` is 3 codepoints and frees the 16-block. |
| chain-load A-slot | 49 → 14 | The A slot of `deref-chain` and `base-chain` is **100.0% lw/ld across all six**; lb/lbu/lh/lhu/lwu never appear. 7×7 → 2×7 drops the 64-block to 16. |
| `store-chain-alu-pair` | 32 | ≤0.8 per 1000 everywhere, max 96 raw pairs. `addi-store-pair` drained it, and that is now confirmed out-of-sample. |

Together roughly **100 codepoints**, against 130 currently spare.

### Frames that are strong elsewhere but were sized for godot

- **`mvload-jump-pair`** — 33.7–34.5 per 1000 on sqlite (its second-largest
  frame there), against 7.5 on godot. Budget is 16, and the yaml note admits
  the load-offset field was sized on a single RV32 idiom: *"54 of 55 are the
  same frame-pointer spill."* sqlite now supplies mass evidence to re-derive
  `imma` honestly.
- **`load-base-branch`** 18.2–18.5 per 1000 on sqlite vs 7.8 godot;
  **`arith-mem-pair`** 5.0–8.7 vs 1.3. Both deserve a sizing pass. Note the
  offset-overflow distributions were NOT measured, so "widen the field" is a
  hypothesis, not a finding.

### Godot-flavoured — REFUTED, see §5

`load-sp-branch` (11.0 per 1000 on godot vs 1.1–2.5) and
`load-chain-alu-pair` (8.3 vs 1.7–2.0) were argued here to track C++
register-pressure spill/reload — corpus *character* rather than a tuning
accident, on the reasoning that any C++ workload would reproduce it.

**A second C++ corpus refuted this.** cpp-rv64/cpp-rv32 use
`load-chain-alu-pair` at 0.29/0.34 per 1000 — less than any other corpus in
the set — and `load-sp-branch` at 1.54/1.70, squarely in the ordinary band.
See §5. Both frames' budgets are reclaimable.

`chain-bit-test-branch` is the mirror image: 24.5 per 1000 on **testcase0**
vs 0.6 on godot, and two anonymous Rust functions own 30% of testcase0's
hits. ACCOUNTING §6 applies. Same for `addi-store-pair` (14.9 vs 2.4–5.2).

### One op-set eviction

`maxu` in `chain_alu`: 86 occurrences in godot, 21–29 in each new corpus,
and inside accepted chain pairs 74 slot-uses on godot vs ≤9 anywhere else
(zero on musl-rv32). It is a Zbb op godot's build happens to emit.

Worth noting `chain-alu-pair`'s rate is *lowest* on godot (3.4 vs 6.3–17.8),
so that frame is under-fitted to godot, not over-fitted — it was testcase0
inflating the pooled rate.

---

## 2. RV32 vs RV64: same sign, opposite causes — VERIFIED

`vsRVC = (1 − P/N) / (1 − C/2N)`. Decomposed:

| corpus | P/N (packet term) | C/2N (RVC term) | vsRVC |
|---|---:|---:|---:|
| musl-rv32 | 21.55% | **25.10%** | 104.7% |
| musl-rv64 | 21.44% | **27.26%** | 108.0% |
| sqlite-rv32 | **22.94%** | 27.89% | 106.9% |
| sqlite-rv64 | **21.95%** | 27.92% | 108.3% |

- **musl's RV32 advantage is entirely on the RVC side.** Packets pair almost
  identically (21.55 vs 21.44); RVC just compresses musl-rv64 better. RV32
  does not win — RVC loses.
- **sqlite's is entirely on the packet side.** RVC is identical
  (27.89 vs 27.92); packets genuinely pair 1pp more of the RV32 stream.

So the headline "RV32 does better" is half a real result and half a weak
denominator. The real one: the RV32 stream is intrinsically denser in the
shapes the frames encode — register-pair spills produce adjacent lw/lw and
sw/sw at consecutive offsets (mem-pair adjacency candidates 14347 vs 8714 on
musl), and carry chains produce dependent ALU pairs (`sltu` 594 vs 40 on
sqlite).

### The offset hypothesis is inverted

"64-bit pointers widen offsets" is true in bytes but **backwards after
width-scaling**, because scaling divides by k. An RV64 frame twice the byte
size scales back to the same index range; an RV32 frame of the *same* byte
size — which is what musl produces, since the spilled data is still 64-bit —
needs one more bit at k=4.

sp-relative offsets, cumulative % fitting ≤N bits width-scaled:

| corpus | sites | ≤5b | ≤6b | ≤7b | ≤10b |
|---|---:|---:|---:|---:|---:|
| musl-rv32 | 27212 | 51.8 | **63.0** | 75.2 | 99.9 |
| musl-rv64 | 14621 | 83.7 | **93.8** | 97.2 | 99.9 |
| sqlite-rv32 | 24149 | 86.1 | 94.9 | 98.2 | 99.9 |
| sqlite-rv64 | 23766 | 89.8 | 96.9 | 98.9 | 100.0 |

This is exactly where musl-rv64's RVC advantage comes from: `c.lwsp`/`c.swsp`
reach 256 bytes, `c.ldsp`/`c.sdsp` reach 512, so a third of musl-rv32's sp
accesses miss RVC on *range*. **The packet frames' 10-bit sp rows cover 99.9%
on both targets** — the encoding is already doing precisely what RVC cannot
do for RV32. No widening needed there.

### RV64 bias in the encoding

Roughly **223 of the 894 spoken-for codepoints name RV64-only ops** — dead
weight on an RV32 implementation. About 115 are aliasable w-forms (addiw,
addw, srliw, subw, div*w) and about 108 are ld/sd/lwu columns.

Two zero-cost proposals follow from that, both with RVC precedent (RVC itself
reuses quadrants mode-dependently: c.jal↔c.addiw, c.flw↔c.ld):

1. **Declare the w-forms RV32-mode aliases** of their non-w counterparts.
   Turns a quarter of the RV64 bias into shared codepoints; a yaml
   annotation, displacing nothing.
2. **Reinterpret the ld/sd/lwu codepoints in RV32 mode as extended-range
   lw/sw** — one extra width-scaled offset bit, doubling byte reach to parity
   with RV64's k=8. This attacks the one distribution where RV32 measurably
   starves, and hands RV32 back the bit the k-scaling convention gives RV64
   for free. Needs a mode-dependent-decode section in the yaml and an XLEN
   switch in `rules.py`, which currently has none.

RV64's own gap: `sext.w`/`zext.w` (784 musl / 519 sqlite sites) appear in no
frame — the cheapest identified candidate for the RV64 side's 1pp deficit.

### ACCOUNTING §2 is settled by the matched corpora

The non-w form dominates **every** high-volume ambiguous pair: add 93.2/90.0%,
addi 88.4/87.4%, slli 93.6/90.3%, srli 84.5/82.0%, mul 94.1/97.3%. Weakest is
sub at 69.8/60.9%, still non-w. Only `sll` inverts (34–44%) and `div` at n≤44
is noise. So the skew rule can be adopted as SETTLED and global — a no-op in
effect, but it legitimises the alias proposal above. `sub` is the case to
re-check if a future corpus disagrees.

### One conditional cost

`addi4spn` at 6 bits scale-4 does not survive the matched corpora:
musl-rv32 has 4242 sites, 9.7% not 4-aligned, and u6x4 fits only **39.7%**
(u7x4 55.1%, u8x4 70.4%). Widening to 7 bits costs ~16 codepoints and pushes
`dual-indep-pair` from its 16-block to 32. Apply the §6 concentration check
first — musl-rv32's big-frame functions may own that tail. Scale 8 is
re-refuted for RV32 (u6x8 fits 43.9%).

---

## Method caveats carried from the agents

- Per-frame attribution is first-accepting-rule, so RULES order shapes every
  absolute per-frame number. Cross-corpus ratios *within* one rule are fine.
- Candidate acceptance ≠ packed pairs, and they diverge badly in one place:
  on godot, `load-chain-alu-pair` has 44 adjacent candidates but 746 scheduled
  pairs (17×), because the list scheduler drags sp-reloads next to consumers.
  This is a live warning for every candidate-based op-set search in
  `analysis/`.
- Immediate-width fit ("encodable pairs", ACCOUNTING §4 category 3) was NOT
  re-measured on the new corpora. Every field-width recommendation needs that
  pass before any yaml edit.
- All four corpora share clang 18 -O2, so cross-target *differences* are
  trustworthy even where absolute levels are not. musl's magnitude (double sp
  traffic) is workload — a libc is 64-bit-type-heavy by design.

---

## 3. Where the missing pairs are

Harness validated against the reference runs exactly (P = 21874 / 41631 /
25652, solos identical), so these are real-pipeline numbers.

| corpus | pairs needed (C/2−P) | ENC | SCHED (cons–opt) | ENC as % of need | STRUCT % of misses |
|---|---:|---:|---:|---:|---:|
| musl-rv64 | 5942 | **2053** | 495–1894 | 35% | 91% |
| sqlite-rv64 | 11324 | **5863** | 1174–4146 | 52% | 87% |
| musl-rv32 | 4219 | **4863** | 622–2818 | **115%** | 84% |

**The structural hypothesis is right about the population and wrong about the
margin.** 84–91% of misses have no near-miss fix. But break-even needs only
~20% more pairs, and at that margin measured 1–2-bit width deficits plus
scheduler recovery cover 43% / 62% / >100% of the gap.

The binding resource is **not codepoint slack** — it is immediate-field bits
inside frame rows, whose cost is multiplicative (ACCOUNTING §7). "More
encoding space" helps only as targeted per-frame widening, and for the RV64
corpora it cannot close the gap alone.

### The biggest single opportunity: calls — VERIFIED

`addi + jal(ra)` is the top leftover adjacency in all three corpora.
`is_call` excludes jal-with-link from **every** jump B slot, so argument
setup before a call — the exact shape `mvload-jump-pair` captures before a
*tail* call — is unreachable.

Raw adjacency, independently verified: **3900 (musl-rv64) / 6043
(sqlite-rv64) / 3343 (musl-rv32)** addi-family instructions immediately
before a call. The agent's realizable estimate after filtering was
~1200 / ~2200 / ~1500 pairs. Cost ≈ one frame at arith-jump's scale (~64
codepoints), which the ~100 reclaimable from §1 would fund.

A call in the B slot needs `ra` to point past the packet — packets are
4 bytes and 32-bit aligned, so that is `packet_addr + 4`, the same as any
other instruction. The exclusion looks like a design choice worth revisiting,
not a hard constraint.

### Measured width deficits, by rule

| rule / limit | deficit | musl-rv64 | sqlite-rv64 | musl-rv32 |
|---|---|---:|---:|---:|
| mem-pair non-local offset (5u scaled) | +1 | 62 | 151 | **1394** |
| load-base-branch offset (5u scaled) | +1–2 | 30 | **1034** | 41 |
| arith-mem-pair B offset (2-bit scaled) | +1–2 | 62 | **693** | 57 |
| chain-alu addi (6s) | +1–2 | 222 | 163 | 83 |
| mvload-jump load offset (5u) | +1–2 | 22 | **296** | 27 |
| addi-store (10s / 5u-sp / base=0) | +1–2 | 98 | 182 | 233 |
| rsd-alu addi (7s) | +1–2 | 133 | 95 | 94 |
| dual-indep addi4spn | +1–4 | 111 | 48 | **841** |
| load/store-chain `base-not-sp` | needs base field | 234 | 530 | 313 |
| chain-bit-test andi not mask-shaped | needs full andi imm | 27 | 271 | 27 |

Deficits are front-loaded at 1–2 bits everywhere except arith-jump and
addi-branch, which have long tails to +6/+7. Unaligned/negative offsets
("inf") are only 83–143.

Note `mem-pair` +1 bit is worth 1394 pairs on musl-rv32 but only 62–151 on
RV64 — the RV32-specific case, since lw/sw scaling by 4 halves the byte reach.
This is the same k-scaling asymmetry §2 identified.

### Scheduler recovery — free

**495 / 1174 / 622 pairs measured** as a movability-proven lower bound;
ceiling ignoring movability is 1894 / 4146 / 2818. Costs no encoding space at
all. The rules being missed are mvload-jump, load-base-branch, chain-alu and
mem-pair — the scheduler is failing to bring jumps and branches together with
their partners.

### Ops in no frame at all

auipc (2.9–3.7k), lui (1.1–1.9k), float (musl ~4k), `czero.*` (sqlite 1530),
`sh1add` (sqlite 1030), `sext.w`, `bltz`/`blez`/`bgez`.

### Caveats

ENC counts only *adjacent* near-misses, so it is itself a floor. Line items
are independently measured but realizing all of them means widening many
fields at once. 343/968/1117 of the ENC total are "no numeric deficit"
near-misses (imm==0 exclusions, mask-shape failures) — patch-verified, medium
confidence. Branch/jump displacement optimism applies to the call frame and
to load-base-branch.

---

## 4. New frame candidates

Counts are category-1 ceilings (adjacencies a rule would accept) for pairs no
current rule captures. Observed capture→scheduled realization on these corpora
is ~0.78–0.81. All shapes passed the §6 concentration check (top function ≤4%).

| # | candidate | total | encodable | codepoints | est. yield |
|---|---|--:|---|--:|--:|
| 1 | **setup-call-pair** (`mv/li/addi4spn` + `call`) | 19663 | fits, but see caveat | 4 | ~14k |
| 2 | **load-chain any-base** | 5052 | **rows already exist** | **0** (lw/ld) | ~1.5k |
| 3 | **chain-alu-load-pair** (`add/shXadd/addi` → load via tmp) | 3908 | fits | ~24 | ~2.9k |
| 4 | **czero-select-or** | 2492 | fits | **2** | ~1.9k |
| 5 | **mem-copy-pair** (`load tmp,imma(ra)` → `store tmp,immb(rb)`) | 1560 | fits exactly, 20 bits | 5–7 | ~1.2k |

Candidates 2–5 total 46 codepoints (110 with byte/half loads added to #2),
against 130 free. They supply roughly half the break-even gap. Candidate 1,
if admissible, closes it outright.

### Candidate 2 is free money — VERIFIED

The yaml template for `load-chain-alu-pair` is
`load tmp, k*imma(rs1a)` with rows 1–2 drawing an explicit `rs1a` base field;
rows 3–4 are the SP-relative variant with a 10-bit offset. **`rules.py`
applies `@a_sp_mem` unconditionally**, refusing the any-base form the encoding
already reserves. `rules_conform` cannot see this — base-register constraints
are in its explicit NOT-CHECKED list.

Measured by removing the decorator and re-running:

| corpus | before | after | delta |
|---|--:|--:|--:|
| musl-rv64 | 21874 | 22111 | **+237** |
| sqlite-rv64 | 41631 | 42644 | **+1013** |

**Zero codepoints.** This is the first thing to do.

### Zicond: CONFIRMED and stronger than godot

czero counts: musl-rv32 1281, musl-rv64 1017, sqlite-rv32 2319,
sqlite-rv64 1530 (godot 272, testcase0 0). Forward chain into `or` is
**97–99%** on the new corpora against godot's 93%; chained *and dead* totals
2492 (~91%).

`[czero, czero]` exists at scale (~1464 pooled) but is **refuted as a frame**:
both temps stay live into the following `or`, so it needs five register fields
against 20 bits. The three-instruction select is served by pairing the
*second* czero with the `or` — which is candidate 4, at 2 codepoints.

### Candidate 1 caveat — quote it whenever the number is quoted

Its entire yield rests on the existing §8 optimism that jump displacements are
unbounded and unencoded. `encoding.yaml` *deliberately* excludes calls from
every jump frame, and a call target genuinely has nowhere to live in the
packet. It is the largest idiom in the corpus by ~4× and emphatically not an
artifact (mv+call top function 1.0%, present in 5 of 6 corpora), but it should
be adopted only if the project decides the `j`-optimism extends to calls.
Otherwise strike it, and candidates 2–5 deliver about half the gap.

### Rejected

- `auipc+jalr` (7563) — godot/testcase0 only (non-PIC call spelling), needs a
  20-bit immediate. Artifact *and* unencodable. Same idiom as #1, spelled
  differently.
- `auipc/lui + addi/ld` (~19k) — 20-bit upper immediates, unencodable.
- Independent same-op mem pairs with free offsets (`lw+lw` 10.6k, `sw+sw`
  5.7k) — two independent offsets need 3 regs + 2×5 bits = 25 bits. The
  encodable residue (~3.8k, same base, delta one width, offset just out of
  range) is a mem-pair offset-width question, and there are no spare row bits.
- `mv+mv`, `addi_rsd+sw`, `lw+beqz` residues — shapes existing frames already
  own, failing on ordering or escape. Tuning, not frame gaps.

---

## Suggested order of work

1. **Candidate 2** — delete one decorator, +1250 measured on two corpora, zero
   codepoints. (Then extend `rules_conform` to catch base-register
   disagreements, which is how this hid.)
2. **Reclaim ~100 codepoints** from §1 (dual-mem-shadd 0/6, dual-arith2,
   chain-load A-slot 7→2, store-chain).
3. **Candidate 4** (zicond) at 2 codepoints for ~1.9k pairs — best ratio in
   the set.
4. **Scheduler work** — ~500–1900 pairs, zero encoding cost.
5. **Decide the call question.** It is the difference between closing the
   break-even gap and getting halfway.
6. **Candidates 3 and 5**, funded by step 2.
7. Targeted width increases from §3's table, cheapest-first.

---

## 5. UPDATE — a second C++ corpus refutes §1's "corpus character" defence

`cpp-rv32` / `cpp-rv64` (leveldb + protobuf, two independent codebases, ~415k
instructions each, matched musl toolchain) settle the question §1 left open.

**godot is an outlier binary, not representative C++.** It pairs at 29.3%;
the two C++ corpora pair at **40.1% and 41.5%** — 11 points better, in line
with everything else in the set.

### Both "C++ character" frames are refuted

Normalised hits per 1000 instructions:

| frame | godot | cpp-rv64 | cpp-rv32 | musl-rv64 | sqlite-rv64 |
|---|--:|--:|--:|--:|--:|
| `load-chain-alu-pair` | **8.27** | **0.29** | **0.34** | 1.71 | 1.86 |
| `load-sp-branch` | **11.01** | **1.54** | **1.70** | 1.30 | 2.53 |

§1 argued these two track C++ register-pressure spill/reload and that "any
C++ workload would reproduce it". It does not. The C++ corpora use
`load-chain-alu-pair` **less than any other corpus in the set** — 0.29 against
musl's 1.71 — and `load-sp-branch` sits in the ordinary 1.3–2.5 band.

godot's rates are a property of that one binary. `load-chain-alu-pair`'s
64-codepoint block and `load-sp-branch`'s dedicated 10-bit sp row are
justified by nothing but godot, which adds to the ~100 codepoints §1 already
identified.

### The other two disputed frames hold up

| frame | godot | cpp-rv64 | cpp-rv32 | sqlite-rv64 |
|---|--:|--:|--:|--:|
| `load-base-branch` | 7.76 | 11.22 | 11.66 | 18.47 |
| `mvload-jump-pair` | 7.52 | 14.87 | 14.28 | 34.53 |

Both are strong everywhere and weakest on godot — §1's "sized for godot,
strong elsewhere" reading is confirmed, now on five corpora.

### C++ still loses worst, for a different reason

`vsRVC` 111.4–112.0%, essentially tied with godot's 111.8% — **despite
pairing 11 points better**. The cause is on the RVC side: C++ compresses
better than anything else in the set (71.1–71.4% of baseline). And it does so
through the quadrant packets claim:

```
cpp-rv64   c.mv:49351  c.ldsp:32254  c.sdsp:31139  c.ld:19677  c.j:15626
```

`c.mv` + `c.ldsp` + `c.sdsp` alone are 47% of its compressed instructions, all
C2. C++ leans on the third of RVC that packets destroy far harder than C does.

This makes the call frame more attractive, not less: **28008 of 29983 call
sites in cpp-rv64 have an addi-family predecessor (93%)**, against 49351
`c.mv` in the corpus. The idiom RVC spends `c.mv` on is exactly what a
setup-call frame would absorb.
