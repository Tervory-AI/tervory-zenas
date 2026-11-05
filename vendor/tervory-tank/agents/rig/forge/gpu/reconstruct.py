"""Credential-free reconstruction worker; all outputs remain drafts."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image


def measured_scale(scene, dimensions):
    longest = float(max(scene.extents))
    if not np.isfinite(longest) or longest <= 0:
        raise ValueError("Reconstruction is empty")
    # Photos have no absolute scale. Use the supplied longest measurement only;
    # the CPU forge independently checks all three dimensions and rejects drift.
    scene.apply_scale(max(dimensions) / 1000 / longest)


def photogrammetry(work, body):
    output = work / "meshroom-output"
    subprocess.run(["/opt/meshroom/meshroom_batch", "--input", str(work / "photos"),
                    "--output", str(output), "--cache", str(work / "cache"),
                    "--paramOverrides", "Texturing:colorMapping.colorMappingFileType=png"], check=True)
    models = list(output.rglob("texturedMesh.obj"))
    if len(models) != 1:
        raise ValueError("Meshroom did not produce one textured mesh")
    model = models[0]
    # Preserve Meshroom's OBJ, MTL and texture files together in the R2 gpu prefix.
    for path in model.parent.iterdir():
        if path.is_file() and path.suffix.lower() in {".obj", ".mtl", ".png", ".jpg", ".jpeg", ".exr"}:
            shutil.copyfile(path, work / "outputs" / path.name)
    scene = trimesh.load_scene(model, process=False)
    measured_scale(scene, body["dimensions_mm"])
    scene.export(work / "outputs" / "result.glb")


def trellis(work, body):
    import types
    import torch
    import o_voxel
    from trellis2.pipelines import Trellis2ImageTo3DPipeline, rembg

    # Do not load the upstream default commercial background-removal model.
    # Prepared photos are composited on a neutral matte; no extra model/service.
    class PreparedBackground:
        def __init__(self, **kwargs):
            pass
        def to(self, device):
            return self
    rembg.PreparedBackground = PreparedBackground
    pipeline = Trellis2ImageTo3DPipeline.from_pretrained("/opt/models/trellis2")
    pipeline.cuda()
    images = []
    Image.MAX_IMAGE_PIXELS = 32_000_000
    for name in body["inputs"]:
        with Image.open(work / "photos" / name) as photo:
            rgba = photo.convert("RGBA")
            matte = Image.new("RGBA", rgba.size, (128, 128, 128, 255))
            images.append(Image.alpha_composite(matte, rgba).convert("RGB"))
    # Upstream single-view API: concatenate every view's conditioning tokens.
    # This is explicitly a multi-view draft, never evidence of measured fit.
    original = pipeline.get_cond
    def all_views(self, image, resolution, include_neg_cond=True):
        result = original(images, resolution, include_neg_cond)
        return {key: torch.cat(value.unbind(0), dim=0).unsqueeze(0) for key, value in result.items()}
    pipeline.get_cond = types.MethodType(all_views, pipeline)
    mesh = pipeline.run(images[0], preprocess_image=False, seed=42, pipeline_type="512")[0]
    mesh.simplify(16777216)
    result = o_voxel.postprocess.to_glb(vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs,
        coords=mesh.coords, attr_layout=mesh.layout, voxel_size=mesh.voxel_size,
        aabb=[[-.5, -.5, -.5], [.5, .5, .5]], decimation_target=1000000,
        texture_size=1024, remesh=True, remesh_band=1, remesh_project=0, verbose=False)
    measured_scale(result, body["dimensions_mm"])
    result.export(work / "outputs" / "result.glb")


if __name__ == "__main__":
    method, directory = sys.argv[1:]
    work = Path(directory)
    body = json.loads((work / "manifest.json").read_text())
    (work / "outputs").mkdir()
    {"photogrammetry": photogrammetry, "trellis2": trellis}[method](work, body)
