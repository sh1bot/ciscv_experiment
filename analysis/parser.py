"""
analysis/parser.py — Two-pass RISC-V assembly parser.

Pass 1: Pre-scan to classify labels as barrier or non-barrier.
Pass 2: Decode instructions and build basic blocks.
"""

from __future__ import annotations
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

from isa.instruction import Instruction
from isa.registers import REG_ALIASES
from analysis.cfg import BasicBlock, Function


# ---------------------------------------------------------------------------
# Minimal instruction decoder
# ---------------------------------------------------------------------------

_CONDITIONAL_BRANCHES = {
    "beq", "bne", "blt", "bge", "bltu", "bgeu",
    "beqz", "bnez", "bltz", "bgez", "blez", "bgtz",
}

_COMMUTATIVE_MNEMONICS = {
    "add", "addw", "mul", "mulh", "mulhu", "mulhsu",
    "and", "or", "xor", "min", "minu", "max", "maxu",
    "fadd.s", "fadd.d", "fmul.s", "fmul.d",
}

_MEMORY_READS = {
    "lb", "lbu", "lh", "lhu", "lw", "lwu", "ld",
    "flw", "fld",
}

_MEMORY_WRITES = {
    "sb", "sh", "sw", "sd",
    "fsw", "fsd",
}

_FLOAT_DEST_MNEMONICS = {
    "flw", "fld", "fmv.w.x", "fmv.d.x",
    "fadd.s", "fsub.s", "fmul.s", "fdiv.s", "fsqrt.s",
    "fmadd.s", "fmsub.s", "fnmadd.s", "fnmsub.s",
    "fadd.d", "fsub.d", "fmul.d", "fdiv.d", "fsqrt.d",
    "fmadd.d", "fmsub.d", "fnmadd.d", "fnmsub.d",
    "fsgn.s", "fsgnjn.s", "fsgnjx.s",
    "fmin.s", "fmax.s", "fmin.d", "fmax.d",
    "fcvt.s.d", "fcvt.d.s",
    "fcvt.s.w", "fcvt.s.wu", "fcvt.s.l", "fcvt.s.lu",
    "fcvt.d.w", "fcvt.d.wu", "fcvt.d.l", "fcvt.d.lu",
}

_CSR_MNEMONICS = {
    "csrrw", "csrrs", "csrrc", "csrrwi", "csrrsi", "csrrci",
    "csrr", "csrw", "csrs", "csrc", "csrwi", "csrsi", "csrci",
    "rdcycle", "rdtime", "rdinstret",
    "rdcycleh", "rdtimeh", "rdinstreth",
}

_KNOWN_MNEMONICS_WARNED: set = set()


def _parse_reg(tok: str) -> Optional[int]:
    """Parse a register name token (int or float), return unified index or None."""
    t = tok.strip().rstrip(",").lower()
    return REG_ALIASES.get(t)


def _parse_imm(tok: str) -> Optional[int]:
    """Parse an immediate value, return int or None."""
    t = tok.strip().rstrip(",")
    try:
        return int(t, 0)
    except (ValueError, TypeError):
        return None


def _parse_mem_operand(tok: str) -> tuple[Optional[int], Optional[int]]:
    """Parse 'imm(rs1)' → (imm, rs1_index) or (None, None)."""
    m = re.match(r'^(-?\d+|0x[\da-fA-F]+)\((\w+)\)$', tok.strip())
    if m:
        try:
            imm = int(m.group(1), 0)
        except ValueError:
            imm = None
        reg_name = m.group(2).lower()
        rs1 = REG_ALIASES.get(reg_name)
        return imm, rs1
    # Also handle 0(reg) style
    m2 = re.match(r'^\((\w+)\)$', tok.strip())
    if m2:
        reg_name = m2.group(1).lower()
        rs1 = REG_ALIASES.get(reg_name)
        return 0, rs1
    return None, None


