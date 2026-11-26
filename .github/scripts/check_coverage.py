"""Fail when line or branch coverage is below its minimum.

Reads the structured totals of a coverage.py JSON report (`--cov-report=json`).
The combined percentage of coverage.py mixes lines and branches, so each is
checked separately here.

Usage: python check_coverage.py coverage.json --min-line 99 --min-branch 98
"""

import argparse
import json
import sys


def percent(covered: int, total: int) -> float:
    return covered * 100 / total


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", help="coverage.py JSON report")
    parser.add_argument("--min-line", type=float, required=True, help="minimum line coverage in percent")
    parser.add_argument("--min-branch", type=float, required=True, help="minimum branch coverage in percent")
    args = parser.parse_args(argv)

    with open(args.report, encoding="utf-8") as report_file:
        totals = json.load(report_file)["totals"]

    statements = totals["num_statements"]
    covered_lines = totals["covered_lines"]
    branches = totals.get("num_branches")
    covered_branches = totals.get("covered_branches")

    # An empty measurement must not pass, for example when no package code was imported.
    if statements == 0:
        print("ERROR: the coverage report contains no measured statements")
        return 1
    if not branches:
        print("ERROR: the coverage report contains no branch data (branch coverage disabled?)")
        return 1

    line_percent = percent(covered_lines, statements)
    branch_percent = percent(covered_branches, branches)
    print(f"Line coverage:   {covered_lines}/{statements} = {line_percent:.2f}% (minimum {args.min_line:g}%)")
    print(f"Branch coverage: {covered_branches}/{branches} = {branch_percent:.2f}% (minimum {args.min_branch:g}%)")

    failed = False
    if line_percent < args.min_line:
        print("ERROR: line coverage is below the minimum")
        failed = True
    if branch_percent < args.min_branch:
        print("ERROR: branch coverage is below the minimum")
        failed = True

    if failed:
        return 1
    print("Coverage quality gate passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
