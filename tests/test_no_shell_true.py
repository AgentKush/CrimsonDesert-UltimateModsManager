"""Guard: no ``subprocess`` call in the codebase may use ``shell=True``.

``shell=True`` re-joins an argument list and hands it back to the shell for a
second round of parsing. Every ``cmd`` CDUMM builds is already a list whose
first element is an explicit interpreter, so the shell adds nothing except an
injection point.

This mattered concretely: the live-script runner in ``import_handler`` invoked
``["cmd", "/c", f'"{script_path}" & pause']`` with ``shell=True``. ``&`` is a
legal Windows filename character, so an imported mod named ``foo & calc.bat``
survived the quoting on the first pass and split into a second command on the
second -- turning a mod's *filename* into arbitrary command execution on the
user's machine.

Parsed with ``ast`` rather than grepped, so prose mentioning ``shell=True``
(such as the comments explaining this) does not trip the test.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _shell_true_calls(tree: ast.AST) -> list[int]:
    """Line numbers of calls passing a literal ``shell=True``."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if (
                kw.arg == "shell"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
            ):
                hits.append(node.lineno)
    return hits


def test_no_subprocess_shell_true() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - never expected in-tree
            continue
        for lineno in _shell_true_calls(tree):
            offenders.append(f"{path.relative_to(SRC)}:{lineno}")

    assert not offenders, (
        "shell=True reintroduces shell parsing of an already-quoted command "
        "list. Pass the argument list directly instead. Offenders: "
        + ", ".join(offenders)
    )


def test_guard_detects_shell_true() -> None:
    """The guard must actually fire -- otherwise it silently passes forever."""
    tree = ast.parse("import subprocess\nsubprocess.Popen(['x'], shell=True)\n")
    assert _shell_true_calls(tree) == [2]

    safe = ast.parse("import subprocess\nsubprocess.Popen(['x'], shell=False)\n")
    assert _shell_true_calls(safe) == []
