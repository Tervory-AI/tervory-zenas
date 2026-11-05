"""Reject references that could make native importers read another job's files."""
import json
import struct
from pathlib import Path


def embedded_mesh(path: Path):
    if path.stat().st_size > 256_000_000:
        raise ValueError("Input exceeds the ingestion budget.")
    if path.suffix.lower() == ".obj":
        with path.open() as source:
            for line in source:
                words = line.split()
                if words and words[0].lower() in {"mtllib", "call", "csh"}:
                    raise ValueError("OBJ external files are not accepted; upload an embedded GLB.")
    if path.suffix.lower() == ".glb":
        with path.open("rb") as source:
            header = source.read(20)
            if len(header) != 20:
                raise ValueError("Invalid GLB header.")
            magic, version, total, size, kind = struct.unpack("<5I", header)
            if magic != 0x46546C67 or version != 2 or total != path.stat().st_size or kind != 0x4E4F534A or size > total - 20:
                raise ValueError("Invalid GLB header.")
            document = json.loads(source.read(size))
        for entry in document.get("buffers", []) + document.get("images", []):
            if "uri" in entry and not entry["uri"].startswith("data:"):
                raise ValueError("GLB external files are not accepted.")
