"""
Backend regression tests for the differential-equation solver.

The tests call solver.py directly, so they do not need a running Flask server.
They do require a working Mathematica/Wolfram Engine installation for cases
that actually evaluate DSolve/NDSolve.
"""

from __future__ import annotations

import argparse
import sys
import time
import unittest
from dataclasses import dataclass, field
from typing import Callable

from solver import SolveRequest, preview_expression, solve
from wolfram_session import get_session


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    group: str
    request: SolveRequest | None = None
    preview: tuple[str, str, list[str], list[str]] | None = None
    expect_ok: bool = True
    raw_contains: tuple[str, ...] = ()
    latex_contains: tuple[str, ...] = ()
    normalized_contains: tuple[str, ...] = ()
    warning_contains: tuple[str, ...] = ()
    error_contains: tuple[str, ...] = ()
    image_min_chars: int = 0
    slow: bool = False
    reason: str = ""


CASES: list[Case] = [
    Case(
        id="preview-latex-leibniz",
        title="Preview LaTeX Leibniz derivative",
        group="preview",
        preview=(r"\frac{d^2 y}{dx^2} + y = 0", "latex", ["y"], ["x"]),
        normalized_contains=("Derivative[2][y][x]",),
        latex_contains=("y",),
    ),
    Case(
        id="analytic-harmonic-general",
        title="Analytic harmonic oscillator, general solution",
        group="analytic",
        request=SolveRequest(
            kind="ode",
            mode="analytic",
            equations=["y''[x] + y[x] == 0"],
            functions=["y"],
            variables=["x"],
        ),
        raw_contains=("Cos[x]", "Sin[x]"),
    ),
    Case(
        id="analytic-harmonic-ivp",
        title="Analytic harmonic oscillator with initial values",
        group="analytic",
        request=SolveRequest(
            kind="ode",
            mode="analytic",
            equations=["y''[x] + y[x] == 0"],
            conditions=["y[0] == 1", "y'[0] == 0"],
            functions=["y"],
            variables=["x"],
        ),
        raw_contains=("Cos[x]",),
    ),
    Case(
        id="analytic-exponential-growth",
        title="Analytic first-order exponential growth",
        group="analytic",
        request=SolveRequest(
            kind="ode",
            mode="analytic",
            equations=["y'[x] == y[x]"],
            functions=["y"],
            variables=["x"],
        ),
        raw_contains=("E^x",),
    ),
    Case(
        id="analytic-inhomogeneous-first-order",
        title="Analytic inhomogeneous first-order ODE",
        group="analytic",
        request=SolveRequest(
            kind="ode",
            mode="analytic",
            equations=["y'[x] + y[x] == Exp[-x]"],
            conditions=["y[0] == 0"],
            functions=["y"],
            variables=["x"],
        ),
        raw_contains=("x/E^x",),
    ),
    Case(
        id="analytic-linear-system",
        title="Analytic two-equation linear ODE system",
        group="analytic",
        request=SolveRequest(
            kind="ode_system",
            mode="analytic",
            equations=["x'[t] == y[t]", "y'[t] == -x[t]"],
            conditions=["x[0] == 1", "y[0] == 0"],
            functions=["x", "y"],
            variables=["t"],
        ),
        raw_contains=("Cos[t]", "Sin[t]"),
    ),
    Case(
        id="numeric-logistic",
        title="Numeric logistic equation",
        group="numeric",
        request=SolveRequest(
            kind="ode",
            mode="numeric",
            equations=["y'[t] == 2 y[t] (1 - y[t])"],
            conditions=["y[0] == 0.1"],
            functions=["y"],
            variables=["t"],
            plot_range="{t, 0, 5}",
        ),
        raw_contains=("InterpolatingFunction",),
    ),
    Case(
        id="numeric-system-plot",
        title="Numeric harmonic ODE system with plot",
        group="numeric",
        request=SolveRequest(
            kind="ode_system",
            mode="numeric",
            equations=["x'[t] == y[t]", "y'[t] == -x[t]"],
            conditions=["x[0] == 1", "y[0] == 0"],
            functions=["x", "y"],
            variables=["t"],
            plot_range="{t, 0, 2 Pi}",
            plot=True,
        ),
        raw_contains=("InterpolatingFunction",),
        image_min_chars=5000,
    ),
    Case(
        id="numeric-vanderpol",
        title="Numeric nonlinear Van der Pol equation",
        group="numeric",
        request=SolveRequest(
            kind="ode",
            mode="numeric",
            equations=["x''[t] - (1 - x[t]^2) x'[t] + x[t] == 0"],
            conditions=["x[0] == 2", "x'[0] == 0"],
            functions=["x"],
            variables=["t"],
            plot_range="{t, 0, 10}",
        ),
        raw_contains=("InterpolatingFunction",),
    ),
    Case(
        id="numeric-damped-forced",
        title="Numeric damped forced oscillator",
        group="numeric",
        request=SolveRequest(
            kind="ode",
            mode="numeric",
            equations=["x''[t] + 0.2 x'[t] + x[t] == Sin[1.5 t]"],
            conditions=["x[0] == 0", "x'[0] == 0"],
            functions=["x"],
            variables=["t"],
            plot_range="{t, 0, 20}",
        ),
        raw_contains=("InterpolatingFunction",),
    ),
    Case(
        id="numeric-heat-pde-plot",
        title="Numeric heat equation PDE with 3D plot",
        group="pde",
        request=SolveRequest(
            kind="pde",
            mode="numeric",
            equations=["D[u[x,t],t] == D[u[x,t],{x,2}]"],
            conditions=["u[x,0] == Sin[x]", "u[0,t] == 0", "u[Pi,t] == 0"],
            functions=["u"],
            variables=["x", "t"],
            plot_range="{x, 0, Pi}, {t, 0, 1}",
            plot=True,
        ),
        raw_contains=("InterpolatingFunction",),
        image_min_chars=5000,
        slow=True,
    ),
    Case(
        id="validation-bare-equals",
        title="Reject Wolfram single equals",
        group="validation",
        request=SolveRequest(
            kind="ode",
            mode="analytic",
            equations=["y'[x] = y[x]"],
            functions=["y"],
            variables=["x"],
        ),
        expect_ok=False,
        error_contains=("应当用 ==",),
    ),
    Case(
        id="validation-parentheses-warning",
        title="Warn about y(x) in Wolfram mode",
        group="validation",
        request=SolveRequest(
            kind="ode",
            mode="analytic",
            equations=["y(x) == x"],
            functions=["y"],
            variables=["x"],
        ),
        expect_ok=False,
        warning_contains=("圆括号",),
    ),
    Case(
        id="security-runprocess",
        title="Reject unsafe RunProcess call",
        group="security",
        request=SolveRequest(
            kind="ode",
            mode="analytic",
            equations=['RunProcess[{"echo","bad"}] == 0'],
            functions=["y"],
            variables=["x"],
        ),
        expect_ok=False,
        error_contains=("不允许", "RunProcess"),
    ),
    Case(
        id="security-invalid-identifier",
        title="Reject invalid function identifier",
        group="security",
        request=SolveRequest(
            kind="ode",
            mode="analytic",
            equations=["y[x] == 0"],
            functions=["y;Run"],
            variables=["x"],
        ),
        expect_ok=False,
        error_contains=("未知函数", "只能包含"),
    ),
    Case(
        id="security-plot-range-compound-expression",
        title="Reject compound expression in plot range",
        group="security",
        request=SolveRequest(
            kind="ode",
            mode="numeric",
            equations=["y'[x] == y[x]"],
            conditions=["y[0] == 1"],
            functions=["y"],
            variables=["x"],
            plot_range='{x, 0, 10}; Run["bad"]',
        ),
        expect_ok=False,
        error_contains=("绘图区间", "分号"),
    ),
    Case(
        id="messages-underdetermined-system",
        title="Return Wolfram message for underdetermined NDSolve",
        group="messages",
        request=SolveRequest(
            kind="ode_system",
            mode="numeric",
            equations=["x'[t] == y[t]"],
            conditions=["x[0] == 1"],
            functions=["x", "y"],
            variables=["t"],
            plot_range="{t, 0, 1}",
        ),
        expect_ok=False,
        warning_contains=("NDSolve::underdet",),
    ),
]


