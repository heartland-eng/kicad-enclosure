"""A small, dependency-free reader for the KiCad s-expression (.kicad_pcb) format.

KiCad 6+ stores PCB data as a Specctra-DSN-style s-expression: nested
parenthesised lists, lowercase atoms, and double-quoted strings.  This module
implements a recursive-descent tokenizer + parser that turns such text into
plain nested Python lists, following the same convention as the popular
``sexpdata`` library:

* an unquoted atom (``gr_line``, ``pad``, a number ``25`` ...) becomes a
  ``Symbol`` (a ``str`` subclass) so it can still be compared to plain strings;
* a double-quoted string (``"Edge.Cuts"``, ``"R1"`` ...) becomes a plain
  ``str`` (quotes stripped, escapes handled);
* a parenthesised group becomes a ``list`` whose first element is the head
  atom and whose remaining elements are the arguments.

Only the standard library is used, so the parser runs in any Python >= 3.8
environment without installing KiCad or any third-party package.
"""

from __future__ import annotations

from typing import List, Tuple, Union

__all__ = ["Symbol", "loads", "load"]


class Symbol(str):
    """An unquoted atom.  Subclasses ``str`` so ``Symbol("gr_line") == "gr_line"``
    is True, while ``isinstance(x, Symbol)`` distinguishes atoms from quoted strings."""


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_Token = Tuple[str, str]  # (kind, text) where kind in {"LPAREN", "RPAREN", "STRING", "ATOM"}


def _tokenize(text: str) -> List[_Token]:
    tokens: List[_Token] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == ";":
            # Line comment (KiCad tolerates these; skip to end of line).
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if c == "(":
            tokens.append(("LPAREN", "("))
            i += 1
            continue
        if c == ")":
            tokens.append(("RPAREN", ")"))
            i += 1
            continue
        if c == '"':
            j = i + 1
            buf: List[str] = []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == '"':
                    break
                buf.append(text[j])
                j += 1
            tokens.append(("STRING", "".join(buf)))
            i = j + 1
            continue
        # atom
        j = i
        while j < n and text[j] not in " \t\r\n()\"":
            j += 1
        tokens.append(("ATOM", text[i:j]))
        i = j
    return tokens


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# A parsed node is a list; leaves are Symbol (atoms) or str (quoted strings).
Node = Union[List["Node"], Symbol, str]


def loads(text: str) -> List[Node]:
    """Parse s-expression text into a list of top-level expressions."""
    tokens = _tokenize(text)
    pos = 0

    def parse_list() -> List[Node]:
        nonlocal pos
        # tokens[pos] is LPAREN
        pos += 1
        result: List[Node] = []
        while pos < len(tokens):
            kind, val = tokens[pos]
            if kind == "RPAREN":
                pos += 1
                return result
            if kind == "LPAREN":
                result.append(parse_list())
            elif kind == "STRING":
                result.append(val)
                pos += 1
            else:  # ATOM
                result.append(Symbol(val))
                pos += 1
        raise ValueError("unbalanced parentheses in s-expression")

    exprs: List[Node] = []
    while pos < len(tokens):
        kind, _ = tokens[pos]
        if kind == "LPAREN":
            exprs.append(parse_list())
        else:
            # stray top-level atom (should not happen in valid KiCad output)
            pos += 1
    return exprs


def load(fp) -> List[Node]:
    """Parse from a file object or path."""
    if hasattr(fp, "read"):
        return loads(fp.read())
    with open(fp, "r", encoding="utf-8") as f:
        return loads(f.read())
