from pathlib import Path

from yamibo.waf import WafSolver

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_challenge.js"


def test_solve_synthetic_challenge():
    solver = WafSolver(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36"
    )
    token = solver.solve_script(FIXTURE.read_text(encoding="utf-8"))
    assert token.startswith("2.0_")
    assert "_synthetic_" in token


def test_solve_returns_none_on_failure():
    solver = WafSolver(user_agent="ua")
    assert solver.solve_script("this is not javascript; {{{") is None
