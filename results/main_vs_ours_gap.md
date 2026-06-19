# Gap Analysis: main branch vs claude/beautiful-dijkstra-AqK0r

Comparison method: run both schedulers on the same assembly file, extract
(function, instruction_A, instruction_B) pairs from each, match as unordered
pairs within the same function.  Branch-target collisions within large
functions (same andi/beqz pattern N times, differing only by .Lbranch_XXXX)
inflate the "missed" count for `bit_branch_rsd`/`bit_branch_chain` — those
are measurement artefacts, not rule gaps.

---

## testcase0.s (Rust, 22k insns)

| Main rule | Main pairs | Matched by ours | True gap | Examples |
|---|---|---|---|---|
| adjacent_store_pair | 495 | 0 | **495** | `sw s0,120(sp)` / `sw s1,116(sp)` |
| post_increment | 435 | 0 | **435** | `sw s0,8(sp)` / `addi s0,sp,16` |
| load_branch_chain | 422 | 0 | **422** | `lw a0,-104(s0)` / `bnez a0, label` |
| op_pair | 305 | 0 | **305** | `mv s0,a0` / `mv s1,a1` |
| adjacent_load_pair | 275 | 0 | **275** | `lhu a3,1282(s3)` / `lhu a2,1284(s3)` |
| bit_branch_rsd | 506 | 255 | **~251** (target-collision artefact) | — |
| op_pair_chain | 198 | 0 | **198** | `slli a2,a0,0x4` / `srli a2,a2,0x1c` |
| pre_increment | 179 | 0 | **179** | `addi sp,sp,-16` / `sw ra,12(sp)` |
| addr_chain | 163 | 0 | **163** | `add a4,a2,a0` / `lbu a4,0(a4)` |
| arith_jump | 147 | 0 | **147** | `li a5,5` / `j label` |
| dual_arith | 143 | 0 | **143** | `andi a0,a0,15` / `li a1,10` |
| mv_load_jump | 98 | 0 | **98** | `mv a1,s9` / `j label` |
| arith_mem | 96 | 0 | **96** | `addi t0,t0,-24` / `sb t2,0(t1)` |
| addi_branch | 75 | 0 | **75** | `li a1,18` / `bne a0,a1, label` |
| cmp_branch_chain | 67 | 0 | **67** | `li a3,10` / `bltu a2,a3, label` |
| load_arith_chain | 37 | 0 | **37** | `lw a2,-108(s0)` / `or a2,a2,s1` |
| li_branch_chain | 34 | 0 | **34** | `li a1,-113` / `bltu a1,a0, label` |
| dual_arith_chain | 23 | 0 | **23** | `sub s4,a3,a0` / `addi s5,s4,4` |
| arith_branch | 21 | 0 | **21** | `or a2,a2,a3` / `bnez a2, label` |
| bit_branch_chain | 37 | 14 | **~14** (artefact) | — |
| load_mem_chain | 14 | 0 | **14** | `lw a1,-432(s0)` / `lw s10,12(a1)` |

---

## godot.s (C++, 98k insns)

| Main rule | Main pairs | Missed by ours | Examples |
|---|---|---|---|
| post_increment | 3337 | **3303** | `sd ra,8(sp)` / `addi s0,sp,16` |
| load_mem_chain | 1379 | **1350** | `ld a1,16(sp)` / `ld a0,0(a1)` |
| addr_chain | 1356 | **1334** | `addi s1,a0,-850` / `lb a0,8(s1)` |
| dual_arith | 1325 | **1222** | `andi sp,sp,-16` / `li a4,0` |
| op_pair | 2187 | **1092** | `mv a5,a0` / `li a3,0` |
| adjacent_store_pair | 1329 | **1069** | `sd a0,0(sp)` / `sd a2,8(sp)` |
| mv_load_jump | 714 | **714** | `ld a0,8(sp)` / `jalr a4` |
| pre_increment | 658 | **654** | `addi sp,sp,-32` / `sd ra,24(sp)` |
| arith_jump | 746 | **538** | `addi sp,sp,16` / `j target` |
| load_branch_chain | 512 | **271** | `ld a5,-946(a5)` / `beqz a5, label` |
| cmp_branch_chain | 710 | **236** | `li a2,1` / `bne a1,a2, label` |
| adjacent_load_pair | 1464 | **170** | `ld s0,64(sp)` / `ld s1,56(sp)` |
| arith_mem | 114 | **113** | `add a1,a1,a5` / `sd ra,8(sp)` |
| load_arith_chain | 347 | **76** | `ld a0,-16(a0)` / `and a0,a0,s0` |
| op_pair_chain | 72 | **72** | `slli a2,s2,0x2` / `sub a1,a1,a2` |

---

## Priority ranking (combined godot + testcase0 gap)

| Rule to implement | Combined gap | Notes |
|---|---|---|
| post_increment | ~3,738 | store then addi sp,sp,N (prologue/epilogue) |
| pre_increment | ~833 | addi sp,sp,-N then store (prologue) |
| addr_chain | ~1,497 | arith → mem using result as address |
| dual_arith | ~1,365 | two independent ALU ops sharing rs1 or rs2 |
| op_pair | ~1,397 | two independent ALU ops (no shared input) |
| adjacent_store_pair | ~1,564 | same-base consecutive stores |
| load_mem_chain | ~1,364 | load → deref of loaded pointer |
| mv_load_jump | ~812 | mv/load then jump/call |
| arith_jump | ~685 | arith then indirect/direct jump |
| load_branch_chain | ~693 | load then branch on result |
| adjacent_load_pair | ~445 | same-base consecutive loads |
| cmp_branch_chain | ~303 | li then conditional branch |
| op_pair_chain | ~270 | shift-chain (slli→srli or similar) |
| arith_mem | ~209 | arith then mem to different addr |
| load_arith_chain | ~113 | load then ALU consuming it |

> Note: `op_pair` and `dual_arith` have overlap — dual_arith requires shared rs1/rs2,
> op_pair is fully independent.  `cmp_branch_chain` in main is the same as our
> `li-branch-pair` but with different immediate-range constraint.  Some of our
> existing rules (load-sp-branch, load-base-branch) cover `load_branch_chain`
> partially (and outperform main on that rule).

---

## Rules we outperform main on

| Rule (ours) | Our pairs (godot) | Main equivalent | Main pairs |
|---|---|---|---|
| load-sp-branch | 834 | load_branch_chain (partial) | 512 total |
| load-base-branch | 539 | load_branch_chain (partial) | 512 total |

