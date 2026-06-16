"""
analysis/cfg.py — Basic block and function data structures.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BasicBlock:
    label:             Optional[str]
    instructions:      list = field(default_factory=list)
    successors:        list = field(default_factory=list)
    predecessors:      list = field(default_factory=list)
    is_function_entry: bool = False

    # Allow multi-label blocks (used by parser)
    labels: list = field(default_factory=list)

    # Lines that must appear before any instruction in this block
    # (barrier labels, .globl/.type directives, blanks before the entry label)
    prefix_lines: list = field(default_factory=list)

    def __post_init__(self):
        # Ensure labels list is populated from label field if not provided
        if not self.labels and self.label is not None:
            self.labels = [self.label]


@dataclass
class Function:
    name:    str
    entry:   BasicBlock
    blocks:  list = field(default_factory=list)
