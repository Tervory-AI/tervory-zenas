"""Geometry checks do not repair invalid input or silently guess its scale."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Budget:
    triangles: int = 100_000
    bytes: int = 8_000_000
    min_mm: float = 0.01
    max_mm: float = 100_000.0


def check(path: Path, *, unit_mm: float, expected_mm: tuple[float, float, float],
          budget: Budget = Budget(), encoded_bytes: int | None = None) -> dict:
    """Check each connected shell, including small inverted parts of a large mesh.

    glTF coordinates are metres (unit_mm=1000); OBJ inputs explicitly declare
    their units. Dimensions come from the job's measured dimensions, never AI.
    A failed check yields a report and cannot enter the human approval queue.
    """
    # Only the bounded worker imports native parsers; the service imports Budget.
    import manifold3d
    import numpy as np
    import trimesh

    checks = {key: False for key in ("finite", "watertight", "outward", "manifold", "dimensions", "budget")}
    report = {"passed": False, "checks": checks, "triangles": 0,
              "bytes": path.stat().st_size if encoded_bytes is None else encoded_bytes,
              "dimensions_mm": [], "reasons": [], "status": "rejected"}
    if not np.isfinite(unit_mm) or unit_mm <= 0:
        report["reasons"] = ["Input units must be a positive millimetre scale."]
        return report
    try:
        scene = trimesh.load_scene(path, process=False, skip_materials=True)
        meshes = scene.to_geometry()
        if not isinstance(meshes, trimesh.Trimesh) or not len(meshes.faces):
            raise ValueError("empty mesh")
        # OBJ can repeat vertices at face boundaries. Weld coincident
        # positions without fixing normals, filling holes, or reversing winding.
        meshes.merge_vertices()
        finite = bool(np.isfinite(meshes.vertices).all())
        checks["finite"] = finite and bool(np.all(meshes.area_faces > 0))
        if not checks["finite"]:
            raise ValueError("non-finite or degenerate geometry")
        extents = np.asarray(meshes.extents) * unit_mm
        expected = np.asarray(expected_mm, dtype=float)
        report["dimensions_mm"] = extents.tolist()
        report["triangles"] = len(meshes.faces)
        checks["dimensions"] = bool(
            expected.shape == (3,) and np.isfinite(expected).all()
            and np.all(expected >= budget.min_mm) and np.all(expected <= budget.max_mm)
            and np.all(extents >= budget.min_mm) and np.all(extents <= budget.max_mm)
            and np.allclose(np.sort(extents), np.sort(expected), rtol=0.02, atol=0.01))
        components = meshes.split(only_watertight=False, repair=False)
        checks["watertight"] = bool(len(components)) and all(m.is_watertight for m in components)
        checks["outward"] = bool(len(components)) and all(
            m.is_winding_consistent and m.volume > 0 for m in components)
        checks["manifold"] = bool(len(components)) and all(
            manifold3d.Manifold(manifold3d.Mesh(
                vert_properties=np.asarray(m.vertices, dtype=np.float32),
                tri_verts=np.asarray(m.faces, dtype=np.uint32))).status() == manifold3d.Error.NoError
            for m in components)
        checks["budget"] = report["triangles"] <= budget.triangles and report["bytes"] <= budget.bytes
    except (ValueError, TypeError, IndexError, OSError):
        report["reasons"].append("The model could not be read as nondegenerate triangles.")
    messages = {"finite": "The model has empty, degenerate or non-finite geometry.",
                "watertight": "The model has open holes or nonmanifold edges.",
                "outward": "A connected part has inward or inconsistent faces.",
                "manifold": "The mesh is not an oriented two-manifold.",
                "dimensions": "Dimensions do not match the measured millimetre dimensions.",
                "budget": "The model exceeds the triangle or file-size budget."}
    report["reasons"].extend(messages[name] for name, passed in checks.items() if not passed)
    report["passed"] = all(checks.values())
    report["status"] = "draft" if report["passed"] else "rejected"
    report["budget"] = asdict(budget)
    return report


if __name__ == "__main__":
    import json
    import sys
    from inputs import embedded_mesh

    source, destination, settings = sys.argv[1:]
    options = json.loads(settings)
    options["budget"] = Budget(**options["budget"])
    embedded_mesh(Path(source))
    result = check(Path(source), **options)
    Path(destination).write_text(json.dumps(result))
