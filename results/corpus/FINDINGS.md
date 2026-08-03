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
| `post-inc-shadd-pair` | 4 of post-inc's 16 | **0 hits on all six corpora.** Not because Zba is missing — `sh2add`/`sh3add` occur up to 2687 times and `pre-inc-pair` does consume them. Compilers form the address *before* the access; a post-access shXadd base update is an idiom nothing emits. |
| `macro-op-pair` | 11 used of 16 | Raw hits 1 / 13 / 30 / **0** / 89 / **0**. Only the `mul`/`mulh*` clusters ever fire; add/sub, addw/subw, min/max and every div/rem tuple are zero on all six. Keeping just `{mul}×{mulh,mulhu,mulhsu}` is 3 codepoints and frees the 16-block. |
| chain-load A-slot | 49 → 14 | The A slot of `deref-load-chain` and `base-load-chain` is **100.0% lw/ld across all six**; lb/lbu/lh/lhu/lwu never appear. 7×7 → 2×7 drops the 64-block to 16. |
| `alu-store-chain` | 32 | ≤0.8 per 1000 everywhere, max 96 raw pairs. `addi-store-chain` drained it, and that is now confirmed out-of-sample. |

Together roughly **100 codepoints**, against 130 currently spare.

### Frames that are strong elsewhere but were sized for godot

- **`setup-jump-pair`** — 33.7–34.5 per 1000 on sqlite (its second-largest
  frame there), against 7.5 on godot. Budget is 16, and the yaml note admits
  the load-offset field was sized on a single RV32 idiom: *"54 of 55 are the
  same frame-pointer spill."* sqlite now supplies mass evidence to re-derive
  `imma` honestly.
- **`load-base-branch-pair`** 18.2–18.5 per 1000 on sqlite vs 7.8 godot;
  **`arith-mem-pair`** 5.0–8.7 vs 1.3. Both deserve a sizing pass. Note the
  offset-overflow distributions were NOT measured, so "widen the field" is a
  hypothesis, not a finding.

### Godot-flavoured — REFUTED, see §5

`load-sp-branch-pair` (11.0 per 1000 on godot vs 1.1–2.5) and
`load-alu-chain` (8.3 vs 1.7–2.0) were argued here to track C++
register-pressure spill/reload — corpus *character* rather than a tuning
accident, on the reasoning that any C++ workload would reproduce it.

**A second C++ corpus refuted this.** cpp-rv64/cpp-rv32 use
`load-alu-chain` at 0.29/0.34 per 1000 — less than any other corpus in
the set — and `load-sp-branch-pair` at 1.54/1.70, squarely in the ordinary band.
See §5. Both frames' budgets are reclaimable.

`bit-test-branch-chain` is the mirror image: 24.5 per 1000 on **testcase0**
vs 0.6 on godot, and two anonymous Rust functions own 30% of testcase0's
hits. ACCOUNTING §6 applies. Same for `addi-store-chain` (14.9 vs 2.4–5.2).

### One op-set eviction

`maxu` in `alu_chain`: 86 occurrences in godot, 21–29 in each new corpus,
and inside accepted chain pairs 74 slot-uses on godot vs ≤9 anywhere else
(zero on musl-rv32). It is a Zbb op godot's build happens to emit.

