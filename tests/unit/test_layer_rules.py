"""Layer rules from plan 4.2 and principle 1, enforced by reading the source (R11).

* Nothing below L3 may import an LLM client or an agent framework.
* L1 is deterministic code and must not import from L2 or the crew.

The check parses files with `ast` and never imports them.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "computers"

BELOW_L3 = ("data", "l1", "l2", "bench", "telemetry")
LLM_PACKAGES = {"nooa", "litellm", "openai", "anthropic"}
L1_FORBIDDEN_INTERNAL = {"computers.l2", "computers.crew"}
# structure.md: data is the base, and bench scores the layers below the crew.
BENCH_FORBIDDEN_INTERNAL = {"computers.crew", "computers.cli", "computers.mcp", "computers.ml"}
DATA_FORBIDDEN_INTERNAL = {
    "computers.l1", "computers.l2", "computers.bench", "computers.crew", "computers.cli",
    "computers.mcp", "computers.ml",
}  # fmt: skip


def _hits(mod: str, forbidden: set[str]) -> bool:
    return any(mod == bad or mod.startswith(bad + ".") for bad in forbidden)


def imported_modules(path: Path, package: str) -> set[str]:
    """Absolute dotted names imported by one file. Relative imports are resolved.

    `package` is the dotted package the file lives in, for example `computers.l1`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                prefix = ".".join(base + ([node.module] if node.module else []))
            else:
                prefix = node.module or ""
            found.add(prefix)
            # `from computers import l2` imports the submodule `computers.l2`.
            found.update(f"{prefix}.{alias.name}" for alias in node.names)
    return found


def package_of(path: Path, src_root: Path) -> str:
    rel = path.relative_to(src_root).parent
    return ".".join(["computers", *rel.parts])


def violations(src_root: Path) -> list[str]:
    out = []
    for layer in BELOW_L3:
        folder = src_root / layer
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.py")):
            mods = imported_modules(path, package_of(path, src_root))
            rel = path.relative_to(src_root).as_posix()
            for mod in sorted(mods):
                if mod.split(".")[0] in LLM_PACKAGES:
                    out.append(f"{rel}: imports {mod}; no LLM client or agent framework below L3")
                if layer == "l1" and any(
                    mod == bad or mod.startswith(bad + ".") for bad in L1_FORBIDDEN_INTERNAL
                ):
                    out.append(f"{rel}: imports {mod}; L1 must not depend on L2 or the crew")
                if layer == "bench" and _hits(mod, BENCH_FORBIDDEN_INTERNAL):
                    out.append(f"{rel}: imports {mod}; bench may import data, l1 and l2 only")
                if layer == "data" and _hits(mod, DATA_FORBIDDEN_INTERNAL):
                    out.append(f"{rel}: imports {mod}; data is the base layer and imports nothing above it")
    return out


# ----------------------------------------------------------------- the real tree


def test_the_real_source_tree_obeys_the_layer_rules():
    assert violations(SRC) == []


def test_the_scanned_packages_exist_or_are_skipped_cleanly():
    # Some layers have no code yet; the scan must not trip over that.
    assert isinstance(violations(SRC), list)


# ----------------------------------------------------- the checker catches things


def make_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    src = tmp_path / "src" / "computers"
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return src


@pytest.mark.parametrize("layer", BELOW_L3)
@pytest.mark.parametrize(
    "statement",
    [
        "import openai",
        "import openai.types",
        "from openai import OpenAI",
        "import nooa",
        "from nooa import Agent",
        "import litellm",
        "import anthropic",
        "def f():\n    import litellm\n",  # an import inside a function still counts
    ],
)
def test_llm_imports_are_flagged_in_every_lower_layer(tmp_path, layer, statement):
    src = make_tree(tmp_path, {f"{layer}/mod.py": statement + "\n"})
    found = violations(src)
    assert len(found) >= 1 and "no LLM client" in found[0]


def test_crew_is_allowed_to_import_an_agent_framework(tmp_path):
    src = make_tree(tmp_path, {"crew/maury.py": "from nooa import Agent\n"})
    assert violations(src) == []


