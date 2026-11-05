"""Read-only nightly CLI; no package installs, publication or credential creation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from . import inventory, scan
from .http import Client


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    read_client = Client(os.environ.get("GITHUB_TOKEN", ""))
    report = scan(inventory(args.root), read_client)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Locks: {len(report['coverage'])}; dependencies: {report['dependency_count']}; matches: {len(report['findings'])}")
    if not report["complete"]:
        print("Supply-chain scan incomplete; see the report. No changes made.")
        return 1
    return int(bool(report["findings"]))


if __name__ == "__main__":
    sys.exit(main())
