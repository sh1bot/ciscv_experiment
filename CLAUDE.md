# CLAUDE.md

RISC-V instruction-pairing workbench: reads RISC-V asm, packs instruction pairs
into 32-bit packets, emits annotated asm + stats. Design in `GOALS.md` / `PLAN.md`;
open items in `TODO.md`; measurement conventions in `ACCOUNTING.md`.

**`encoding.yaml` is the source of truth for the prospective packet ISA** — the
frames (op clusters + templates + row layout), immediate widths, and codepoint
budget live there, and it is the single point of iteration (render with
`python3 util/encoding_render.py`; see `yaml_migration.md` for the migration).
`scheduler/rules.py` is the runtime enforcement of those frames, and each
rule's scheduler-side semantics (deadness, chaining, order-sensitivity) are
documented at its definition there; numeric limits are yaml-owned, and
`tests/test_conformance.py` gates yaml/rules agreement, yaml structure, and
`encoding.md` regeneration on every commit. The migration is in progress —
`rules.py` still hand-copies widths that `scheduler/imm_contracts.py` can
derive (TODO A8).

`python3 util/encoding_assign.py` assigns the opcode bit-patterns and emits the
published `ciscv-proto.yml` data file: per frame, the ASCII-art layout with the
identifier filled in, the opcode tables, and the bit-level meaning of every
op-select (`o`) bit. `--text` gives the human report instead, `--decode WORD`
resolves a single selector word.

## Measurement caveats — remind the user about these when relevant

- **RVC-eligibility (`[C]` / `rvc_eligible`) is an OPTIMISTIC ceiling, not actual
  compression.** Branch/jump offset ranges are NOT checked and there is no
  RV32/RV64 gating (PLAN §5). So on a real (already-compressed) binary the count
  is HIGHER than the literal `c.*` opcodes: far `jal zero`/`beq,zero`/`bne,zero`
  are counted as `c.j`/`c.beqz`/`c.bnez`-eligible even though their displacement
  is too large to actually compress. If RVC counts look too high, this is why.
- **Float RVC** (`c.flw`/`c.fld`/`c.f*sp`) is deliberately out of scope (PLAN §5),
  so those literal `c.*` are NOT counted as eligible (slight under-count).
- `[C]`/`[?]` markers are emitted on SOLO instructions only (by design) — a
  "missed something compressible" signal; paired instructions never show them.

## Answering "what could pair with X?"

Use `util/anchor_scan.py` (built on `analysis/anchors.py`). **Never answer a
candidate-frame question from adjacency** — the instruction next to an anchor
is not the instruction that could be there. The tool corrects for all three
biases at once: it scores against the real scheduled and paired stream, skips
candidates another frame has already taken, and lets the scheduler reorder a
candidate down to the anchor. It scores operand SHAPES rather than mnemonics,
because a frame chooses its op set and the question is what is encodable.

    python3 util/anchor_scan.py cpp-rv32 --anchor call --budget 10
    python3 util/anchor_scan.py cpp-rv32 --fixed 'mv rd5,rs5' 'li rd3,imm7'

The first run per corpus pays for the schedule (~30 s on musl, minutes on
cpp); it caches the annotated stream under `results/cache/` and every run
after that is ~0.3 s. The cache key is the content of the parser, scheduler,
pairer, rules and yaml, so it invalidates itself when any of them changes —
never hand-clear it, and never trust a hand-rolled adjacency script instead.

## Conventions

- Develop on the designated feature branch; keep `main` synced when asked.
- Run tests with `python -m pytest tests/ -q`.