def _decode_instruction(mnemonic: str, operands: list, raw: str, label: Optional[str]) -> Instruction:
    """Decode a RISC-V instruction from mnemonic and operand tokens."""
    insn = Instruction(
        mnemonic=mnemonic,
        operands=operands,
        raw=raw,
        label=label,
    )

    m = mnemonic.lower()
    ops = [o.strip().rstrip(",") for o in operands]

    try:
        # --- Pseudo-instructions: expand to canonical forms ---
        if m == "nop":
            insn.mnemonic = "addi"
            insn.rd = 0; insn.rs1 = 0; insn.imm = 0
            return insn

        if m == "mv" and len(ops) >= 2:
            insn.mnemonic = "add"
            insn.rd  = REG_ALIASES.get(ops[0].lower())
            insn.rs1 = REG_ALIASES.get(ops[1].lower())
            insn.rs2 = 0  # x0
            return insn

        if m == "li" and len(ops) >= 2:
            insn.mnemonic = "addi"
            insn.rd  = REG_ALIASES.get(ops[0].lower())
            insn.rs1 = 0
            insn.imm = _parse_imm(ops[1])
            return insn

        if m == "neg" and len(ops) >= 2:
            insn.mnemonic = "sub"
            insn.rd  = REG_ALIASES.get(ops[0].lower())
            insn.rs1 = 0
            insn.rs2 = REG_ALIASES.get(ops[1].lower())
            return insn

        if m == "not" and len(ops) >= 2:
            insn.mnemonic = "xori"
            insn.rd  = REG_ALIASES.get(ops[0].lower())
            insn.rs1 = REG_ALIASES.get(ops[1].lower())
            insn.imm = -1
            return insn

        if m == "seqz" and len(ops) >= 2:
            insn.mnemonic = "sltiu"
            insn.rd  = REG_ALIASES.get(ops[0].lower())
            insn.rs1 = REG_ALIASES.get(ops[1].lower())
            insn.imm = 1
            return insn

        if m == "snez" and len(ops) >= 2:
            insn.mnemonic = "sltu"
            insn.rd  = REG_ALIASES.get(ops[0].lower())
            insn.rs1 = 0
            insn.rs2 = REG_ALIASES.get(ops[1].lower())
            return insn

        if m == "sltz" and len(ops) >= 2:
            insn.mnemonic = "slt"
            insn.rd  = REG_ALIASES.get(ops[0].lower())
            insn.rs1 = REG_ALIASES.get(ops[1].lower())
            insn.rs2 = 0
            return insn

        if m == "sgtz" and len(ops) >= 2:
            insn.mnemonic = "slt"
            insn.rd  = REG_ALIASES.get(ops[0].lower())
            insn.rs1 = 0
            insn.rs2 = REG_ALIASES.get(ops[1].lower())
            return insn

        # --- ret, call, tail: retain as-is ---
        if m in ("ret", "call", "tail"):
            if m == "ret":
                insn.rd = 0; insn.rs1 = 1; insn.imm = 0
            elif m in ("call", "tail") and len(ops) >= 1:
                insn.branch_target = ops[0]
            return insn

        # --- Conditional branches ---
        if m in _CONDITIONAL_BRANCHES:
            # beq rs1, rs2, label / beqz rs1, label
            if m in ("beqz", "bnez", "bltz", "bgez"):
                if len(ops) >= 2:
                    insn.rs1 = REG_ALIASES.get(ops[0].lower())
                    insn.rs2 = 0
                    insn.branch_target = ops[1]
            elif m in ("blez", "bgtz"):
                if len(ops) >= 2:
                    insn.rs1 = 0
                    insn.rs2 = REG_ALIASES.get(ops[0].lower())
                    insn.branch_target = ops[1]
            elif len(ops) >= 3:
                insn.rs1 = REG_ALIASES.get(ops[0].lower())
                insn.rs2 = REG_ALIASES.get(ops[1].lower())
                insn.branch_target = ops[2]
            return insn

        # --- jal ---
        if m == "jal":
            if len(ops) >= 2:
                insn.rd = REG_ALIASES.get(ops[0].lower())
                insn.branch_target = ops[1]
            elif len(ops) == 1:
                # jal label (implicit ra)
                insn.rd = 1
                insn.branch_target = ops[0]
            return insn

        # --- jalr ---
        if m == "jalr":
            if len(ops) >= 3:
                insn.rd  = REG_ALIASES.get(ops[0].lower())
                imm_rs1 = _parse_mem_operand(ops[1] + "(" + ops[2] + ")")
                if imm_rs1[1] is not None:
                    insn.imm = imm_rs1[0]
                    insn.rs1 = imm_rs1[1]
                else:
                    insn.rs1 = REG_ALIASES.get(ops[1].lower())
                    insn.imm = _parse_imm(ops[2])
            elif len(ops) == 2:
                # jalr rd, rs1 or jalr rs1, imm
                rd_maybe = REG_ALIASES.get(ops[0].lower())
                rs1_maybe = REG_ALIASES.get(ops[1].lower())
                if rd_maybe is not None and rs1_maybe is not None:
                    insn.rd = rd_maybe; insn.rs1 = rs1_maybe; insn.imm = 0
                else:
                    imm_r = _parse_mem_operand(ops[1])
                    if imm_r[1] is not None:
                        insn.rd = 0
                        insn.rs1 = imm_r[1]
                        insn.imm = imm_r[0]
                    else:
                        insn.rd = rd_maybe; insn.rs1 = rs1_maybe
                        insn.imm = _parse_imm(ops[1])
            elif len(ops) == 1:
                # jalr rs1
                insn.rd = 0
                insn.rs1 = REG_ALIASES.get(ops[0].lower())
                insn.imm = 0
            return insn

        # --- Load instructions ---
        if m in _MEMORY_READS:
            if len(ops) >= 2:
                insn.rd = _parse_reg(ops[0])
                imm, rs1 = _parse_mem_operand(ops[1])
                insn.imm = imm
                insn.rs1 = rs1
            return insn

        # --- Store instructions ---
        if m in _MEMORY_WRITES:
            if len(ops) >= 2:
                insn.rs2 = _parse_reg(ops[0])
                imm, rs1 = _parse_mem_operand(ops[1])
                insn.imm = imm
                insn.rs1 = rs1
            return insn

        # --- R-type (3-register) ---
        _R_TYPE = {
            "add", "sub", "and", "or", "xor", "sll", "srl", "sra",
            "slt", "sltu", "addw", "subw", "sllw", "srlw", "sraw",
            "mul", "mulh", "mulhu", "mulhsu", "div", "divu", "rem", "remu",
            "mulw", "divw", "divuw", "remw", "remuw",
            "min", "minu", "max", "maxu",
            "sh1add", "sh2add", "sh3add",
            "andn", "orn", "xnor", "clmul", "clmulh", "clmulr",
        }
        if m in _R_TYPE and len(ops) >= 3:
            insn.rd  = REG_ALIASES.get(ops[0].lower())
            insn.rs1 = REG_ALIASES.get(ops[1].lower())
            insn.rs2 = REG_ALIASES.get(ops[2].lower())
            return insn

        # --- I-type (rd, rs1, imm) ---
        _I_TYPE = {
            "addi", "andi", "ori", "xori", "slti", "sltiu",
            "slli", "srli", "srai",
            "addiw", "slliw", "srliw", "sraiw",
        }
        if m in _I_TYPE and len(ops) >= 3:
            insn.rd  = REG_ALIASES.get(ops[0].lower())
            insn.rs1 = REG_ALIASES.get(ops[1].lower())
            insn.imm = _parse_imm(ops[2])
            return insn

        # --- U-type (rd, imm) ---
        if m in ("lui", "auipc") and len(ops) >= 2:
            insn.rd  = REG_ALIASES.get(ops[0].lower())
            insn.imm = _parse_imm(ops[1])
            return insn

        # --- CSR instructions ---
        if m in _CSR_MNEMONICS:
            if len(ops) >= 2:
                insn.rd = REG_ALIASES.get(ops[0].lower())
            return insn

        # --- Float instructions ---
        if m.startswith("f") or m in ("fcvt.w.s", "fcvt.wu.s", "fcvt.l.s", "fcvt.lu.s",
                                       "fcvt.w.d", "fcvt.wu.d", "fcvt.l.d", "fcvt.lu.d",
                                       "fclass.s", "fclass.d",
                                       "fmv.x.w", "fmv.x.d", "fle.s", "flt.s", "feq.s",
                                       "fle.d", "flt.d", "feq.d"):
            if len(ops) >= 1:
                insn.rd = _parse_reg(ops[0])
                if len(ops) >= 2:
                    insn.rs1 = _parse_reg(ops[1])
                if len(ops) >= 3:
                    insn.rs2 = _parse_reg(ops[2])
                if len(ops) >= 4:
                    insn.rs3 = _parse_reg(ops[3])
            return insn

        # --- Fence ---
        if m in ("fence", "fence.i", "ecall", "ebreak"):
            return insn

        # --- Atomic ---
        if m.startswith("lr.") or m.startswith("sc.") or m.startswith("amo"):
            if len(ops) >= 3:
                insn.rd  = REG_ALIASES.get(ops[0].lower())
                insn.rs2 = REG_ALIASES.get(ops[1].lower())
                insn.rs1 = REG_ALIASES.get(ops[2].lower())
            elif len(ops) >= 2:
                insn.rd  = REG_ALIASES.get(ops[0].lower())
                insn.rs1 = REG_ALIASES.get(ops[1].lower())
            return insn

    except Exception:
        pass

    # --- Best-effort for unknown instructions ---
    if m not in _KNOWN_MNEMONICS_WARNED:
        _KNOWN_MNEMONICS_WARNED.add(m)
        print(f"warning: unknown mnemonic '{m}'", file=sys.stderr)
    insn.is_unknown = True
    reg_ops = []
    first_slot_is_vector = False
    for op in ops:
        r = REG_ALIASES.get(op.lower())
        if r is None:
            off, r = _parse_mem_operand(op)
            if r is not None:
                insn.has_mem_operand = True
        if r is not None:
            if not reg_ops and r >= 64:
                first_slot_is_vector = True
            reg_ops.append(r)
    # If slot 0 held a vector register, all scalar operands are sources.
    # Otherwise the first scalar is the destination.
    scalar_ops = [r for r in reg_ops if r < 64]
    if scalar_ops:
        if first_slot_is_vector:
            sources = [r for r in scalar_ops if r != 0]
        else:
            first = scalar_ops[0]
            if first != 0:
                insn.rd = first
            sources = [r for r in scalar_ops[1:] if r != 0]
        if sources:
            insn.rs1 = sources[0]
        if len(sources) > 1:
            insn.rs2 = sources[1]
    return insn


