"""
analysis/jalr_predecessor_analysis.py

Analyse jalr instructions in tests/godot.s:
- For each jalr, look at the immediately preceding instruction in the same basic block.
- Filter to only addi predecessors.
- Split jalr into imm==0 vs imm!=0 groups.
- Within each group, classify the addi subtype and print a breakdown table.
"""

import sys
import os

# Allow import from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analysis.parser import parse_file


def classify_addi(insn):
    """Return a subtype string for an addi instruction."""
    rd  = insn.rd  if insn.rd  is not None else 0
    rs1 = insn.rs1 if insn.rs1 is not None else 0
    imm = insn.imm if insn.imm is not None else 0

    # addi sp,sp,imm (imm>0) — stack pointer epilogue increment
    if rd == 2 and rs1 == 2 and imm > 0:
        return "addi sp,sp,+imm (epilogue)"

    # li: rd != 0, rs1 == 0 (load immediate)
    if rd != 0 and rs1 == 0:
        return "li  (rd!=0, rs1==0)"

    # mv: rd != 0, rs1 != 0, imm == 0 (move)
    if rd != 0 and rs1 != 0 and imm == 0:
        return "mv  (rd!=0, rs1!=0, imm==0)"

    # addi rsd: rd == rs1, rd != 0, rd != 2 (in-place add)
    if rd == rs1 and rd != 0 and rd != 2:
        return "addi rsd (rd==rs1, rd!=0,2)"

    # addi rd,rs1 general: rd != rs1
    if rd != rs1:
        return "addi rd,rs1 (general)"

    # catch-all
    return "addi (other)"


def main():
    asm_path = os.path.join(os.path.dirname(__file__), "..", "tests", "godot.s")
    with open(asm_path, "r") as f:
        source = f.read()

    blocks, functions = parse_file(source)

    # Collect (jalr_insn, predecessor_insn_or_None) pairs
    jalr_zero_imm   = []  # jalr with imm == 0
    jalr_nonzero_imm = []  # jalr with imm != 0

    for bb in blocks:
        insns = bb.instructions
        for idx, insn in enumerate(insns):
            if insn.mnemonic != "jalr":
                continue
            imm = insn.imm if insn.imm is not None else 0
            # Get immediately preceding instruction in same block
            pred = insns[idx - 1] if idx > 0 else None
            if imm == 0:
                jalr_zero_imm.append((insn, pred))
            else:
                jalr_nonzero_imm.append((insn, pred))

    def analyse_group(label, pairs):
        print(f"\n{'='*60}")
        print(f"jalr group: {label}   (total jalr: {len(pairs)})")
        print(f"{'='*60}")

        addi_preds = [(j, p) for j, p in pairs if p is not None and p.mnemonic == "addi"]
        non_addi   = len(pairs) - len(addi_preds)

        print(f"  Preceding instruction is addi : {len(addi_preds)}")
        print(f"  Preceding instruction is other: {non_addi}  (excluded from table)")

        if not addi_preds:
            print("  (no addi predecessors)")
            return

        counts = {}
        for j, p in addi_preds:
            sub = classify_addi(p)
            counts[sub] = counts.get(sub, 0) + 1

        total = len(addi_preds)
        print(f"\n  {'Subtype':<38} {'Count':>6}  {'%':>6}")
        print(f"  {'-'*38}  {'-'*6}  {'-'*6}")
        for sub, cnt in sorted(counts.items(), key=lambda x: -x[1]):
            pct = 100.0 * cnt / total
            print(f"  {sub:<38} {cnt:>6}  {pct:>5.1f}%")
        print(f"  {'TOTAL':<38} {total:>6}  100.0%")

    analyse_group("imm == 0", jalr_zero_imm)
    analyse_group("imm != 0", jalr_nonzero_imm)

    print()


if __name__ == "__main__":
    main()
