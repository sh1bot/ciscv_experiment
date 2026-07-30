# Zicond: where the select idiom splits into packets

Answers ACCOUNTING §10 item 9 ("Zicond looks worth its own frame with its own
partner set") and prices the *front* half of `results/corpus/FINDINGS.md` §4
candidate 4.

Two new tools, run over all eight corpora:

- `analysis/zicond_select.py` — finds the idiom in the emitted order, classifies
  its shape, and counts the register fields each candidate packet would draw.
- `analysis/zicond_realize.py` — runs the real pipeline (liveness → dependence
  graph → list schedule → greedy pair) with candidate rules appended to `RULES`,
  so the reorderer may bring the pieces together and the greedy pairer chooses
  between rival partners. Nothing in `scheduler/rules.py` or `encoding.yaml` is
  touched; the candidate rules live in the analysis module.

---

## The question

The compilers emit a conditional move as four instructions:

```
<setup>    rc, ...            # condition into rc
czero.eqz  t0, v0, rc
czero.nez  t1, v1, rc
or         rd, t0, t1
```

Candidate 4 (`czero-select-or`) takes the back half, `(czero.nez, or)`, for 2
codepoints, and `[czero, czero]` was refused there for needing five register
fields. The proposal measured here splits the idiom into two tuples instead:

```
packet A:  setup rc, ...        ;  czero.eqz t0, v0, rc
packet B:  czero.nez t1, v1, rc ;  or rd, t0, t1
```

Packet B is candidate 4 unchanged. Packet A is the new claim.

## Answer

**The front tuple captures 2.2% of select sites at the adjacency ceiling and
1.3% for both tuples together, against 84.5% for the back tuple alone.** Run
through the real scheduler — which can reorder, and which also reaches czeros
outside a select — it is worth **+796 pairs pooled** (+481 excluding the two C++
corpora) on top of candidate 4's +7050, for an estimated 12–16 codepoints
against candidate 4's 2.

It is not the cheap symmetry it looks like. Three independent things hold it
down, and a fourth makes a *different* A-slot partner the better buy.

---

## 1. The condition is rarely next to the czero

Pooled over 10657 select sites, distance from the condition's definition to the
first czero:

| origin | sites | share |
|---|--:|--:|
| 2 instructions back | 3696 | 34.7% |
| block entry (argument, or another block) | 2124 | 19.9% |
| 3 back | 1343 | 12.6% |
| **adjacent** | **1257** | **11.8%** |
| 7 back | 1062 | 10.0% |
| 4–12 back | 1175 | 11.0% |

The dominant spacing is one instruction, and that instruction is usually the
`li`/load that materialises a select arm. As emitted, the four are contiguous in
the required order in only **6.9%** of sites (shape breakdown: 85.2% back-only,
6.9% four-contiguous, 6.0% czeros-adjacent, 1.9% scattered).

Adjacency is not a hard bound — the arm materialisations are independent of the
condition, so the list scheduler can hoist them — which is why the realized
figure below beats this ceiling. But it is where the idiom starts.

## 2. Two thirds of the sites that *are* adjacent do not fit

`rc` is read by the second czero and `t0` by the `or`, so both cross the packet
boundary: neither is an unencoded chain temp. Packet A must draw rc, the setup's
own sources, and the czero's `v0`/`t0`. The grid has exactly four 5-bit register
columns.

| fields packet A needs | sites | share |
|---|--:|--:|
| 3 + immediate | 4654 | 54.5% |
| 4 + immediate | 1687 | 19.8% |
| **4** | **1431** | **16.8%** |
| 5 | 573 | 6.7% |
| **3** | **180** | **2.1%** |
| 2 | 8 | 0.1% |

Only 19.0% fit. The immediate is what kills it, and one op is why: **`andi` is
58.0% of all condition setups** (4946 of 8533) — the mask-then-test idiom — and
its immediate has nowhere to live once four registers are drawn. `slti` adds
another 5.8%. A four-register packet has 10 bits left, and those 10 bits *are*
the codepoint namespace.

A fifth register field is not impossible, just priced out: it has to eat
`funct3:g:h`, so every (opA, opB) combination claims 32 of the 1024 codepoints —
64 for the two czero variants alone, against 130 spare.

Of the 236 sites that do fit, the A-slot op list is small and cheap:

| setup op | sites | share |
|---|--:|--:|
| sltu | 72 | 30.5% |
| slti (imm 0, i.e. `sltz`) | 60 | 25.4% |
| or | 30 | 12.7% |
| slt | 26 | 11.0% |
| xor | 12 | 5.1% |
| sgtz | 10 | 4.2% |
| and | 10 | 4.2% |
| loads / sext.w / andn | 16 | 6.8% |

Seven forms × two czero variants ≈ **14 codepoints** (loads excluded — an offset
would need bits the packet does not have).

## 3. The condition usually outlives the select

The one lever that would change the arithmetic is putting `rc` in a fixed
architectural register instead of an encoded field — the cross-packet cousin of
the chain temp (TODO decision 4). Then a three-operand setup like
`slt rc, ra, rb` would fit comfortably:

| | sites | share |
|---|--:|--:|
| condition dies with the select | 3476 | 32.6% |
| condition live past the select | 7181 | 67.4% |

Only a third of sites could host it, and 32.4% of sites share one condition
register across four czeros (two selects on the same predicate), which is
exactly the population that keeps it live. Restricted to the third that do die,
the front half becomes 2+imm (51.6%) or 3+imm (28.0%) — comfortable — so this is
a real lever, but it needs the cross-packet-temp semantics decided first, and it
addresses at most a third of an already thin population.

## 4. The rival for the same slot is worth more

The A slot of the first czero has another claimant: the instruction that
materialises the value being masked.

```
li v0, imm  ;  czero.eqz t0, v0, rc
```

Here `v0` dies at the czero (or is overwritten by it — 58% of czeros are RSD
form), so it *is* an unencoded chain temp: the packet draws rc, t0, and the arm
op's own sources. This is `chain-alu-pair`'s shape with a czero in the B slot,
and it is available at **18.1%** of sites against the condition chain's 2.2%
(20.3% of sites have a front partner of one kind or the other).

It cannot simply join `chain-alu-pair`: that frame is a 16×16 cross product at
exactly its 256-codepoint block, so two more B-slot ops take it to 288 and the
block doubles to 512. It needs its own frame. The A-slot list, over the 1925
sites that fit:

| arm op | sites | share |
|---|--:|--:|
| li | 397 | 20.6% |
| slti | 337 | 17.5% |
| maxu | 328 | 17.0% |
| add | 138 | 7.2% |
| sltu | 136 | 7.1% |
| slt | 124 | 6.4% |
| addi | 64 | 3.3% |
| or | 71 | 3.7% |
| lw / ld | 108 | 5.6% |
| sub / clz | 60 | 3.1% |

94.3% of these keep every encoded register inside `chain-alu-pair`'s x0–x15
window, so that constraint is not what would bind.

The immediate is affordable here in a way it never is for the condition chain:
with only two register fields drawn, it can occupy the two spare 5-bit columns,
so **10 bits of immediate cost nothing** in codepoints. Bits beyond the spare
columns come out of the op namespace and double the frame's cost per bit — which
is what `chain_alu`'s 6-bit `addi` (5 column bits + 1 borrowed, 2 codepoints)
already pays — so both tools hold the A slot to the room its register count
leaves: 10 bits at two fields, 5 at three, none at four. That check is why `addi`
falls from 366 eligible sites to 64: the wide constants are address arithmetic.

---

## Realized pair deltas

`analysis/zicond_realize.py`, four cumulative configurations. Marginals are the
difference between consecutive rows.

| corpus | base pairs | +back | +cond | +arm | czero paired |
|---|--:|--:|--:|--:|---|
| godot | 13438 | +70 | +45 | +8 | 39% → 55% → 60% |
| testcase0 | 4156 | 0 | 0 | 0 | no czero in corpus |
| musl-rv32 | 27351 | +375 | +66 | +84 | 41% → 46% → 53% |
| musl-rv64 | 21844 | +285 | +48 | +71 | 40% → 45% → 52% |
| sqlite-rv32 | 45264 | +773 | +176 | +118 | 41% → 49% → 54% |
| sqlite-rv64 | 42339 | +499 | +146 | +105 | 41% → 51% → 58% |
| cpp-rv32 | 88536 | +2623 | +156 | +1504 | 44% → 46% → 64% |
| cpp-rv64 | 83547 | +2425 | +159 | +1383 | 45% → 47% → 63% |
| **pooled** | | **+7050** | **+796** | **+3273** | 0 → 43% → 47% → 61% |

Reading it:

- **Candidate 4 is confirmed and bigger than estimated.** Excluding the two C++
  corpora (which FINDINGS §4 did not have) the back half realizes **+2002**
  pairs, against that section's ~1.9k estimate — an independent reproduction.
  With cpp it is +7050.
- **The front tuple adds 6–29% on top of the back half** (godot's 64% is the
  outlier with 272 czeros in total). On the two corpora with the most Zicond it
  adds 6%.
- The front tuple realizes *more* than its as-emitted ceiling (796 realized vs
  236 sites) for two reasons: the list scheduler creates adjacency that the
  emitted order did not have, and the rule also reaches czeros that are not part
  of a select (7.3% of all czeros).
- **The arm chain beats the condition chain 4.1:1 pooled — but only on the C++
  corpora.** Excluding cpp the condition chain is slightly *ahead* (+481 vs
  +386). Two thirds of the whole Zicond population lives in those two corpora,
  so which frame looks better is a corpus-weighting decision, not a fact.
- `cond` and `arm` barely compete: adding `arm` leaves every `cond` hit intact
  (161/159 before and after) while taking 6–7% off `back`'s attribution.

## Complementary czero.eqz + czero.nez — refused, but not for the field count

The stated objection is five register fields. That is only true of the general
form: **37.4% of select sites have both czeros in RSD form** (`czero.eqz t,t,rc`),
where the two collapse to three fields — 15 bits, cheaper than candidate 4.

The frame still fails, for a better reason: it wins nothing. In 85.2% of sites
the czeros and the `or` are already contiguous, so pairing `(czero, czero)`
leaves the `or` solo and takes the same three instructions to two packets that
`(czero, or)` does — while foreclosing the first czero's own A-slot partner.
Where a front partner exists, `[x, cz0][cz1, or]` is two packets and
`[x][cz0, cz1][or]` is three. It is never better and sometimes worse.

## Recommendation

1. **Adopt candidate 4** (`czero-select-or`, 2 codepoints, +7050 / +2002 ex-cpp).
   Unchanged conclusion, now measured through the scheduler rather than by
   adjacency count, and reproduced against the earlier estimate.
2. **If a czero A-slot partner is bought, buy the arm chain first** — ~16–24
   codepoints for +3273 (+386 ex-cpp), and it needs no new semantics. Its case
   rests on the C++ corpora; on C and the rest it is no better than (3).
3. **The condition chain (this proposal) is real but marginal**: ~14 codepoints
   for +796 (+481 ex-cpp). Both front frames together are still a worse ratio
   than FINDINGS candidates 3 and 5, so they should queue behind them.
4. Revisit (3) only if the cross-packet fixed-register question (TODO decision 4)
   is settled in favour of a fixed condition register, which would let
   three-operand setups in and lift the 19.0%-fit figure.

Prerequisite for any of it: `czero.eqz`/`czero.nez` are decoded (correctly, as
`rd, rs1, rs2`) and are *not* flagged `is_unknown`, so they pair without a
parser change — but they appear in no frame today, which is why the base
configuration pairs 0 of 23001 czeros.

## Caveats

- **The corpora disagree by 6.6× on Zicond density** — 1.98% of instructions on
  cpp-rv32 (8350 czeros) against 0.30% on godot (272) and none at all on
  testcase0 — and the two C++ corpora carry 72% of the
  population. Every pooled number here is dominated by them, and it is the
  corpora that most separate the arm chain from the condition chain. Nothing
  here is evidence about a target that does not emit czero at that rate.
- Per ACCOUNTING §6, the concentration check passes comfortably: the top
  contributing function owns 44 of 10657 sites (0.4%).
- The arm chain refuses auipc-fed `addi`s — their immediate is a resolved
  `%pcrel_lo` relocation, the same policy the load frames apply — and holds every
  A-slot immediate to the columns its registers leave free. Both matter, and by
  a lot: on sqlite-rv32 the arm marginal measures **+229** with neither check,
  **+140** with the relocation refusal and a flat 11-bit immediate cap, and
  **+118** as reported. The discarded pairs would have been encoding a
  relocation, or a constant wider than the packet, into a field that cannot hold
  it.
- Realize baselines are exact: `musl-rv64` 21844 pairs and `sqlite-rv64` 42339 /
  147338 packets match `python3 __main__.py` at HEAD to the instruction. The
  figures in `results/corpus/scheduler-runs.txt` predate later rule commits
  (sqlite-rv64 41631, musl-rv64 21874) and should be regenerated before being
  quoted as a baseline again.
- Branch/jump optimism does not apply here — no frame in this analysis holds a
  displacement.