Worth noting `alu-alu-chain`'s rate is *lowest* on godot (3.4 vs 6.3–17.8),
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
sw/sw at consecutive offsets (mem-base-pair adjacency candidates 14347 vs 8714 on
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
`indep-pair` from its 16-block to 32. Apply the §6 concentration check
first — musl-rv32's big-frame functions may own that tail. Scale 8 is
re-refuted for RV32 (u6x8 fits 43.9%).

---

## Method caveats carried from the agents

- Per-frame attribution is first-accepting-rule, so RULES order shapes every
  absolute per-frame number. Cross-corpus ratios *within* one rule are fine.
- Candidate acceptance ≠ packed pairs, and they diverge badly in one place:
  on godot, `load-alu-chain` has 44 adjacent candidates but 746 scheduled
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
setup before a call — the exact shape `setup-jump-pair` captures before a
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
| mem-base-pair non-local offset (5u scaled) | +1 | 62 | 151 | **1394** |
| load-base-branch-pair offset (5u scaled) | +1–2 | 30 | **1034** | 41 |
| arith-mem-pair B offset (2-bit scaled) | +1–2 | 62 | **693** | 57 |
| alu-alu-chain addi (6s) | +1–2 | 222 | 163 | 83 |
| setup-jump load offset (5u) | +1–2 | 22 | **296** | 27 |
| addi-store-chain (10s / 5u-sp / base=0) | +1–2 | 98 | 182 | 233 |
| rsd-alu addi (7s) | +1–2 | 133 | 95 | 94 |
| indep addi4spn | +1–4 | 111 | 48 | **841** |
| load/store-chain `base-not-sp` | needs base field | 234 | 530 | 313 |
| bit-test-branch-chain andi not mask-shaped | needs full andi imm | 27 | 271 | 27 |

Deficits are front-loaded at 1–2 bits everywhere except arith-jump and
addi-branch, which have long tails to +6/+7. Unaligned/negative offsets
("inf") are only 83–143.

Note `mem-base-pair` +1 bit is worth 1394 pairs on musl-rv32 but only 62–151 on
RV64 — the RV32-specific case, since lw/sw scaling by 4 halves the byte reach.
This is the same k-scaling asymmetry §2 identified.

### Scheduler recovery — free

**495 / 1174 / 622 pairs measured** as a movability-proven lower bound;
ceiling ignoring movability is 1894 / 4146 / 2818. Costs no encoding space at
all. The rules being missed are setup-jump, load-base-branch-pair, alu-alu-chain and
mem-base-pair — the scheduler is failing to bring jumps and branches together with
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
to load-base-branch-pair.

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

The yaml template for `load-alu-chain` is
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
  range) is a mem-base-pair offset-width question, and there are no spare row bits.
- `mv+mv`, `addi_rsd+sw`, `lw+beqz` residues — shapes existing frames already
  own, failing on ordering or escape. Tuning, not frame gaps.

---

## Suggested order of work

1. **Candidate 2** — delete one decorator, +1250 measured on two corpora, zero
   codepoints. (Then extend `rules_conform` to catch base-register
   disagreements, which is how this hid.)
