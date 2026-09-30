"""Stage 7: exactly what ships. The import closure of the image's entry points.

    python scripts/phase4_final_scope.py            # print the closure and what else sits in src/
    python scripts/phase4_final_scope.py --write    # write docker/app_code_files.txt

The entry points are the image's commands: `python -m src.service` (the service),
`python -m src.service.cli` (the competition CLI) and `python -m src.service.healthcheck`
(the HEALTHCHECK). Every `import` and `from ... import` statement is followed, at module
level and inside functions (the code imports lazily), for modules of this repository only
(`src.*` and `scripts.*`). Package `__init__.py` files on the way are included, and so are the
data files a shipped module reads from its own package directory (the golden fixtures).
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY_POINTS = ("src.service.__main__", "src.service.cli", "src.service.healthcheck")
#: data a shipped module reads from its package directory (src/service/golden.py: GOLDEN_DIR)
DATA_FILES = ("src/service/golden/golden_expected.json", "src/service/golden/golden_fixtures.npz")
LIST_FILE = ROOT / "docker" / "app_code_files.txt"


def module_path(name: str) -> Path | None:
    base = ROOT.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def imported_names(path: Path, module: str) -> set[str]:
    """Every project module named by an import statement anywhere in `path`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            found.add(target)
            found.update(f"{target}.{alias.name}" for alias in node.names)   # `from pkg import module`
    return {n for n in found if n.split(".")[0] in ("src", "scripts") and module_path(n) is not None}


def closure(entries=ENTRY_POINTS) -> set[str]:
    seen: set[str] = set()
    todo = list(entries)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        parts = name.split(".")
        for i in range(1, len(parts)):                      # the packages on the way
            parent = ".".join(parts[:i])
            if module_path(parent) is not None and parent not in seen:
                todo.append(parent)
        todo.extend(imported_names(module_path(name), name) - seen)
    return seen


def closure_files() -> list[str]:
    files = {str(module_path(m).relative_to(ROOT)).replace("\\", "/") for m in closure()}
    return sorted(files | set(DATA_FILES))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    shipped = closure_files()
    in_src = sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "src").rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts)
    research = [f for f in in_src if f not in shipped]
    print(f"shipped code files: {len(shipped)}")
    for f in shipped:
        print("  ship", f)
    print(f"files in src/ outside the closure (research history): {len(research)}")
    for f in research:
        print("  research", f)
    if args.write:
        LIST_FILE.write_text("\n".join(shipped) + "\n", encoding="utf-8", newline="\n")
        print(f"written {LIST_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