def test_ordinary_imports_are_fine(tmp_path):
    src = make_tree(
        tmp_path,
        {"l1/mod.py": "import numpy as np\nimport requests\nfrom computers.data import schemas\n"},
    )
    assert violations(src) == []


@pytest.mark.parametrize(
    ("where", "statement"),
    [
        ("l1/mod.py", "from computers.l2 import rank"),
        ("l1/mod.py", "import computers.l2.rank"),
        ("l1/mod.py", "from computers import l2"),
        ("l1/mod.py", "from ..l2 import rank"),  # .. from computers.l1 is computers
        ("l1/mod.py", "from .. import crew"),
        ("l1/mod.py", "from computers.crew.maury import Maury"),
        ("l1/sub/mod.py", "from computers.l2 import rank"),
        ("l1/sub/mod.py", "from ...l2 import rank"),  # ... from computers.l1.sub is computers
    ],
)
def test_l1_importing_l2_or_crew_is_flagged(tmp_path, where, statement):
    src = make_tree(tmp_path, {where: statement + "\n"})
    found = violations(src)
    assert found and "L1 must not depend on L2 or the crew" in found[0]


def test_two_dots_inside_a_subpackage_stay_inside_l1(tmp_path):
    # `..l2` from computers.l1.sub is computers.l1.l2, which is not the L2 layer.
    src = make_tree(tmp_path, {"l1/sub/mod.py": "from ..l2 import rank\n"})
    assert violations(src) == []


def test_l2_may_import_l1_but_l1_may_not_import_l2(tmp_path):
    src = make_tree(tmp_path, {"l2/mod.py": "from computers.l1 import triggers\n"})
    assert violations(src) == []


def test_a_module_named_like_an_llm_package_is_not_confused_with_it(tmp_path):
    src = make_tree(tmp_path, {"l1/mod.py": "from computers.data import openai_notes\n"})
    assert violations(src) == []


def test_relative_import_resolution():
    p = ROOT / "tests" / "unit" / "test_layer_rules.py"
    # Sanity check on the resolver with a hand-built file.
    tmp = p.parent / "_tmp_resolver_check.py"
    tmp.write_text("from . import a\nfrom .. import b\nfrom ..x import c\n")
    try:
        mods = imported_modules(tmp, "computers.l1.sub")
    finally:
        tmp.unlink()
    assert {"computers.l1.sub", "computers.l1.sub.a"} <= mods
    assert {"computers.l1", "computers.l1.b"} <= mods
    assert {"computers.l1.x", "computers.l1.x.c"} <= mods


@pytest.mark.parametrize(
    "statement",
    ["from computers.crew import maury", "import computers.cli", "from computers import mcp",
     "from computers.ml.train import fit", "from ..crew import x"],
)
def test_bench_importing_the_crew_cli_mcp_or_ml_is_flagged(tmp_path, statement):
    src = make_tree(tmp_path, {"bench/mod.py": statement + "\n"})
    found = violations(src)
    assert found and "bench may import data, l1 and l2 only" in found[0]


def test_bench_may_import_data_l1_and_l2(tmp_path):
    src = make_tree(
        tmp_path,
        {"bench/mod.py": "from computers.data import silver\nfrom computers.l1 import x\nfrom computers.l2 import y\n"},
    )
    assert violations(src) == []


@pytest.mark.parametrize(
    "statement",
    ["from computers.l1 import x", "from computers.bench import run", "import computers.crew",
     "from .. import l2", "from ..cli import main"],
)
def test_data_importing_anything_above_it_is_flagged(tmp_path, statement):
    src = make_tree(tmp_path, {"data/mod.py": statement + "\n"})
    found = violations(src)
    assert found and "data is the base layer" in found[0]


def test_data_may_import_the_thresholds_registry(tmp_path):
    src = make_tree(tmp_path, {"data/mod.py": "from computers.thresholds import THRESHOLDS\nfrom computers.data import silver\n"})
    assert violations(src) == []