2. **Reclaim ~100 codepoints** from §1 (dual-mem-shadd 0/6, macro-op,
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
| `load-alu-chain` | **8.27** | **0.29** | **0.34** | 1.71 | 1.86 |
| `load-sp-branch-pair` | **11.01** | **1.54** | **1.70** | 1.30 | 2.53 |

§1 argued these two track C++ register-pressure spill/reload and that "any
C++ workload would reproduce it". It does not. The C++ corpora use
`load-alu-chain` **less than any other corpus in the set** — 0.29 against
musl's 1.71 — and `load-sp-branch-pair` sits in the ordinary 1.3–2.5 band.

godot's rates are a property of that one binary. `load-alu-chain`'s
64-codepoint block and `load-sp-branch-pair`'s dedicated 10-bit sp row are
justified by nothing but godot, which adds to the ~100 codepoints §1 already
identified.

### The other two disputed frames hold up

| frame | godot | cpp-rv64 | cpp-rv32 | sqlite-rv64 |
|---|--:|--:|--:|--:|
| `load-base-branch-pair` | 7.76 | 11.22 | 11.66 | 18.47 |
| `setup-jump-pair` | 7.52 | 14.87 | 14.28 | 34.53 |

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

---

## 6. The call frame is much weaker than §3 claimed — and a better jump target exists

§3 ranked a setup-call frame as the largest opportunity (19663 adjacencies,
~14k estimated pairs), caveated on "whether the j-optimism extends to calls".
Measuring the displacements settles it: **it does not.**

Displacement magnitude, from the linked binaries (bits needed, halfword-scaled):

| | ≤8b | ≤10b | ≤12b | ≤16b |
|---|--:|--:|--:|--:|
| `j` — already allowed, unchecked (sqlite) | **74.0%** | 82.7% | 89.9% | 98.6% |
| `j` (musl) | **82.5%** | 92.3% | 95.5% | 98.6% |
| `jal` — excluded as a call (sqlite) | **3.5%** | 7.4% | 14.5% | 55.0% |
| `jal` (musl) | **6.3%** | 13.0% | 24.5% | 63.0% |

`j` is intra-function and mostly local; `jal` is inter-function and mostly
far. Neither is encoded today, but the §8 optimism costs little on `j` and
would be a fiction on `jal`: **85% of calls need a displacement that fits
nowhere in the packet.** The `is_call` guard is therefore well founded — not
because of a 12-bit linker fixup, but because call targets are genuinely
distant.

The zero-immediate escape does not exist either. `jalr` writing ra — an
indirect call, target in a register, needing no immediate at all — occurs
**0 times** in cpp-rv64, sqlite-rv64 and musl-rv64. Every call in the corpus
is a direct `jal`, including PLT calls. Even C++ virtual dispatch produced
none, because the vtable load and the call are separated.

So: strike the setup-call frame. Its ~14k pairs were resting on an assumption
the data refutes.

### What has room: mv/li paired with a direct `j`

A direct jump needs no target register, which frees the `rs1b` field the jump
frames spend on `jr`. With `A = mv rda, rs1a` (two 5-bit fields) the packet
has **12 operand bits spare** — enough for a real, checked displacement
instead of the optimism.

On sqlite-rv64 there are **1791 adjacent (mv|li) + `j` pairs**, and their
displacements fit:

```
   <= 8b scaled:  82.6%
   <=10b scaled:  92.9%
   <=12b scaled:  96.5%
```

A 12-bit scaled field captures 96.5% of them with bits already available.
This is the jump-with-immediate candidate: unlike the call frame it needs no
new optimism, and unlike the existing jump frames it would be *honest* —
the displacement checked rather than assumed.

Worth noting the same measurement indicts the current frames mildly:
`arith-jump-pair` and `setup-jump-pair` accept `j` with no range check at
all, and 17–26% of `j` targets exceed 8 bits. Those frames have no spare bits
in their `jr` rows, so making them honest means either a direct-jump-only row
or accepting the existing fiction.

---

## 7. Build-flag variants: the RVC register tax is real but small

Four extra musl builds, same source and toolchain, varying only the flags.

```
variant                 insns  pairs  pair%  packet%  realRVC%   vsRVC
musl-rv64  -O2 +C      102040  21973  43.1%    78.5%     72.7%  107.9%
musl-os-rv64  -Os +C    93289  20442  43.8%    78.1%     72.5%  107.7%
musl-norvc-rv64 -O2    101828  22198  43.6%    78.2%    100.0%   78.2%
musl-rv32  -O2 +C      119026  27481  46.2%    76.9%     74.9%  102.7%
musl-os-rv32  -Os +C   109880  25906  47.2%    76.4%     74.7%  102.3%
musl-norvc-rv32 -O2    118755  27856  46.9%    76.5%    100.0%   76.5%
```

### Compiling for RVC costs us register-allocation freedom — measured

RVC's register fields are 3 bits, so codegen targeting it biases allocation
into x8..x15. Our packet rows draw FULL 5-bit fields, so that clustering is
pure loss to us: we inherit a constraint imposed for a compression scheme we
do not use. (This is the same axis as opening `_RSD_ALU_REGS` from x0..x15 to
x0..x31 earlier, which was worth 377 pairs.)

Directly measured on musl-rv64: **68.5% of register references fall in x8..x15
with `c`, 63.9% without** — a 4.6pp redistribution, so the bias is real.

But it is worth only **+0.5pp** of pair rate on rv64 and **+0.7pp** on rv32.
The instruction counts barely move (102040 vs 101828), which says clang's RVC
targeting is mostly encoding *selection* rather than code reshaping. Roughly
half a point of the ~8pp gap to parity.

CAVEAT: the no-C build is not perfectly matched (101828 vs 102040
instructions), so a little of the pair delta may be instruction selection
rather than allocation. The 68.5/63.9 register split is the direct evidence
and is unconfounded.

### Read the no-RVC vsRVC column with care

100.0% "real RVC" there just means we told the compiler not to emit any
compressed instructions, so vsRVC collapses to packet%. It is not a win. The
meaningful comparison for one source is between DEPLOYMENTS: an RVC build at
72.7% of baseline, or a no-RVC build packed at 78.2%. That is still 107.6% --
unchanged, because freeing the registers only bought half a point.

### -Os is a mild positive and competitively neutral

8.6%/7.7% fewer instructions, pair rate up 0.7-1.0pp, vsRVC flat (107.9->107.7,
102.7->102.3). Smaller code pairs slightly better and RVC compresses it
slightly worse; the two nearly cancel.

## §4 candidates REMEASURED (2026-08, post-honesty-pass)

The §4 table above was measured before the width-honesty pass, before
`index-mem-chain` existed, and against a different attribution order.  Both
surviving candidates were re-run as scratch rules against the current tree,
each ALONE (joint runs hide which frame a pair really came from) on
musl-rv32 + sqlite-rv64.  Baseline 27192 / 40851.

| candidate | musl-rv32 | sqlite-rv64 | net pairs | codepoints | pairs/cp |
|---|--:|--:|--:|--:|--:|
| 3 alu -> load via dead temp | +186 | +564 | **+750** | ~+24 | ~31 |
| 5 mem-copy (load -> store via dead temp) | +313 | +541 | **+854** | 4-16 | 53-213 |
| both together | +484 | +1093 | +1577 | | |

750 + 854 = 1604 against 1577 measured jointly, so the two overlap by only
27 pairs — they are effectively independent and can be judged separately.

**Candidate 3 is not what its name says.**  Of its A slots only 2 (musl) and
16 (sqlite) are `addi`, so this is NOT the foldable `addi t,b,k; ld d,0(t)`
population `index-mem-chain` deliberately excludes.  It is `add`/`shXadd`
where the shift does NOT match the access width — indexing a byte array with
a word-scaled index, struct-array walks, and so on.  So the real proposal is
"relax `index-mem-chain`'s width matching from the diagonal to the full
cross product": 4 shifts x 8 memory ops = 32 codepoints against the 8 it
reserves now, i.e. **+24 codepoints for ~750 pairs (~31/cp)**, comfortably
above the ~6 portfolio floor.

**Candidate 5 survives intact and is the better buy.**  A load whose value is
stored straight back out through a dead temporary — a memory copy — is served
by no current frame, and the fields fit exactly: rbase_a + imma + rbase_b +
immb = 20 bits, with the temp unencoded.  Widths match 90% (musl) / 71%
(sqlite) of the time, so the diagonal alone (lbu-sb, lhu-sh, lw-sw, ld-sd, 4
codepoints) captures most of it; the full 4x4 cross product costs 16 and
picks up the width-changing copies (`ld` -> `sw` truncation is 113 pairs on
sqlite alone).  At 4 codepoints this is ~200 pairs/cp — the best return
measured in this project.

Both fit inside the 106 spare codepoints with room left over.  Neither has
been drawn into `encoding.yaml`; these are measurements, not decisions.

### Candidate 5 (mem-copy): the design space, measured — and BUILT as B

Baseline 27192 / 40851.  Every row is a scheduler run.  The frame is
`load tmp, k*imma(rs1a)` then `store tmp, k*immb(rs1b)` with tmp dead: two
bases and two width-scaled offsets fill the 20-bit budget exactly.

| option | ops | offsets | codepoints | musl | sqlite | net pairs | pairs/cp |
|---|---|---|--:|--:|--:|--:|--:|
| A | width-matched diagonal | 5b | 4 | +242 | +362 | +604 | 151 |
| **B — BUILT** | width-matched diagonal | 6b | **16** | +314 | +647 | **+961** | **60** |
| C | full 4x4 cross | 5b | ~32 | +313 | +541 | +854 | 27 |
| D | full 4x4 cross | 6b | ~112 | +386 | +851 | +1237 | 11 |
| ceiling | full cross | unbounded | n/a | +827 | +901 | +1728 | — |

CORRECTION: an earlier version of this table labelled C and D as the
"diagonal" and priced them at 4 and 16 codepoints.  The scratch rules behind
those two runs never enforced width matching, so they were measuring the full
cross product, whose op list costs 28-32 codepoints before any widening and
~112 with it.  A and B are the real diagonal, measured after the frame was
drawn.  The recommendation survives the correction; the numbers under it did
not.

Two facts decide it.  **The offset width binds, not the op set**: uncensored
the population is 859 (musl) / 968 (sqlite) sites, of which only 39% / 62%
have both scaled offsets inside five bits, 47% / 95% inside six, 63% / 98%
inside seven.  **Copies preserve width** 96% / 79% of the time, so the
diagonal captures nearly everything; the truncating tail (`ld` -> `sw`, 122
pairs on sqlite) is worth ~250 pairs for ~28 extra codepoints (~9/cp), close
enough to the portfolio floor to leave out.

B is the knee: the marginal 12 codepoints over A buy 357 pairs (30/cp,
5x the floor) and take sqlite -- the corpus furthest from parity -- from 62%
to 95% capture.  Seven bits would cost 64 codepoints for perhaps 150 more.

One structural alternative, unmeasured: a SAME-BASE variant (`rs1a == rs1b`)
frees five bits for the offsets.  It splits the corpora hard -- 58% of musl's
copies share a base against 5% of sqlite's -- so it would serve struct-copy
code and do nothing for sqlite.

## alu-alu-chain resized to 11 ops per axis (2026-08)

The frame was the roster's worst value: 256 codepoints for 5090 pairs across
musl-rv32 + sqlite-rv64 + cpp-rv64, 19.9 per codepoint.  Block sizes are
exact squares (8^2 = 64, 11^2 = 121 <= 128, 16^2 = 256), so the axes hold 8,
11 or 16 ops of weight (`addi` counts 2 for its 6-bit immediate).

| ops/axis | block | census pairs (3 corpora) | pairs/cp |
|--:|--:|--:|--:|
| 8 | 64 | 3755 (74%) | 58.7 |
| **11** | **128** | **4500 (88%)** | **35.2** |
| 16 | 256 | 5090 (100%) | 19.9 |

**Two 8x8 blocks beat one 11x11 at the same cost**, because the population
splits into two vocabularies that barely interact and a single square pays
for every cross term between them — the same argument as decoupling A from B,
one level up.  Census over three corpora: 94% against 88%.  Rectangle-cover
alternatives at 128 codepoints, measured by census:

| plan | cp | covers | pairs/cp |
|---|--:|--:|--:|
| one 11x11 | 121 | 4500 (88%) | 37.2 |
| **two 8x8** | **128** | **4775 (94%)** | **37.3** |
| 8x8 + 8x4 + 4x8 | 128 | 4815 (95%) | 37.6 |
| 8x8 + 4x4 x4 | 128 | 4866 (96%) | 38.0 |
| 16x4 + 4x16 | 128 | 4182 (82%) | 32.7 |
| 31 x 2x2 (unconstrained greedy) | 128 | 5014 (99%) | 39.2 |

The unconstrained greedy reaches 99% only by splintering into 31 tiny
rectangles pinned to individual hot idioms — the shape most likely to fail on
a fourth corpus, and 31 decode groups against the decoder-alignment objective
(A1.10).  Extreme aspect ratios (16x4) are actively bad: they spend width on
ops with few partners.  Two squares is the knee.

The blocks have distinct identities:

* **Block 1, arithmetic/shift** (3755 pairs, 59/cp): A `add addi andi slli
  sltu srliw sub` -> B `add addi and or slli srli sub`.  Address and index
  work: `slli->add` 476, `srliw->addi` 453, `addi->srli` 288.
* **Block 2, logical/compare** (1020 pairs, 16/cp): A `addi and or sltiu srli
  xor xori` -> B `add addi andi or slli sltiu xor`.  Predicate and bitfield
  work: `or->or` 124, `xori->add` 124, `sltiu->or` 72.

Only `addi`, `or` and `srli` appear in both A sets, and the two top-pair
lists share no entry.  6% of the population (315 pairs) falls outside both;
`addw` and `maxu` become unreachable in either slot, and `sltu`/`srliw`/`xori`
are producer-only.

MEASURED, all three corpora scheduled end to end (total pairs, and the
frame's own hits):

| config | block | musl-rv32 | sqlite-rv64 | cpp-rv64 | total vs 16x16 | frame hits |
|---|--:|--:|--:|--:|--:|--:|
| 16/axis | 256 | 27506 | 41498 | 84439 | — | 5090 |
| **two 8x8, 3-corpus fit (SHIPPED)** | **128** | 27436 | 41434 | pending | **-134 so far** | 2453* |
| 11/axis, 3-corpus fit | 128 | 27385 | 41389 | 84196 | -473 | 4655 |
| 11/axis, 2-corpus fit | 128 | — | — | 83595 | (cpp -844) | — |
| 8/axis, 2-corpus fit | 64 | 27282 | 41261 | 83432 | (-1468) | — |

The shipped configuration keeps **91% of the frame's hits for 50% of its
codepoints**, and the 128 codepoints given up were earning 3.7 pairs each —
under the floor near 6, so the cut is correct.

As the marginal cost of KEEPING space: 64 -> 128 buys 745 census pairs
(11.6/cp), 128 -> 256 buys 590 (4.6/cp).  Deflated by the ~0.7
census-to-measured factor seen throughout this project those are roughly 8
and 3 against a portfolio floor near 6 — so **128**.  Measured on
musl-rv32 + sqlite-rv64 the 11/axis frame costs 230 pairs against the full
block, i.e. the top 128 codepoints were earning 1.8 per codepoint.

**A two-corpus fit is not safe here — this was caught, not predicted.**  An
8/axis set fitted on musl-rv32 + sqlite-rv64 alone was measured, shipped, and
then failed validation on cpp-rv64: it cost 1007 pairs there (1.2% of the
corpus's total) against 461 across the other two combined.  The cause is
`srliw`, C++'s second heaviest A op at 456 of 2588 — an RV64 W-form shift
that cannot appear on RV32 at all and is rare in sqlite, so neither fitting
corpus could see it.  `andi` (232) and `xori` (118) are smaller versions of
the same trap.  That set covers just 66% of the three-corpus population
against 88% for the set fitted on all three.

**The axes are decoupled, worth ~8%.**  The best SYMMETRIC set of equal
weight reaches 1691 census pairs against 1830 for the asymmetric pair (two
corpora), because a symmetric set must carry `sltu` (only ever a producer)
and `or` (only ever a consumer), wasting a slot on each axis.

**What `sltu` feeds.**  Nearly every occurrence is spelled `snez`
(`sltu rd, x0, rs`) or `seqz` (`sltiu rd, rs, 1`), so its single bit is a
PREDICATE, spent three ways: 52% into `add`/`addi` (branchless conditional
increment, `count += (x < y)`), 31% into `or`/`and`/`xor` (combining
predicates), 14% into `slli` (shifting the flag into a bit position).  A
comparison is never a consumer, which is exactly why the asymmetry pays.

### Re-optimising on uniquely-owned value changes nothing

Attributed hits overstate a frame's worth: some of its pairs would be
re-formed elsewhere, or its instructions would find other partners.  Two
measurements separate the notions.

* **Exact-pair uniqueness: 100%.**  Every pair alu-alu-chain wins is a shape
  no other rule accepts — the rule set is disjoint at the pair level.
* **Schedule-level uniqueness: 58%.**  Deleting the frame outright costs
  1455 packets, not 2502 (two corpora), because the freed instructions
  re-pair with DIFFERENT neighbours.

Re-running the op-set search weighted by that recoverability (a pair counts
only insofar as its instructions end up solo once the frame is gone) returns
**exactly the same sets**, covering 73% of unique value just as they covered
73% of attributed pairs.  The recoverable mass is spread proportionally
across the op mix rather than concentrated in particular ops, so the choice
is not an artifact of counting attributed hits.
