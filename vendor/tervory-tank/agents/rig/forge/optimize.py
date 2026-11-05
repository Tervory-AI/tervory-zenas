"""Run only as Blender's separate GPL program: blender -b -P optimize.py -- ..."""
import json
import sys
from pathlib import Path

import bpy


def main():
    source, destination, scale, target = sys.argv[sys.argv.index("--") + 1:]
    scale, target = float(scale), int(target)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    suffix = Path(source).suffix.lower()
    if suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=source)
    elif suffix == ".ply":
        bpy.ops.wm.ply_import(filepath=source)
    elif suffix == ".glb":
        bpy.ops.import_scene.gltf(filepath=source)
    else:
        raise ValueError("Unsupported mesh format")
    objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not objects:
        raise ValueError("Empty geometry")
    total = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in objects)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 8
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 2
    scene.render.bake.use_clear = True
    for obj in objects:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        obj.scale = [v * scale for v in obj.scale]
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        if total > target:
            decimate = obj.modifiers.new("forge_budget", "DECIMATE")
            decimate.ratio = target / total
            bpy.ops.object.modifier_apply(modifier=decimate.name)
        if not obj.data.uv_layers:
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.smart_project(island_margin=0.02)
            bpy.ops.object.mode_set(mode="OBJECT")
        if not obj.data.materials:
            material = bpy.data.materials.new("Neutral surface")
            material.use_nodes = True
            obj.data.materials.append(material)
        # Keep source PBR materials. Bake detail maps headlessly; AO is exported
        # through the glTF exporter-recognized occlusion group.
        for material in obj.data.materials:
            if material is None:
                continue
            material.use_nodes = True
        for kind in ("NORMAL", "AO"):
            image = bpy.data.images.new(obj.name + "_" + kind, width=256, height=256)
            image.colorspace_settings.name = "Non-Color"
            for material in obj.data.materials:
                if material is None:
                    continue
                node = material.node_tree.nodes.new("ShaderNodeTexImage")
                node.image = image
                material.node_tree.nodes.active = node
            bpy.ops.object.bake(type=kind)
            image.pack()
            for material in obj.data.materials:
                if material is None:
                    continue
                nodes, links = material.node_tree.nodes, material.node_tree.links
                texture = next(n for n in nodes if n.type == "TEX_IMAGE" and n.image == image)
                shader = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
                if kind == "NORMAL" and shader and not shader.inputs["Normal"].is_linked:
                    normal = nodes.new("ShaderNodeNormalMap")
                    links.new(texture.outputs["Color"], normal.inputs["Color"])
                    links.new(normal.outputs["Normal"], shader.inputs["Normal"])
                if kind == "AO":
                    group = bpy.data.node_groups.get("glTF Material Output")
                    if group is None:
                        group = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
                        group.interface.new_socket(name="Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
                    output = nodes.new("ShaderNodeGroup")
                    output.node_tree = group
                    links.new(texture.outputs["Color"], output.inputs["Occlusion"])
    bpy.ops.export_scene.gltf(filepath=destination, export_format="GLB", export_animations=False)
    print(json.dumps({"exported": True, "draft": True}))


if __name__ == "__main__":
    main()
