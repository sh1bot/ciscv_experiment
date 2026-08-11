# Slot A operand shape -- opcode5-partitioned encoding

rs1/rs2/imm readiness ("shape") for the A slot, resolved dynamically
from encoding.yaml, partitioned across the packet's opcode5 field the
same way real RISC-V's opcode[6:2] determines instruction FORMAT before
funct3 picks the specific operation. rd is out of scope. See
`util/opcode_shape.py`'s module docstring for the full methodology.

723 codepoints catalogued across 25 frames.

opcode5 values used: 25/32

## Frame index

| frame | does | A-shape(s) | B-shape(s) | weight |
|---|---|---|---|---|
| `rsd-alu-pair` | Two in-place ALU updates, each rewriting its own source register. | RI/RR | RI/RR | 256 |
| `alu-alu-chain` | Two ALU operations, the second consuming the first's result. | RI/RR | RI/RR | 128 |
| `load-alu-chain` | Load a value and immediately compute with it. | RI | RI/RR | 64 |
| `li-branch-chain` | Compare a register against a constant and branch. | RI | RRI | 48 |
| `alu-store-chain` | Compute a value and store it. | RI/RR | RRI | 32 |
| `bit-test-branch-chain` | Test a bit or bit-field and branch on the result. | RI | RI/RRI | 24 |
| `setup-jump-pair` | Set up an argument or return value, then transfer control. | RI | RI | 20 |
| `load-store-chain` | Copy a value from one memory location to another. | RI | RRI | 16 |
| `inc-branch-pair` | Step a loop counter by one and branch on the comparison. | RI | RRI | 16 |
| `arg-call-pair` | Set up an argument, then call through a hard-coded base register. | RI/RRI | RI | 16 |
| `load-base-branch-pair` | Load a value and branch on whether it is zero; the value survives. | RI | RI | 14 |
| `dual-setup-pair` | Two independent small moves or constants -- argument marshalling. | RI | RI | 14 |
| `macro-op-pair` | Both halves of ONE computation over the same operands (mul/mulh, div/rem), declared as a pair so an implementation can fuse them. | RR | RR | 9 |
| `index-mem-chain` | Scaled-index addressing: compute `base + i*width` and access it. | RR | RI/RRI | 8 |
| `pre-inc-pair` | Advance a pointer, then access through it (pre-increment). | RI/RR | RI/RRI | 8 |
| `mem-base-pair` | Two adjacent accesses through one base register, one data width apart. | RI/RRI | RI/RRI | 8 |
| `addi-store-off-chain` | Compute a value from one base and store it at an offset from another. | RI | RRI | 8 |
| `load0-load10-chain` | Pointer chase: bare first load, the second carries a wide offset. | RI | RI | 7 |
| `load5-load5-chain` | Pointer chase with BOTH loads offset: a pointer in a slot, then indexed. | RI | RI | 7 |
| `load-sp-branch-pair` | Load a stack slot and branch on whether it is zero; the value survives. | RI | RI | 6 |
| `post-inc-pair` | Access through a pointer, then advance it (post-increment). | RI/RRI | RI | 4 |
| `addi-store-chain` | Form a value -- constant, copy or sp-relative address -- and store it. | RI | RRI | 4 |
| `mem-sp-pair` | Two adjacent stack accesses one word apart -- a spill or reload pair. | RI/RRI | RI/RRI | 2 |
| `li-czero-chain` | Materialise a constant and conditionally zero it -- one arm of a select. | RI | RR | 2 |
| `czero-or-chain` | Finish a conditional select: merge the surviving arm into the result. | RR | RR | 2 |

## A-shape partition

### RI -- opcode5=0xxxx

block=16/32 values (512 codepoints, 15 spare exact / -81 spare buddy-rounded), content=497 (buddy-rounded sub-alloc overflows too)

