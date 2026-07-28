# CLAUDE.md

RISC-V instruction-pairing workbench: reads RISC-V asm, packs instruction pairs
into 32-bit packets, emits annotated asm + stats. Design in `GOALS.md` / `PLAN.md`;
open items in `TODO.md`; measurement conventions in `ACCOUNTING.md`.

**`encoding.yaml` is the source of truth for the prospective packet ISA** — the
frames (op clusters + templates + row layout), immediate widths, and codepoint
budget live there, and it is the single point of iteration (render with
`python3 util/encoding_render.py`; see `yaml_migration.md` for the migration).
`scheduler/rules.py` is the runtime enforcement of those frames and
`scheduler/RULES.md` documents its scheduler-side semantics (deadness, chaining,
order-sensitivity); numeric limits are yaml-owned. This migration is in progress,
so some docs still describe the older rules-as-source model — see `TODO.md`.

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

## Conventions

- Develop on the designated feature branch; keep `main` synced when asked.
- Run tests with `python -m pytest tests/ -q`.
