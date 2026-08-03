"""analysis/anchors.py — reusable "what can I pair with X?" analysis.

Every candidate-frame question in this project has the same shape: pick an
anchor instruction, ask what else could sit beside it, and count. Doing that
honestly needs three things that a naive adjacency census skips:

  1. Run the REAL scheduler and pairer first, so instructions another frame
     has already taken are not counted twice (the selection bias recorded in
     "Two ways to bias an immediate-width census").
  2. Look at every candidate in the block that could be REORDERED to the
     anchor, not just the one that happens to sit next door.
  3. Score operand SHAPES, not mnemonics — the frame chooses its op set, so
     the question is which encodable shapes are reachable.

The expensive part is parse + liveness + schedule + pair, which costs minutes
on cpp-rv32. This module does it once and caches a reduced form of the
scheduled stream, keyed by the content of everything that could change it, so
every later question answers in seconds.

  from analysis.anchors import stream, candidates, shapes
  for block in stream("cpp-rv32"):
      for j, rec in enumerate(block):
          if rec.is_call and rec.solo:
              for i in candidates(block, j):
                  print(shapes(block[i]))
"""
import hashlib
import os
import pickle
import sys
from collections import namedtuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "results", "cache")

# Anything whose content could change the scheduled stream. A cache entry is
# keyed by the digest of all of it, so editing a rule invalidates every corpus.
INPUTS = ["analysis/parser.py", "analysis/liveness.py", "analysis/depgraph.py",
          "scheduler/reorder.py", "scheduler/pairing.py", "scheduler/rules.py",
          "encoding.yaml"]

Rec = namedtuple("Rec", "mnem rd rs1 rs2 imm solo is_call is_branch is_jump "
                        "is_load is_store")

LOADS = {"lw", "ld", "lbu", "lhu", "lb", "lh", "lwu", "flw", "fld"}
STORES = {"sw", "sd", "sb", "sh", "fsw", "fsd"}
ARG = frozenset(range(10, 18))          # a0-a7, the 3-bit register class
SP = 2


def _fingerprint():
    h = hashlib.sha256()
    for rel in INPUTS:
        path = os.path.join(ROOT, rel)
        h.update(rel.encode())
        with open(path, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:16]


def _build(corpus):
    """Parse, schedule and pair one corpus; reduce to plain tuples."""
    from analysis.parser import parse_file
    from analysis.liveness import compute_global_liveness, compute_local_liveness
    from analysis.depgraph import build_dep_graph
    from scheduler.reorder import schedule, ScheduleMode
    from scheduler.pairing import greedy_pair, stamp_slot_eligibility

    path = os.path.join(ROOT, "tests", f"{corpus}.s")
    with open(path) as fh:
        _blocks, functions = parse_file(fh.read())

    out = []
    for fn in functions:
        for b in fn.blocks:
            stamp_slot_eligibility(b.instructions)
        gl = compute_global_liveness(fn.blocks)
        for b in fn.blocks:
            if not b.instructions:
                continue
            compute_local_liveness(b, gl)
            graph = build_dep_graph(b)
            ordered = schedule(b, graph, ScheduleMode.LIST)
            b.instructions = ordered
            compute_local_liveness(b, gl)
            solo = {id(it[1]) for it in greedy_pair(ordered) if it[0] == "solo"}
            out.append([
                Rec(p.mnemonic, p.rd, p.rs1, p.rs2, p.imm, id(p) in solo,
                    bool(getattr(p, "is_call", False)),
                    bool(getattr(p, "is_branch", False)),
                    bool(getattr(p, "is_jump", False)),
                    p.mnemonic in LOADS, p.mnemonic in STORES)
                for p in ordered])
    return out


