# Pairing Rule Comparison: main vs claude/beautiful-dijkstra-AqK0r

Generated against `tests/testcase0.s` (Rust, 22k insns) and `tests/godot.s` (C++, 98k insns).

"Pairs to RVC parity" = (RVC-eligible / 2) − pairs won.  Each pair compresses
two instructions into one slot; each RVC encoding compresses one instruction from
32-bit to 16-bit.  The ratio is not perfectly equivalent but gives a useful gap
metric on the same instruction count.

---

## testcase0.s

| Metric | main | this branch | delta |
|---|---|---|---|
| Instructions | 22,013 | 21,876 | −137 (scheduler reorder diff) |
| Pairs won | 4,728 | **2,745** pairs = **5,490 insns** | +762 insns paired |
| Paired % | 43.0% of slots | **25.1% of insns** | — |
| RVC-eligible | 8,809 (40.0%) | 8,200 (37.5%) | −609 |
| Pairs to RVC parity | 4,404 − 4,728 = **−324** (ahead) | 4,100 − 2,745 = **+1,355** behind | |

> Note: main counts pairs as slots (2 insns = 1 pair); this branch counts paired
> instructions.  Main wins 4,728 pairs = 9,456 paired instructions vs our 5,490.
> Main is ahead on testcase0 by ~3,966 paired instructions.

### main — rule breakdown (testcase0)
| Rule | Pairs won |
|---|---|
| adjacent_load_pair | 631 |
| op_pair | 617 |
| adjacent_store_pair | 589 |
| bit_branch_rsd | 506 |
| post_increment | 442 |
| load_branch_chain | 425 |
| op_pair_chain | 198 |
| arith_jump | 186 |
| addr_chain | 163 |
| cmp_branch_chain | 91 |
| dual_arith | 226 |
| bit_branch_chain | 37 |
| load_arith_chain | 37 |
| load_mem_chain | 14 |
| cmp_branch_rsd | 4 |
| load_jalr_chain | 3 |
| **Total** | **4,728** |

### this branch — rule breakdown (testcase0)
| Rule | Pairs won |
|---|---|
| dual-op-pair | 1,845 |
| bit-branch-pair | 523 |
| rsd-alu-pair | 145 |
| epilogue-pair | 92 |
| li-branch-pair | 69 |
| chain-alu-pair | 45 |
| load-base-branch | 26 |
| **Total** | **2,745** |

---

## godot.s

| Metric | main | this branch | delta |
|---|---|---|---|
| Instructions | 97,796 | 97,790 | ≈0 |
| Pairs won | 16,468 | **10,140** pairs = **20,280 insns** | +762 insns paired |
| Paired % | 33.7% of slots | **20.7% of insns** | — |
| RVC-eligible | 48,388 (49.5%) | 48,083 (49.2%) | −305 |
| Pairs to RVC parity | 24,194 − 16,468 = **+7,726** behind | 24,042 − 10,140 = **+13,902** behind | |

> Main is ahead on godot by ~12,368 paired instructions (32,936 vs 20,280).

### main — rule breakdown (godot)
| Rule | Pairs won |
|---|---|
| post_increment | 3,337 |
| op_pair | 2,187 |
| adjacent_load_pair | 1,464 |
| load_mem_chain | 1,379 |
| addr_chain | 1,356 |
| adjacent_store_pair | 1,329 |
| dual_arith | 1,325 |
| mv_load_jump | 714 |
| arith_jump | 746 |
| cmp_branch_chain | 710 |
| load_branch_chain | 512 |
| pre_increment | 658 |
| load_arith_chain | 347 |
| arith_mem | 114 |
| op_pair_chain | 72 |
| addi_branch | 49 |
| li_branch_chain | 46 |
| dual_arith_chain | 38 |
| bit_branch_rsd | 35 |
| arith_branch | 32 |
| bit_branch_chain | 16 |
| load_jalr_chain | 2 |
| **Total** | **16,468** |

### this branch — rule breakdown (godot)
| Rule | Pairs won |
|---|---|
| dual-op-pair | 6,167 |
| load-sp-branch | 834 |
| load-chain-alu-pair | 762 |
| epilogue-pair | 582 |
| load-base-branch | 539 |
| li-branch-pair | 521 |
| store-chain-alu-pair | 240 |
| base-chain-load-pair | 204 |
| rsd-alu-pair | 142 |
| bit-branch-pair | 54 |
| deref-chain-load-pair | 42 |
| pre-inc-pair | 27 |
| chain-alu-pair | 26 |
| **Total** | **10,140** |

---

## Gap analysis — what main has that this branch lacks

### godot (biggest gaps)

| Main rule | Main wins | Nearest this-branch equivalent | Notes |
|---|---|---|---|
| post_increment | 3,337 | pre-inc-pair (27) | store/load + stride update; biggest single gap |
| op_pair | 2,187 | dual-op-pair (6,167) | main's op_pair is independent ALU; ours is wider |
| adjacent_load_pair | 1,464 | — | same-base adjacent loads; not yet implemented |
| load_mem_chain | 1,379 | deref/base-chain-load-pair (246) | load→indexed-load chain |
| addr_chain | 1,356 | — | arith→mem using result as base |
| adjacent_store_pair | 1,329 | — | same-base adjacent stores |
| dual_arith | 1,325 | rsd-alu-pair + chain-alu-pair (168) | two ops sharing inputs |
| arith_jump | 746 | — | arith then indirect jump/call |
| mv_load_jump | 714 | — | mv + load + jump |
| cmp_branch_chain | 710 | li-branch-pair (521) | partial overlap |
| load_branch_chain | 512 | load-sp-branch + load-base-branch (1,373) | **we win here** |
| pre_increment | 658 | pre-inc-pair (27) | pre-increment pointer then mem |

### testcase0 (biggest gaps)

| Main rule | Main wins | Notes |
|---|---|---|
| adjacent_load_pair | 631 | Not implemented here |
| op_pair | 617 | Partially covered by dual-op-pair |
| adjacent_store_pair | 589 | Not implemented here |
| bit_branch_rsd + chain | 543 | Covered: bit-branch-pair (523) — close |
| post_increment | 442 | pre-inc-pair (27) |
| load_branch_chain | 425 | load-base-branch (26) — gap here |
| op_pair_chain | 198 | chain-alu-pair (45) |
| arith_jump | 186 | Not implemented here |
| addr_chain | 163 | Not implemented here |

---

## RVC parity summary

| File | RVC ceiling (pairs equiv) | main pairs | this branch pairs | main gap | our gap |
|---|---|---|---|---|---|
| testcase0 | 4,404 | 4,728 ✓ (+324 ahead) | 2,745 | — | **−1,659** |
| godot | 24,194 | 16,468 | 10,140 | **−7,726** | **−14,054** |

This branch has not yet implemented: `post_increment`/`pre_increment`, `adjacent_load_pair`,
`adjacent_store_pair`, `addr_chain`, `load_mem_chain` (general), `arith_jump`, `mv_load_jump`,
`dual_arith` (full shared-input form), `op_pair_chain`.  Those rules together account for the
bulk of the gap on godot.
