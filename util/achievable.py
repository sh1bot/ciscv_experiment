"""For each frame with an immediate, what widths can it honestly afford?

Achievable = the field drawn from register columns (5 or 10 bits, never g/h)
plus opcode repetition, priced by the existing model.  For each candidate
width we redeclare every immediate-carrying op in the slot at that width and
read the resulting demand, then round up to a power-of-two block.
"""
import copy, os, sys, yaml
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from encoding_render import (opcode_codepoints, imm_field_bits, op_name,
                             REG_FORM_OPS, op_imm)

spec = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'encoding.yaml')))
grid = spec['grid']

def block(n):
    b = 1
    while b < n: b <<= 1
    return b

def redeclare(frame, slot, bits):
    f = copy.deepcopy(frame)
    for cl in f.get('ops') or []:
        new = []
        for e in cl.get(slot, []):
            n = op_name(e)
            if n in REG_FORM_OPS:
                new.append(e); continue
            c = dict(op_imm(e)); c['bits'] = bits
            c.setdefault('signed', True)
            new.append({'op': n, 'imm': c})
        if new: cl[slot] = new
    return f

print(f"{'frame':30}{'slot':>5}{'base':>5}{'budget':>7}   demand@ 5   6   7   8  10  12")
print("-"*86)
for node in spec['doc']:
    f = node.get('frame')
    if not f or not f.get('ops') or not f.get('rows'): continue
    for slot in ('a','b'):
        try: base, full = imm_field_bits(f, grid, slot)
        except Exception: continue
        if not base: continue
        carries = any(op_name(e) not in REG_FORM_OPS
                      for cl in f['ops'] for e in cl.get(slot, []))
        if not carries: continue
        row = []
        for w in (5,6,7,8,10,12):
            try: d = opcode_codepoints(redeclare(f, slot, w), grid)
            except Exception: d = None
            row.append(f"{block(d):>4}" if d else "   -")
        print(f"{f['name'][:29]:30}{slot:>5}{base:>5}{f.get('budget',0):>7}   "
              + "".join(row))