def stream(corpus, rebuild=False):
    """The scheduled, paired corpus as a list of blocks of Rec, from cache."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{corpus}.{_fingerprint()}.pkl")
    if not rebuild and os.path.exists(path):
        with open(path, "rb") as fh:
            return pickle.load(fh)
    blocks = _build(corpus)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        pickle.dump(blocks, fh, protocol=4)
    os.replace(tmp, path)
    # a rebuilt fingerprint orphans the old entries for this corpus
    for stale in os.listdir(CACHE):
        if stale.startswith(corpus + ".") and stale != os.path.basename(path):
            os.remove(os.path.join(CACHE, stale))
    return blocks


# ---------------------------------------------------------------- reordering

def _defs(r):
    return {r.rd} if r.rd not in (None, 0) else set()


def _uses(r):
    return {x for x in (r.rs1, r.rs2) if x is not None}


def movable(block, i, j):
    """Can block[i] be moved down to sit immediately before block[j]?

    Deliberately conservative: no control transfer may be crossed, no register
    hazard may be created, and memory operations may not be reordered past one
    another. It will refuse some legal moves; it should never permit an
    illegal one, so counts built on it are a floor.
    """
    x = block[i]
    d, u = _defs(x), _uses(x)
    mem = x.is_load or x.is_store
    for k in range(i + 1, j):
        y = block[k]
        if y.is_call or y.is_branch or y.is_jump:
            return False
        if d & (_defs(y) | _uses(y)):
            return False
        if u & _defs(y):
            return False
        if mem and (y.is_load or y.is_store):
            return False
    return True


def candidates(block, j, solo_only=True):
    """Indices of instructions that could be made adjacent to block[j].

    solo_only excludes anything the pairer has already given to another frame
    — the correction that separates a real opportunity from a stolen one.
    """
    return [i for i in range(j - 1, -1, -1)
            if (block[i].solo or not solo_only) and movable(block, i, j)]


# ------------------------------------------------------------------- shapes

def _u(v, bits):
    return v is not None and 0 <= v < (1 << bits)


def _s(v, bits):
    return v is not None and -(1 << (bits - 1)) <= v < (1 << (bits - 1))


COMMUTATIVE = {"add", "and", "or", "xor", "min", "max", "minu", "maxu", "mul",
               "addw", "mulw"}
RSD_ALU = {"add", "sub", "and", "or", "xor", "sll", "srl", "sra", "slt", "sltu",
           "addw", "subw", "sllw", "srlw", "sraw", "min", "max", "minu",
           "maxu", "mul", "mulw"}
SHIFTI = {"slli", "srli", "srai", "slliw", "srliw", "sraiw"}
SHADD = {"sh1add", "sh2add", "sh3add"}


def shapes(r, budget=10):
    """Every operand shape of `budget` bits or fewer that could encode `r`.

    A shape name reads as its row would be drawn: `rd3` is a three-bit
    destination restricted to a0-a7, `imm7*4` is a seven-bit field scaled by
    four, `rsd5` is a five-bit register used as both source and destination.
    Widening a field past five bits is free only while the row still fits the
    budget; that is what makes this the right unit to count.
    """
    m, rd, rs1, rs2 = r.mnem, r.rd, r.rs1, r.rs2
    i = r.imm if r.imm is not None else 0
    out = set()

    def add(name, bits):
        if bits <= budget:
            out.add(name)

    if m == "mv" or (m in ("addi", "addiw") and i == 0 and rs1 not in (0, None)):
        if rd not in (0, None):
            add("mv rd5,rs5", 10)
            if rd in ARG:
                add("mv rd3,rs5", 8)
        return out

    if m == "li" or (m in ("addi", "addiw") and rs1 in (0, None)):
        for b in (5, 6, 7, 8):
            if rd in ARG and _s(i, b):
                add(f"li rd3,imm{b}", 3 + b)
            if rd not in (0, None) and _s(i, b):
                add(f"li rd5,imm{b}", 5 + b)
        return out

    if m in ("addi", "addiw") and rs1 == SP:
        for b in (5, 7, 9):
            if rd in ARG and _u(i, b):
                add(f"addi rd3,sp,imm{b}", 3 + b)
            if rd not in (0, None) and _u(i, b):
                add(f"addi rd5,sp,imm{b}", 5 + b)
            if rd in ARG and _u(i, b + 2) and i % 4 == 0:
                add(f"addi rd3,sp,imm{b}*4", 3 + b)
        return out

    if m in ("addi", "addiw"):
        if rd == rs1 and rd not in (0, None) and _s(i, 5):
            add("addi rsd5,imm5", 10)
        if rd in ARG and rs1 in ARG and _s(i, 4):
            add("addi rd3,rs3,imm4", 10)
        return out

    if r.is_load:
        for b in (5, 7):
            if rd in ARG and rs1 == SP and _u(i, b):
                add(f"load rd3,imm{b}(sp)", 3 + b)
            if rd in ARG and rs1 == SP and _u(i, b + 2) and i % 4 == 0:
                add(f"load rd3,imm{b}*4(sp)", 3 + b)
        if rd not in (0, None) and rs1 is not None and i == 0:
            add("load rd5,0(rs5)", 10)
        if rd in ARG and rs1 is not None and _u(i, 2):
            add("load rd3,imm2(rs5)", 10)
        return out

    if r.is_store:
        if rs1 == SP:
            if _u(i, 5):
                add("store rs5,imm5(sp)", 10)
            if _u(i, 7) and i % 4 == 0:
                add("store rs5,imm5*4(sp)", 10)
            if rs2 in ARG and _u(i, 7):
                add("store rs3,imm7(sp)", 10)
        if i == 0 and rs1 is not None and rs2 is not None:
            add("store rs5,0(rs5)", 10)
        return out

    if m in RSD_ALU:
        if rd not in (0, None) and (rd == rs1 or (rd == rs2 and m in COMMUTATIVE)):
            add(f"{m} rsd5,rs5", 10)
        return out

    if m in SHIFTI:
        if rd == rs1 and rd not in (0, None) and _u(i, 5):
            add(f"{m} rsd5,shamt5", 10)
        return out

    if m in SHADD:
        if rd == rs1 and rd in ARG:
            add("shadd rsd3,rs5,sh2", 10)
        return out

    return out


# ------------------------------------------------------------------ scanning

def direct_call(r):
    """The usual anchor: a call whose target is a label, not a register."""
    return r.is_call and r.mnem != "jalr"


def scan(corpus, anchor=direct_call, solo_anchor_only=True, budget=10,
         rebuild=False):
    """Per anchor, the set of shapes reachable there and the candidate count.

    Returns (n_anchors, n_scored, [ (shapes, n_candidates), ... ]).
    n_anchors counts every anchor; n_scored counts those actually scored,
    which differs when solo_anchor_only drops anchors another frame took.
    """
    rows = []
    n_anchors = 0
    for block in stream(corpus, rebuild=rebuild):
        for j, rec in enumerate(block):
            if not anchor(rec):
                continue
            n_anchors += 1
            if solo_anchor_only and not rec.solo:
                continue
            avail, count = set(), 0
            for i in candidates(block, j):
                sh = shapes(block[i], budget)
                if sh:
                    avail |= sh
                    count += 1
            rows.append((frozenset(avail), count))
    return n_anchors, len(rows), rows


def greedy(rows, limit=12):
    """Shapes in order of marginal gain: [(shape, gain, cumulative), ...]."""
    remaining, chosen, cum = list(rows), [], 0
    universe = set()
    for avail, _ in rows:
        universe |= avail
    while len(chosen) < limit:
        best, gain = None, 0
        for shp in universe:
            if shp in chosen:
                continue
            g = sum(1 for a, _ in remaining if shp in a)
            if g > gain:
                best, gain = shp, g
        if not best:
            break
        chosen.append(best)
        cum += gain
        remaining = [(a, c) for a, c in remaining if best not in a]
        yield best, gain, cum


def coverage(rows, shape_set):
    """How many anchors a fixed set of shapes covers."""
    s = set(shape_set)
    return sum(1 for a, _ in rows if a & s)