def _contains_all(text: str, pieces: tuple[str, ...]) -> bool:
    return all(piece in text for piece in pieces)


def run_case(test: unittest.TestCase, case: Case) -> None:
    start = time.perf_counter()
    if case.preview:
        text, fmt, functions, variables = case.preview
        result = preview_expression(text, fmt, functions, variables)
        elapsed = time.perf_counter() - start
        test.assertEqual(result["ok"], case.expect_ok, msg=result)
        test.assertTrue(_contains_all(result.get("latex", ""), case.latex_contains), msg=result)
        test.assertTrue(_contains_all(result.get("wolfram", ""), case.normalized_contains), msg=result)
        print(f"[{case.id}] {elapsed:.2f}s")
        return

    assert case.request is not None
    result = solve(case.request)
    elapsed = time.perf_counter() - start
    test.assertEqual(result.ok, case.expect_ok, msg=result.to_dict())
    test.assertTrue(_contains_all(result.raw, case.raw_contains), msg=result.to_dict())
    test.assertTrue(_contains_all(result.latex, case.latex_contains), msg=result.to_dict())
    test.assertTrue(
        _contains_all("\n".join(result.normalized_eqs + result.normalized_conds), case.normalized_contains),
        msg=result.to_dict(),
    )
    test.assertTrue(_contains_all(result.error, case.error_contains), msg=result.to_dict())
    test.assertTrue(_contains_all("\n".join(result.warnings), case.warning_contains), msg=result.to_dict())
    if case.image_min_chars:
        test.assertGreaterEqual(len(result.image_base64), case.image_min_chars, msg=result.to_dict())
    print(f"[{case.id}] {elapsed:.2f}s")


