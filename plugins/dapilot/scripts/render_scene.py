"""Studio product render of one mesh. Runs inside Blender; use render_model.py to drive it.

  blender --background --python render_scene.py -- --input mesh.stl --out DIR --mode draft|final [options]

draft:  a low-res turntable, --frames N (default 8), for a model to pick an angle from
final:  --angles "35,125,215,305" at full quality; the first angle is hero.png

Why it looks like a photo and not a viewport: environment light from Blender's
bundled studio.exr, a soft key and a rim, a curved backdrop (cyclorama) that
takes a soft shadow, an 85 mm lens with shallow depth of field, a filament-like
material with optional layer lines, and the AgX view transform. All of that is
scene setup, so it lives here and is identical for every product.

Cycles on CPU with OpenImageDenoise. EEVEE needs a GPU context and fails headless.
"""
import argparse
import json
import math
import os
import sys
import time

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ap = argparse.ArgumentParser()
ap.add_argument("--input", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--mode", choices=["draft", "final"], default="draft")
ap.add_argument("--frames", type=int, default=8)
ap.add_argument("--angles", default="35,125,215,305")
ap.add_argument("--elevation", type=float, default=18.0, help="camera elevation in degrees")
ap.add_argument("--res", type=int)
ap.add_argument("--samples", type=int)
ap.add_argument("--color", default="#E8E4DC", help="filament colour, hex")
ap.add_argument("--rotate", default="0,0,0", help="model rotation x,y,z in degrees before framing")
ap.add_argument("--layer-lines", action="store_true")
ap.add_argument("--layer-mm", type=float, default=0.2)
ap.add_argument("--hdri", default="studio.exr")
ap.add_argument("--backdrop", default="#F2F0EB", help="backdrop colour, hex")
a = ap.parse_args(argv)
res = a.res or (320 if a.mode == "draft" else 1024)
samples = a.samples or (24 if a.mode == "draft" else 128)
os.makedirs(a.out, exist_ok=True)


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def srgb_to_linear(c):
    return tuple((x / 12.92) if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


t0 = time.time()
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

# --- import ------------------------------------------------------------------
ext = os.path.splitext(a.input)[1].lower()
if ext == ".stl":
    try:
        bpy.ops.wm.stl_import(filepath=a.input)
    except AttributeError:
        bpy.ops.import_mesh.stl(filepath=a.input)
elif ext == ".obj":
    bpy.ops.wm.obj_import(filepath=a.input)
elif ext == ".ply":
    bpy.ops.wm.ply_import(filepath=a.input)
else:
    raise SystemExit(f"render_scene: cannot import {ext}; convert 3mf to ply first")
meshes = [o for o in scene.objects if o.type == "MESH"]
if not meshes:
    raise SystemExit(f"no mesh imported from {a.input}")
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
obj = bpy.context.view_layer.objects.active
obj.name = "product"
t_import = time.time() - t0

# --- framing: rotate, scale about origin, then centre and stand on the floor ---
rx, ry, rz = (math.radians(float(v)) for v in a.rotate.split(","))
obj.rotation_euler = (rx, ry, rz)
bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
dims_mm = tuple(obj.dimensions)
longest = max(dims_mm)
if longest == 0:
    raise SystemExit("degenerate mesh: zero extent")
TARGET = 2.0
s = TARGET / longest
obj.scale = (s, s, s)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
center = sum(corners, Vector()) / 8.0
obj.location -= center
bpy.context.view_layer.update()  # matrix_world is stale until the depsgraph runs
lowest = min((obj.matrix_world @ Vector(c)).z for c in obj.bound_box)
obj.location.z -= lowest
bpy.context.view_layer.update()
corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
center = sum(corners, Vector()) / 8.0
radius = max((c - center).length for c in corners)
bpy.ops.object.shade_smooth_by_angle(angle=math.radians(35)) if hasattr(bpy.ops.object, "shade_smooth_by_angle") else bpy.ops.object.shade_smooth()

pivot = bpy.data.objects.new("pivot", None)
scene.collection.objects.link(pivot)
obj.parent = pivot

# --- material: filament ------------------------------------------------------
mat = bpy.data.materials.new("filament")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (*srgb_to_linear(hex_rgb(a.color)), 1.0)
bsdf.inputs["Roughness"].default_value = 0.42
if "Specular IOR Level" in bsdf.inputs:
    bsdf.inputs["Specular IOR Level"].default_value = 0.45
if "Coat Weight" in bsdf.inputs:
    bsdf.inputs["Coat Weight"].default_value = 0.08
if a.layer_lines:
    coord = nt.nodes.new("ShaderNodeTexCoord")
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.wave_type = "BANDS"
    wave.bands_direction = "Z"
    wave.inputs["Scale"].default_value = 1.0 / (a.layer_mm * s)  # bands per Blender unit
    wave.inputs["Distortion"].default_value = 0.0
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    bump.inputs["Distance"].default_value = 0.02
    nt.links.new(coord.outputs["Object"], wave.inputs["Vector"])
    nt.links.new(wave.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
obj.data.materials.clear()
obj.data.materials.append(mat)

# --- backdrop: a cyclorama, floor sweeping up into a wall ----------------------
# Profile in the y-z plane: floor from y=-D (camera side) to y=+2, a quarter
# circle of radius R sweeping up, then a wall to height H. Camera sits at -y.
verts, faces = [], []
W, D, H, R, N = 40.0, 30.0, 40.0, 2.5, 16
profile = [(-D, 0.0)]
profile += [(2.0 + R * math.sin(t), R - R * math.cos(t)) for t in (math.pi / 2 * i / N for i in range(N + 1))]
profile.append((2.0 + R, H))
for (y, z) in profile:
    verts += [(-W, y, z), (W, y, z)]
for i in range(len(profile) - 1):
    faces.append((2 * i, 2 * i + 1, 2 * i + 3, 2 * i + 2))
bd_mesh = bpy.data.meshes.new("cyclorama")
bd_mesh.from_pydata(verts, [], faces)
bd_mesh.update()
backdrop = bpy.data.objects.new("cyclorama", bd_mesh)
scene.collection.objects.link(backdrop)
bd_mat = bpy.data.materials.new("backdrop")
bd_mat.use_nodes = True
bd_bsdf = bd_mat.node_tree.nodes["Principled BSDF"]
bd_bsdf.inputs["Base Color"].default_value = (*srgb_to_linear(hex_rgb(a.backdrop)), 1.0)
bd_bsdf.inputs["Roughness"].default_value = 0.7
backdrop.data.materials.append(bd_mat)
for p in backdrop.data.polygons:
    p.use_smooth = True

# --- world and lights -------------------------------------------------------
world = bpy.data.worlds.new("world")
scene.world = world
world.use_nodes = True
wnt = world.node_tree
bg = wnt.nodes["Background"]
hdri_path = None
for root_dir in (bpy.utils.resource_path("LOCAL"), bpy.utils.resource_path("SYSTEM")):
    cand = os.path.join(root_dir, "datafiles", "studiolights", "world", a.hdri)
    if os.path.exists(cand):
        hdri_path = cand
        break
if hdri_path:
    env = wnt.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(hdri_path)
    mapping = wnt.nodes.new("ShaderNodeMapping")
    texco = wnt.nodes.new("ShaderNodeTexCoord")
    mapping.inputs["Rotation"].default_value = (0, 0, math.radians(200))
    wnt.links.new(texco.outputs["Generated"], mapping.inputs["Vector"])
    wnt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
    wnt.links.new(env.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 0.4
else:
    bg.inputs[0].default_value = (0.8, 0.8, 0.8, 1.0)
    bg.inputs[1].default_value = 0.8
# the environment lights the backdrop; the camera sees the backdrop, not the HDRI
scene.world.cycles_visibility.camera = False if hasattr(scene.world, "cycles_visibility") else None


def area_light(name, loc, target, energy, size, color=(1, 1, 1)):
    ld = bpy.data.lights.new(name, type="AREA")
    ld.energy = energy
    ld.size = size
    ld.color = color
    lo = bpy.data.objects.new(name, ld)
    scene.collection.objects.link(lo)
    lo.location = Vector(loc)
    lo.rotation_euler = (Vector(target) - lo.location).to_track_quat("-Z", "Y").to_euler()
    return lo


focus = Vector((0, 0, center.z))
area_light("key", (-3.5, -4.0, 5.0), focus, 1300, 2.5, (1.0, 0.97, 0.93))
area_light("fill", (4.5, -3.5, 2.5), focus, 220, 4.0, (0.93, 0.96, 1.0))
area_light("rim", (1.5, 4.5, 4.5), focus, 800, 1.2)

# --- camera: 85 mm, shallow depth of field ------------------------------------
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 85
cam_data.sensor_width = 36
cam_data.dof.use_dof = True
cam_data.dof.aperture_fstop = 4.0
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
fov = 2 * math.atan(cam_data.sensor_width / (2 * cam_data.lens))
distance = radius / math.sin(fov / 2) * 1.25
elev = math.radians(a.elevation)


from bpy_extras.object_utils import world_to_camera_view  # noqa: E402

MARGIN = 0.10  # fraction of the frame left empty on the tightest side


def place_camera(yaw_deg):
    """Turntable: rotate the product on its pivot; camera, lights and backdrop stay put.

    Every angle is lit the same way and the backdrop is always behind the
    product. Orbiting the camera instead put it behind the cyclorama wall at
    yaw 180 and rendered a grey wall.
    """
    pivot.rotation_euler = (0.0, 0.0, -math.radians(yaw_deg))
    bpy.context.view_layer.update()
    box = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    direction = Vector((0.0, -math.cos(elev), math.sin(elev)))
    d = distance
    for _ in range(6):  # converge on the distance where the projected box fills the frame
        cam.location = focus + direction * d
        cam.rotation_euler = (focus - cam.location).to_track_quat("-Z", "Y").to_euler()
        bpy.context.view_layer.update()
        pts = [world_to_camera_view(scene, cam, c) for c in box]
        spread = max(max(abs(p.x - 0.5), abs(p.y - 0.5)) for p in pts) * 2  # 1.0 == exactly filling
        target = 1.0 - 2 * MARGIN
        if abs(spread - target) < 0.01:
            break
        d *= spread / target
    cam_data.dof.focus_distance = (focus - cam.location).length


# --- render settings ------------------------------------------------------------
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = samples
scene.cycles.use_adaptive_sampling = True
scene.cycles.use_denoising = True
try:
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
except TypeError:
    pass
scene.render.resolution_x = res
scene.render.resolution_y = res
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
scene.render.film_transparent = False
try:
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Punchy"
except TypeError:
    pass

# --- render -------------------------------------------------------------------
outputs = []
t_render = time.time()
if a.mode == "draft":
    for i in range(a.frames):
        yaw = 360.0 * i / a.frames
        place_camera(yaw)
        path = os.path.join(a.out, f"draft_{i:02d}.png")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        outputs.append({"file": os.path.basename(path), "yaw": yaw})
else:
    for i, yaw_s in enumerate(a.angles.split(",")):
        yaw = float(yaw_s)
        place_camera(yaw)
        name = "hero.png" if i == 0 else f"angle_{int(round(yaw)) % 360:03d}.png"
        path = os.path.join(a.out, name)
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        outputs.append({"file": name, "yaw": yaw})

print("RENDER_RESULT " + json.dumps({
    "ok": True, "mode": a.mode, "res": res, "samples": samples, "outputs": outputs,
    "dims_mm": [round(d, 2) for d in dims_mm], "color": a.color, "rotate": a.rotate, "elevation": a.elevation,
    "layer_lines": a.layer_lines, "hdri": os.path.basename(hdri_path) if hdri_path else None,
    "import_s": round(t_import, 1), "render_s": round(time.time() - t_render, 1), "total_s": round(time.time() - t0, 1),
}))
