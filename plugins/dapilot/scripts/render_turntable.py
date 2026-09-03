"""Headless turntable render of one model file.

Run:
  blender --background --python render_turntable.py -- model.stl out/ [frames] [res] [samples]

frames == 1 renders a single three-quarter "hero" still to out/hero.png.
frames  > 1 renders out/frame_000.png ... rotating the model a full turn.

Engine is Cycles on CPU deliberately. EEVEE and Workbench both need a GPU/GL
context, so they fail in a headless container with no display - which is where
this actually has to run. Cycles is slower but it is the only one that works
there, and OpenImageDenoise lets the sample count come down far enough to pay
for itself.

NOTE: reconstructed on 2026-09-03 after the original was lost. Framing and
lighting follow the notes in CLAUDE.md (scale before centering, Cycles only,
OIDN denoise); the exact original camera and light values are unknown.
"""
import math
import sys
import time

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
stl_path = argv[0]
out_dir = argv[1].rstrip("/")
frames = int(argv[2]) if len(argv) > 2 else 36
res = int(argv[3]) if len(argv) > 3 else 512
samples = int(argv[4]) if len(argv) > 4 else 48

t0 = time.time()
bpy.ops.wm.read_factory_settings(use_empty=True)

# Blender 4.x renamed the STL importer and shipped a much faster one; 3.x has
# only the old name. Try both so the same script runs on either.
try:
    bpy.ops.wm.stl_import(filepath=stl_path)
except AttributeError:
    bpy.ops.import_mesh.stl(filepath=stl_path)

objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not objs:
    raise SystemExit(f"no mesh imported from {stl_path}")
obj = objs[0]
t_import = time.time() - t0

# --- framing -----------------------------------------------------------------
# Scale BEFORE centering. Scaling happens about the object's origin, which an
# STL puts wherever the exporter felt like; centering first and scaling second
# shoves the model back off-centre by a model-dependent amount.
TARGET = 2.0  # longest side in Blender units after scaling
longest = max(obj.dimensions)
if longest == 0:
    raise SystemExit("degenerate mesh: zero extent")
s = TARGET / longest
obj.scale = (s, s, s)
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
center = sum(corners, Vector()) / 8.0
obj.location -= center
lowest = min((obj.matrix_world @ Vector(c)).z for c in obj.bound_box)
obj.location.z -= lowest  # stand it on the ground plane

# Turntable pivot: rotate an empty at the origin, not the object itself, so
# the model's own transform stays untouched.
pivot = bpy.data.objects.new("pivot", None)
bpy.context.scene.collection.objects.link(pivot)
obj.parent = pivot

# Neutral material so surface detail reads without hinting at a colour.
mat = bpy.data.materials.new("neutral")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.get("Principled BSDF")
if bsdf:
    bsdf.inputs["Base Color"].default_value = (0.8, 0.8, 0.8, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.5
obj.data.materials.clear()
obj.data.materials.append(mat)

# --- camera and light ---------------------------------------------------------
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 50
cam = bpy.data.objects.new("cam", cam_data)
bpy.context.scene.collection.objects.link(cam)
bpy.context.scene.camera = cam
cam.location = Vector((3.6, -3.6, 2.4))
look_at = Vector((0.0, 0.0, TARGET * 0.35))
cam.rotation_euler = (look_at - cam.location).to_track_quat("-Z", "Y").to_euler()

sun_data = bpy.data.lights.new("sun", type="SUN")
sun_data.energy = 3.0
sun = bpy.data.objects.new("sun", sun_data)
bpy.context.scene.collection.objects.link(sun)
sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(35))

world = bpy.data.worlds.new("world")
bpy.context.scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes.get("Background")
if bg:
    bg.inputs[0].default_value = (0.9, 0.9, 0.9, 1.0)
    bg.inputs[1].default_value = 1.0

# --- render settings ----------------------------------------------------------
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = samples
scene.cycles.use_denoising = True
try:
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
except TypeError:
    pass
scene.render.resolution_x = res
scene.render.resolution_y = res
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = False

# --- render -------------------------------------------------------------------
t_render = time.time()
if frames <= 1:
    pivot.rotation_euler = (0, 0, math.radians(-30))
    scene.render.filepath = f"{out_dir}/hero.png"
    bpy.ops.render.render(write_still=True)
else:
    for i in range(frames):
        pivot.rotation_euler = (0, 0, 2 * math.pi * i / frames)
        scene.render.filepath = f"{out_dir}/frame_{i:03d}.png"
        bpy.ops.render.render(write_still=True)

t_total = time.time() - t0
print(f"render_turntable: import {t_import:.1f}s, render {time.time() - t_render:.1f}s, "
      f"total {t_total:.1f}s, {frames} frame(s) at {res}px/{samples} samples -> {out_dir}")