```
alu-alu-chain            weight=  80  sub-id=000
rsd-alu-pair             weight= 128  sub-id=001
li-branch-chain          weight=  48  sub-id=0100
load-alu-chain           weight=  64  sub-id=0101
bit-test-branch-chain    weight=  24  sub-id=01100
setup-jump-pair          weight=  20  sub-id=01101
alu-store-chain          weight=  16  sub-id=011100
arg-call-pair            weight=  12  sub-id=011101
dual-setup-pair          weight=  14  sub-id=011110
inc-branch-pair          weight=  16  sub-id=011111
load-base-branch-pair    weight=  14  sub-id=100000
load-store-chain         weight=  16  sub-id=100001
addi-store-off-chain     weight=   8  sub-id=1000100
load-sp-branch-pair      weight=   6  sub-id=1000101
load0-load10-chain       weight=   7  sub-id=1000110
load5-load5-chain        weight=   7  sub-id=1000111
addi-store-chain         weight=   4  sub-id=10010000
mem-base-pair            weight=   4  sub-id=10010001
pre-inc-pair             weight=   4  sub-id=10010010
li-czero-chain           weight=   2  sub-id=100100110
post-inc-pair            weight=   2  sub-id=100100111
mem-sp-pair              weight=   1  sub-id=1001010000
```

### RR -- opcode5=10xxx

block=8/32 values (256 codepoints, 41 spare exact / 18 spare buddy-rounded), content=215

```
rsd-alu-pair             weight= 128  sub-id=0
alu-alu-chain            weight=  48  sub-id=10
alu-store-chain          weight=  16  sub-id=1100
macro-op-pair            weight=   9  sub-id=1101
index-mem-chain          weight=   8  sub-id=11100
pre-inc-pair             weight=   4  sub-id=111010
czero-or-chain           weight=   2  sub-id=1110110
```

### RRI -- opcode5=11000

block=1/32 values (32 codepoints, 21 spare exact / 21 spare buddy-rounded), content=11

```
arg-call-pair            weight=   4  sub-id=00
mem-base-pair            weight=   4  sub-id=01
post-inc-pair            weight=   2  sub-id=100
mem-sp-pair              weight=   1  sub-id=1010
```

## Slot B operand shape, independent of A (marginal)

```
RI     weight= 425  (58.8%)
RR     weight= 149  (20.6%)
RRI    weight= 149  (20.6%)
```

If B got first pick of its own 32-value field (a reference point,
not something that physically exists): 32/32 values

## funct3 partition and contention

funct3 is ALWAYS B's shape field, full stop -- opcode5's
leftover bits and g/h are only for whatever a funct3 code's own
content doesn't fit, not for B's shape itself. Modeled as a real
2-stage mux: funct3 selects one of 8 codes, each code's own inner
mux is uniformly (opcode5-leftover + g + h) wide. Each B-shape
claims whole codes for its bulk; where two B-shapes both have a
leftover too small alone to justify a full code, they SHARE one
(an asymmetric/split code, marked *) rather than each rounding up
-- the busiest B-shape's own full codes stay simple and uniform,
only the small remainder has to be irregular:

```
A=RI     weight=  497   per-funct3-code capacity=64   6 full + 2 shared* = 8/8 funct3 codes -> fits (0 funct3 code(s) spare)
    B=RI     weight=  282  4 full code(s) + shares a code* (26 left over)  (56.7% of this A-shape)
    B=RRI    weight=  122  1 full code(s) + shares a code* (58 left over)  (24.5% of this A-shape)
    B=RR     weight=   93  1 full code(s) + shares a code* (29 left over)  (18.7% of this A-shape)
A=RR     weight=  215   per-funct3-code capacity=32   5 full + 2 shared* = 7/8 funct3 codes -> fits (1 funct3 code(s) spare)
    B=RI     weight=  137  4 full code(s) + shares a code* (9 left over)  (63.7% of this A-shape)
    B=RR     weight=   56  1 full code(s) + shares a code* (24 left over)  (26.0% of this A-shape)
    B=RRI    weight=   22  0 full code(s) + shares a code* (22 left over)  (10.2% of this A-shape)
A=RRI    weight=   11   per-funct3-code capacity=4   2 full + 1 shared* = 3/8 funct3 codes -> fits (5 funct3 code(s) spare)
    B=RI     weight=    6  1 full code(s) + shares a code* (2 left over)  (54.5% of this A-shape)
    B=RRI    weight=    5  1 full code(s) + shares a code* (1 left over)  (45.5% of this A-shape)
```