def build_suite(selected: list[Case]) -> unittest.TestSuite:
    class BackendSolverTests(unittest.TestCase):
        pass

    def make_test(case: Case) -> Callable[[unittest.TestCase], None]:
        def test_method(self: unittest.TestCase) -> None:
            run_case(self, case)

        test_method.__name__ = f"test_{case.id.replace('-', '_')}"
        test_method.__doc__ = case.title
        return test_method

    for case in selected:
        setattr(BackendSolverTests, f"test_{case.id.replace('-', '_')}", make_test(case))

    return unittest.defaultTestLoader.loadTestsFromTestCase(BackendSolverTests)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run backend tests for the differential-equation solver.")
    parser.add_argument("--list", action="store_true", help="List test cases and exit.")
    parser.add_argument("--include-slow", action="store_true", help="Include slow PDE/plot cases.")
    parser.add_argument("--group", action="append", help="Run only a group. Can be repeated.")
    parser.add_argument("--case", action="append", help="Run only a case id. Can be repeated.")
    parser.add_argument("--failfast", action="store_true", help="Stop on first failure.")
    return parser.parse_args(argv)


def select_cases(args: argparse.Namespace) -> list[Case]:
    selected = CASES
    if not args.include_slow:
        selected = [case for case in selected if not case.slow]
    if args.group:
        groups = set(args.group)
        selected = [case for case in selected if case.group in groups]
    if args.case:
        ids = set(args.case)
        selected = [case for case in selected if case.id in ids]
    return selected


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    selected = select_cases(args)

    if args.list:
        for case in selected:
            slow = " slow" if case.slow else ""
            print(f"{case.id:42s} group={case.group}{slow}  {case.title}")
        return 0

    if not selected:
        print("No test cases selected.", file=sys.stderr)
        return 2

    try:
        suite = build_suite(selected)
        result = unittest.TextTestRunner(verbosity=2, failfast=args.failfast).run(suite)
        return 0 if result.wasSuccessful() else 1
    finally:
        try:
            get_session().terminate()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
