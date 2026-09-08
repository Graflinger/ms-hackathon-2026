from goldenloop_eval.cli import main as eval_main

from . import run_case


def main(argv: list[str] | None = None) -> int:
    return eval_main(argv, runner=run_case)