# ---------------------------------------------------------------------------
# Line tokenization
# ---------------------------------------------------------------------------

def _tokenize_line(line: str) -> tuple[Optional[str], Optional[str], list[str], str]:
    """Parse an assembly source line.

    Returns: (label, mnemonic, operand_tokens, raw_line)
    """
    raw = line
    # Strip comments
    line = re.sub(r'#.*$', '', line).strip()
    line = re.sub(r'//.*$', '', line).strip()

    if not line:
        return None, None, [], raw

    label = None

    # Check for label
    m = re.match(r'^(\S+):\s*(.*)', line)
    if m:
        label = m.group(1)
        line = m.group(2).strip()

    if not line:
        return label, None, [], raw

    # Check for directive
    if line.startswith('.'):
        return label, None, [], raw

    # Parse mnemonic and operands
    parts = line.split(None, 1)
    mnemonic = parts[0].lower()
    operand_str = parts[1] if len(parts) > 1 else ""

    # Split operands by comma (but keep mem operands together)
    operands = [o.strip() for o in operand_str.split(',') if o.strip()]

    return label, mnemonic, operands, raw


# ---------------------------------------------------------------------------
# Two-pass parser
# ---------------------------------------------------------------------------

def parse_file(source: str) -> tuple[list[BasicBlock], list[Function]]:
    """Parse RISC-V assembly source text.

    Returns (blocks, functions).
    """
    lines = source.splitlines()

    # --- Pass 1: Collect labels and identify barrier labels ---
    all_labels: set = set()
    branch_targets: set = set()
    globl_weak: set = set()
    type_function: set = set()  # labels declared .type @function

    for line in lines:
        stripped = re.sub(r'#.*$', '', line).strip()
        if not stripped:
            continue

        # Collect label definitions
        lm = re.match(r'^(\S+):\s*', stripped)
        if lm:
            all_labels.add(lm.group(1))

        # .globl / .weak directives
        gm = re.match(r'^\.(globl|global|weak)\s+(\S+)', stripped)
        if gm:
            globl_weak.add(gm.group(2))

        # .type name, @function
        tm = re.match(r'^\.type\s+(\S+)\s*,\s*@function', stripped)
        if tm:
            type_function.add(tm.group(1))

        # Branch/jump targets from instructions
        # Look for branch/jump mnemonics followed by a label target
        parts = stripped.split()
        if len(parts) >= 2:
            mn = parts[0].lower()
            if ':' in parts[0]:
                # It's a label: mnemonic
                rest = stripped[stripped.index(':') + 1:].strip()
                rparts = rest.split()
                if rparts:
                    mn = rparts[0].lower()
                    parts = rparts

            if mn in _CONDITIONAL_BRANCHES or mn in ("jal", "j", "call", "tail"):
                # Last non-register operand is likely a label
                for tok in reversed(parts[1:]):
                    t = tok.strip().rstrip(",")
                    if t and t[0].isalpha() or (t and t[0] == '.') or (t and t[0] == '_'):
                        if t not in REG_ALIASES:
                            branch_targets.add(t)
                            break

    # Barrier labels: appear as branch targets, or .globl/.weak, or .type @function
    barrier_labels = branch_targets | globl_weak | type_function

    # Function entry labels
    function_entries = type_function | globl_weak

    # --- Pass 2: Decode instructions, build basic blocks ---
    blocks: list[BasicBlock] = []
    current_block_insns: list[Instruction] = []
    current_block_labels: list[str] = []
    current_is_function_entry: bool = False
    prefix_buffer: list[str] = []  # non-barrier labels/lines buffered for next insn

    _anon_counter = [0]

    def _flush_block():
        nonlocal current_block_insns, current_block_labels, current_is_function_entry, prefix_buffer
        if current_block_insns or current_block_labels:
            if current_block_labels:
                label = current_block_labels[0]
            else:
                label = f"<anon_{_anon_counter[0]}>"
                _anon_counter[0] += 1
            bb = BasicBlock(
                label=label,
                labels=list(current_block_labels),
                instructions=list(current_block_insns),
                is_function_entry=current_is_function_entry,
            )
            blocks.append(bb)
        current_block_insns = []
        current_block_labels = []
        current_is_function_entry = False
        prefix_buffer = []

    def _new_block_with_label(lbl: str):
        nonlocal current_block_labels, current_is_function_entry
        _flush_block()
        current_block_labels = [lbl]
        current_is_function_entry = lbl in function_entries

    in_text_section = True

    for line in lines:
        raw = line
        stripped = re.sub(r'#.*$', '', line).strip()

        if not stripped:
            continue

        # Track section
        if re.match(r'^\.section\s+\.(text|text\.\S+)', stripped):
            in_text_section = True
            continue
        if re.match(r'^\.section\s+\.(data|bss|rodata|rodata\.\S+)', stripped):
            in_text_section = False
            continue
        if re.match(r'^\.text\b', stripped):
            in_text_section = True
            continue
        if re.match(r'^\.data\b|^\.bss\b|^\.rodata\b', stripped):
            in_text_section = False
            continue

        # Directives (non-instruction, non-label lines)
        if stripped.startswith('.') and not re.match(r'^\.', stripped.lstrip().split(':')[0] if ':' in stripped else stripped):
            continue

        # Check for label prefix
        lm = re.match(r'^(\S+):\s*(.*)', stripped)
        if lm:
            lbl = lm.group(1)
            rest_of_line = lm.group(2).strip()

            if lbl in barrier_labels:
                # Start a new block
                _new_block_with_label(lbl)
                # Process any buffered prefix lines on the new block's first instruction
                # (they'll be attached to the first instruction when it's added)
                # Any prefix_buffer from before this barrier label becomes
                # prefix for the first insn of the new block
                if not rest_of_line or rest_of_line.startswith('.'):
                    # Label is on its own line — the block starts here
                    continue
                # else: fall through to process rest_of_line as an instruction
                stripped = rest_of_line
                # Process as normal instruction below
                lm2 = re.match(r'^(\S+):\s*(.*)', rest_of_line)
                if lm2:
                    # More labels chained
                    lbl2 = lm2.group(1)
                    if lbl2 in barrier_labels:
                        current_block_labels.append(lbl2)
                    else:
                        prefix_buffer.append(raw)
                    stripped = lm2.group(2).strip()
            else:
                # Non-barrier label: add to prefix buffer
                if not rest_of_line or rest_of_line.startswith('.'):
                    prefix_buffer.append(raw)
                    continue
                else:
                    prefix_buffer.append(f"{lbl}:")
                    stripped = rest_of_line

        # Skip pure directive lines
        if stripped.startswith('.'):
            continue

        if not stripped:
            continue

        # Parse instruction
        parts = stripped.split(None, 1)
        if not parts:
            continue
        mnemonic = parts[0].lower()
        operand_str = parts[1] if len(parts) > 1 else ""
        operands = [o.strip() for o in operand_str.split(',') if o.strip()]

        insn = _decode_instruction(mnemonic, operands, raw, None)
        insn.prefix_lines = list(prefix_buffer)
        prefix_buffer = []

        current_block_insns.append(insn)

        # Check if this instruction is a block terminator
        is_term = (insn.is_branch or insn.is_jump or insn.is_return or
                   insn.is_tail or
                   (insn.mnemonic == "jalr" and insn.rd == 0))

        if is_term:
            _flush_block()

    _flush_block()

    if not blocks:
        return [], []

    # --- Build CFG edges ---
    label_to_block: dict[str, BasicBlock] = {}
    for bb in blocks:
        for lbl in bb.labels:
            label_to_block[lbl] = bb
        if bb.label:
            label_to_block[bb.label] = bb

    for i, bb in enumerate(blocks):
        if not bb.instructions:
            # Connect empty block to next
            if i + 1 < len(blocks):
                bb.successors.append(blocks[i + 1])
                blocks[i + 1].predecessors.append(bb)
            continue

        last = bb.instructions[-1]
        next_bb = blocks[i + 1] if i + 1 < len(blocks) else None

        if last.is_branch:
            # Two successors: branch target + fall-through
            if last.branch_target and last.branch_target in label_to_block:
                tgt = label_to_block[last.branch_target]
                if not tgt.is_function_entry and tgt not in bb.successors:
                    bb.successors.append(tgt)
                    tgt.predecessors.append(bb)
            if next_bb and not next_bb.is_function_entry and next_bb not in bb.successors:
                bb.successors.append(next_bb)
                next_bb.predecessors.append(bb)

        elif last.is_call:
            # Fall-through only
            if next_bb and not next_bb.is_function_entry and next_bb not in bb.successors:
                bb.successors.append(next_bb)
                next_bb.predecessors.append(bb)

        elif last.is_jump and not last.is_call and not last.is_return:
            # jal x0, target — one successor
            if last.branch_target and last.branch_target in label_to_block:
                tgt = label_to_block[last.branch_target]
                if not tgt.is_function_entry and tgt not in bb.successors:
                    bb.successors.append(tgt)
                    tgt.predecessors.append(bb)
            elif last.rd not in (None, 0, 1, 5) and last.mnemonic == "jalr":
                # Unclassified jalr — no successors
                pass
            elif last.mnemonic == "jalr" and last.rd == 0:
                # Tail call / indirect jump — no successors
                pass

        elif last.is_return or last.is_tail:
            pass  # no successors

        elif last.mnemonic in ("ecall", "ebreak"):
            # Fall-through
            if next_bb and next_bb not in bb.successors:
                bb.successors.append(next_bb)
                next_bb.predecessors.append(bb)

        else:
            # Fall-through
            if next_bb:
                # Check function boundary
                if next_bb.is_function_entry:
                    pass  # hard boundary — don't connect
                else:
                    if next_bb not in bb.successors:
                        bb.successors.append(next_bb)
                        next_bb.predecessors.append(bb)

    # --- Identify functions ---
    functions = identify_functions(blocks)

    # --- Enforce hard function boundaries ---
    # Any successor/predecessor edges that cross function boundaries must be
    # removed. This can happen when a branch targets a function-entry label
    # (e.g. a loop that spans a .globl boundary) — the parser guards new edges
    # above, but older edges set before function identification are still present.
    for fn in functions:
        fn_block_set = set(id(b) for b in fn.blocks)
        for bb in fn.blocks:
            bb.successors   = [s for s in bb.successors   if id(s) in fn_block_set]
            bb.predecessors = [p for p in bb.predecessors if id(p) in fn_block_set]

    return blocks, functions


def identify_functions(blocks: list) -> list:
    """Group blocks into Function objects."""
    from analysis.cfg import Function
    functions = []
    visited_ids: set = set()

    def _visited(b):
        return id(b) in visited_ids

    def _mark(b):
        visited_ids.add(id(b))

    for bb in blocks:
        if _visited(bb):
            continue
        if bb.is_function_entry or not bb.predecessors:
            # BFS from this block
            fname = bb.label or f"<anon@{id(bb)}>"
            fn_blocks = []
            queue = [bb]
            fn_visited: set = set()
            while queue:
                b = queue.pop(0)
                if id(b) in fn_visited:
                    continue
                fn_visited.add(id(b))
                fn_blocks.append(b)
                for s in b.successors:
                    if id(s) not in fn_visited and not s.is_function_entry:
                        queue.append(s)
            functions.append(Function(name=fname, entry=bb, blocks=fn_blocks))
            for b in fn_blocks:
                _mark(b)

    # Collect unreachable blocks into anonymous functions
    for bb in blocks:
        if not _visited(bb):
            fname = f"<dead@{id(bb)}>"
            functions.append(Function(name=fname, entry=bb, blocks=[bb]))
            _mark(bb)

    return functions
