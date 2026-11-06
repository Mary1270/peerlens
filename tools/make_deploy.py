#!/usr/bin/env python3
"""
Builds contract_deploy.py from contract.py.

GenLayer Studio can reject a contract file that carries long comments or
docstring blocks, so the deployable file keeps exactly two header lines and
then code. The stripping is done with the ast and tokenize modules, never
with regular expressions on source text:

  1. ast locates every docstring (and any other bare string statement);
     those statements are removed, or replaced by `pass` when a body would
     otherwise become empty.
  2. tokenize locates every comment; each is cut from its line.
  3. Runs of blank lines are collapsed.
  4. The two required header lines are written back first.

The result is verified before it is written: its syntax tree must equal the
syntax tree of contract.py with docstrings removed. Any difference aborts.

Usage:
    python3 tools/make_deploy.py            write contract_deploy.py
    python3 tools/make_deploy.py --check    exit 1 if contract_deploy.py is stale
"""
import ast
import io
import os
import sys
import tokenize

HEADER = (
    "# v0.2.16",
    '# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }',
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "contract.py")
TARGET = os.path.join(ROOT, "contract_deploy.py")

SCOPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _is_string_statement(node):
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _string_statements(tree):
    """Every bare string statement together with the body that holds it."""
    found = []
    for scope in ast.walk(tree):
        if not isinstance(scope, SCOPES):
            continue
        for field in ("body", "orelse", "finalbody"):
            body = getattr(scope, field, None)
            if isinstance(body, list):
                for node in body:
                    if _is_string_statement(node):
                        found.append((scope, field, node))
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if isinstance(body, list) and not isinstance(node, SCOPES):
                for child in body:
                    if _is_string_statement(child):
                        found.append((node, field, child))
    return found


def remove_docstrings_from_tree(tree):
    """In-place: drop every bare string statement, adding `pass` where a
    body would become empty. Used to build the expected syntax tree."""
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if isinstance(body, list):
                kept = [child for child in body if not _is_string_statement(child)]
                if len(kept) != len(body):
                    if not kept and field == "body" and not isinstance(node, ast.Module):
                        kept = [ast.Pass()]
                    setattr(node, field, kept)
    return tree


def strip_source(source):
    tree = ast.parse(source)
    lines = source.splitlines()
    drop = set()
    replace_with_pass = {}
    for scope, field, node in _string_statements(tree):
        body = getattr(scope, field)
        first = node.lineno - 1
        last = node.end_lineno - 1
        text_first = lines[first]
        indent = text_first[: len(text_first) - len(text_first.lstrip())]
        if len(indent) != node.col_offset:
            raise SystemExit(f"string statement at line {node.lineno} shares its line with other code")
        if lines[last].rstrip()[node.end_col_offset - 1:] not in ('"', "'", '"""', "'''") and \
                len(lines[last].rstrip()) != node.end_col_offset:
            raise SystemExit(f"string statement at line {node.lineno} is followed by other code")
        only_statement = all(_is_string_statement(child) for child in body)
        if only_statement and field == "body" and not isinstance(scope, ast.Module):
            if node is body[0]:
                replace_with_pass[first] = indent + "pass"
                drop.update(range(first + 1, last + 1))
            else:
                drop.update(range(first, last + 1))
        else:
            drop.update(range(first, last + 1))
    kept = []
    for index, line in enumerate(lines):
        if index in replace_with_pass:
            kept.append(replace_with_pass[index])
        elif index not in drop:
            kept.append(line)
    text = "\n".join(kept) + "\n"

    comment_cuts = {}
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            row, col = token.start
            comment_cuts[row - 1] = col
    out_lines = text.splitlines()
    for row, col in comment_cuts.items():
        out_lines[row] = out_lines[row][:col]
    cleaned = []
    blank = True
    for line in out_lines:
        line = line.rstrip()
        if not line:
            if not blank:
                cleaned.append("")
            blank = True
            continue
        cleaned.append(line)
        blank = False
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return "\n".join(cleaned) + "\n"


def build(source):
    body = strip_source(source)
    expected = remove_docstrings_from_tree(ast.parse(source))
    actual = ast.parse(body)
    if ast.dump(expected) != ast.dump(actual):
        raise SystemExit("stripped file does not have the same syntax tree as contract.py")
    first_lines = source.splitlines()[:2]
    if tuple(first_lines) != HEADER:
        raise SystemExit("contract.py must start with exactly the two required header lines")
    return "\n".join(HEADER) + "\n" + body


def main(argv):
    with open(SOURCE, encoding="utf-8") as handle:
        source = handle.read()
    # contract.py keeps the two header lines as its own first lines, so the
    # body to strip starts at line 3.
    lines = source.splitlines()
    if tuple(lines[:2]) != HEADER:
        raise SystemExit("contract.py must start with exactly the two required header lines")
    body_source = "\n".join(lines[2:]) + "\n"
    stripped = strip_source(body_source)
    expected = remove_docstrings_from_tree(ast.parse(body_source))
    if ast.dump(expected) != ast.dump(ast.parse(stripped)):
        raise SystemExit("stripped file does not have the same syntax tree as contract.py")
    result = "\n".join(HEADER) + "\n" + stripped
    if "--check" in argv:
        try:
            with open(TARGET, encoding="utf-8") as handle:
                current = handle.read()
        except FileNotFoundError:
            current = None
        if current != result:
            print("contract_deploy.py is stale: run python3 tools/make_deploy.py")
            return 1
        print("contract_deploy.py is up to date")
        return 0
    with open(TARGET, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(result)
    print(f"wrote {TARGET} ({len(result.splitlines())} lines from {len(lines)})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
