"""Create an unregistered vertical package from the neutral template, offline."""
import argparse
import json
from pathlib import Path
import re
import shutil


def scaffold(name: str, destination: Path) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name) or name == "ffl":
        raise ValueError("Choose a new vertical identifier")
    target = destination / name
    shutil.copytree(Path(__file__).parent / "template", target)
    manifest = target / "manifest.json"
    data = json.loads(manifest.read_text())
    data["id"] = name
    manifest.write_text(json.dumps(data, indent=2) + "\n")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    scaffold(args.name, args.destination)
