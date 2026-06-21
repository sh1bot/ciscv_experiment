# CLAUDE.md

RISC-V instruction-pairing workbench: reads RISC-V asm, packs instruction pairs
into 32-bit packets, emits annotated asm + stats. Design in `GOALS.md` / `PLAN.md`;
pairing rules in `scheduler/RULES.md`; open items in `TODO.md`.

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
