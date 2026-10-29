"""Compare two package trees by behaviour: Python by AST (ignoring docstrings and imports), the rest byte-for-byte.

Usage: python scripts/compare_trees.py <dir_a> <dir_b>    (exit 0 if equivalent)
"""

import os
import ast
import sys
import filecmp


def _files(root: str) -> set[str]:
    out = set()
    for d, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        out.update(os.path.relpath(os.path.join(d, f), root) for f in fs if not f.endswith(".pyc"))
    return out


def _norm(path: str) -> str:
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(getattr(body[0], "value", None), ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    tree.body = [s for s in tree.body if not isinstance(s, (ast.Import, ast.ImportFrom))]
    return ast.dump(tree, annotate_fields=False)


def main(a: str, b: str) -> int:
    """Print differences and return 0 when the trees behave the same."""
    fa, fb = _files(a), _files(b)
    problems = [f"only in {a}: {f}" for f in sorted(fa - fb)] + [f"only in {b}: {f}" for f in sorted(fb - fa)]
    for f in sorted(fa & fb):
        pa, pb = os.path.join(a, f), os.path.join(b, f)
        same = _norm(pa) == _norm(pb) if f.endswith(".py") else filecmp.cmp(pa, pb, shallow=False)
        if not same:
            problems.append(f"differs: {f}")
    print("\n".join(problems) if problems else f"equivalent ({len(fa)} files)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
