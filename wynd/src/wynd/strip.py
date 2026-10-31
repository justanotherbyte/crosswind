import ast
import io
import re
import tokenize
from typing import NamedTuple

__all__ = ("metadata_headers", "strip")

# PEP 263's encoding declaration, which must survive comment stripping.
CODING = re.compile(r"^[ \t\f]*#.*?coding[:=]")


class Cut(NamedTuple):
    """A span of source to replace, with columns counted in characters."""

    row: int
    column: int
    end_row: int
    end_column: int
    replacement: str


def strip(source: bytes, docstrings: bool) -> bytes:
    """A module's source with comments, and docstrings if asked, blanked out
    without moving any line. Source that won't parse comes back unchanged."""
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
        # Lines as Python counts them. str.splitlines also splits at form feeds
        # and other characters that don't end a line, which shifts every cut.
        lines = io.StringIO(source.decode(encoding), newline=None).readlines()
        text = "".join(lines)
        tree = ast.parse(text)
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (SyntaxError, ValueError, UnicodeDecodeError, tokenize.TokenError):
        return source

    cuts = [
        Cut(*token.start, *token.end, "")
        for token in tokens
        if token.type == tokenize.COMMENT
        and not (token.start[0] <= 2 and CODING.match(token.line))
    ]
    for node in ast.walk(tree) if docstrings else []:
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        first = node.body[0] if node.body else None
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            continue
        assert first.end_lineno is not None and first.end_col_offset is not None
        # ast counts columns in UTF-8 bytes.
        start = len(lines[first.lineno - 1].encode()[: first.col_offset].decode())
        end = len(lines[first.end_lineno - 1].encode()[: first.end_col_offset].decode())
        # A body needs a statement. A module doesn't, and `pass` would come
        # before its `from __future__` imports.
        sole = len(node.body) == 1 and not isinstance(node, ast.Module)
        replacement = "pass" if sole else ""
        cuts.append(Cut(first.lineno, start, first.end_lineno, end, replacement))

    for row, column, end_row, end_column, replacement in sorted(cuts, reverse=True):
        head, tail = lines[row - 1][:column], lines[end_row - 1][end_column:]
        lines[row - 1 : end_row] = [head + replacement + "\n" * (end_row - row) + tail]

    stripped = "".join(lines)
    try:
        ast.parse(stripped)
    except SyntaxError:
        return source
    return stripped.encode(encoding)


def metadata_headers(metadata: bytes) -> bytes:
    """A METADATA file without its description, the body after the headers."""
    headers, blank, _ = metadata.partition(b"\n\n")
    return headers + b"\n" if blank else metadata
