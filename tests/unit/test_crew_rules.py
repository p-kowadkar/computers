"""Rules about how the crew is written (spec S8 R3.3): code as action stays off."""
import ast
from pathlib import Path

import pytest

CREW = Path(__file__).resolve().parents[2] / "src" / "computers" / "crew"


def ellipsis_methods_without_predict(path: Path) -> list[str]:
    """In classes that subclass `Agent`: methods whose body is `...` (after an optional docstring) without `@strategy(PredictStrategy(...))`.

    Protocol stubs elsewhere also end in `...`; they are not agent methods and are not checked.
    """
    bad = []
    tree = ast.parse(path.read_text())
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        if not any(getattr(b, "id", None) == "Agent" for b in cls.bases):
            continue
        for node in cls.body:
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
                body = body[1:]
            if not (len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and body[0].value.value is Ellipsis):
                continue
            ok = False
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and getattr(d.func, "id", None) == "strategy" and d.args:
                    a = d.args[0]
                    ok = ok or (isinstance(a, ast.Call) and getattr(a.func, "id", None) == "PredictStrategy")
            if not ok:
                bad.append(f"{path.name}:{node.name}")
    return bad


def agent_ellipsis_methods(path: Path) -> list[str]:
    """Names of every ellipsis method in Agent subclasses, so the rule can be shown not to be vacuous."""
    tree = ast.parse(path.read_text())
    out = []
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        if any(getattr(b, "id", None) == "Agent" for b in cls.bases):
            out += [n.name for n in cls.body if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
                    and isinstance(n.body[-1], ast.Expr) and getattr(n.body[-1].value, "value", None) is Ellipsis]  # fmt: skip
    return out


def test_every_ellipsis_method_in_the_crew_carries_predict_strategy():
    files = sorted(CREW.glob("*.py"))
    assert files
    assert [m for f in files for m in ellipsis_methods_without_predict(f)] == []


def test_the_crew_has_at_least_one_ellipsis_method_so_the_check_is_not_vacuous():
    found = [m for f in CREW.glob("*.py") for m in agent_ellipsis_methods(f)]
    assert "argue_artifact" in found


@pytest.mark.parametrize(
    "decorators",
    ["", "@strategy(CodeActStrategy())\n    ", "@strategy()\n    ", "@other(PredictStrategy())\n    ", "@strategy('predict')\n    "],
)
def test_a_class_that_breaks_the_rule_is_caught(tmp_path, decorators):
    f = tmp_path / "bad.py"
    f.write_text(f"class A(Agent):\n    {decorators}async def argue(self, x):\n        '''prompt'''\n        ...\n")
    assert ellipsis_methods_without_predict(f) == ["bad.py:argue"]


def test_a_method_with_a_real_body_needs_no_strategy(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("class A(Agent):\n    def hits(self, p):\n        return []\n    @strategy(PredictStrategy(config=C))\n    async def argue(self, x):\n        '''p'''\n        ...\n")
    assert ellipsis_methods_without_predict(f) == []


def test_an_ellipsis_in_a_class_that_is_not_an_agent_is_not_checked(tmp_path):
    f = tmp_path / "proto.py"
    f.write_text("class P(Protocol):\n    def post(self, x): ...\n")
    assert ellipsis_methods_without_predict(f) == []
