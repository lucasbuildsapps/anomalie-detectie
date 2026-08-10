"""Structural enforcement of the causal contract.

ARCHITECTURE_V2.md §2.1 claims that leakage is made structurally impossible
rather than merely discouraged. A claim like that is worth exactly as much as
the test that enforces it, so this file is that test.

The rule: nothing under `sentinel/core/` may reach the storage layer directly.
Data enters through `AsOfView`, which is the only component allowed to know
where rows come from. As `core/baseline/`, `core/detect/` and the rest grow,
this test keeps the boundary intact without anyone having to remember it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SENTINEL_CORE = Path(__file__).resolve().parent.parent / "sentinel" / "core"

#: The adapter that is allowed to bridge to storage, and only lazily.
_STORAGE_ADAPTER = SENTINEL_CORE / "time" / "as_of.py"

#: Modules whose names indicate a storage dependency.
_FORBIDDEN_ROOTS = {"core.storage", "storage", "sqlalchemy", "psycopg"}


def _python_files() -> list[Path]:
    return sorted(p for p in SENTINEL_CORE.rglob("*.py")
                  if p.name != "__init__.py")


def _imported_modules(tree: ast.Module) -> list[tuple[str, int, bool]]:
    """(module, lineno, is_module_level) for every import in the tree.

    "Module level" means a direct child of the module body — an import that
    runs on `import`. Imports nested inside a function are deliberate lazy
    bridges and are allowed.
    """
    top_level = {id(node) for node in tree.body}
    out: list[tuple[str, int, bool]] = []
    for node in ast.walk(tree):
        at_top = id(node) in top_level
        if isinstance(node, ast.Import):
            out.extend((a.name, node.lineno, at_top) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.module, node.lineno, at_top))
    return out


def _is_forbidden(module: str) -> bool:
    parts = module.split(".")
    return any(
        ".".join(parts[:n]) in _FORBIDDEN_ROOTS
        for n in range(1, len(parts) + 1)
    )


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_core_modules_do_not_import_storage_at_module_level(path: Path):
    """Core logic must receive data, not fetch it.

    A module-level storage import is the point at which a module stops being
    testable without a database and starts being able to read whatever it
    likes — including rows from the future.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders = [
        (mod, lineno)
        for mod, lineno, module_level in _imported_modules(tree)
        if module_level and _is_forbidden(mod)
    ]
    if path == _STORAGE_ADAPTER:
        # The adapter may bridge to storage, but only inside a function, so
        # that importing it never requires a database.
        assert not offenders, (
            f"{path.name} must import storage lazily, inside the function "
            f"that uses it: {offenders}"
        )
        return
    assert not offenders, (
        f"{path.relative_to(SENTINEL_CORE)} imports storage directly at "
        f"module level: {offenders}. Take an AsOfView instead."
    )


def test_the_boundary_test_actually_sees_files():
    """Guard against the rule passing vacuously as the tree is still small."""
    files = _python_files()
    assert files, "no modules found under sentinel/core — check the path"
    assert _STORAGE_ADAPTER in files


def test_as_of_module_imports_without_a_database():
    """The core must be usable in tests and in the entity layer without a DB."""
    import importlib

    module = importlib.import_module("sentinel.core.time.as_of")
    assert hasattr(module, "AsOfView")


def test_forbidden_detection_matches_submodules():
    assert _is_forbidden("core.storage")
    assert _is_forbidden("sqlalchemy.orm")
    assert not _is_forbidden("core.estimative")
    assert not _is_forbidden("pandas")
