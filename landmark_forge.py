# -*- coding: utf-8 -*-
"""
LANDMARK FORGE
==============
Blender 4.x / 5.x procedural architecture generator.

One-file script. Open in Blender → Scripting workspace → Run Script.
Or from a terminal:

    blender --background --python landmark_forge.py

What it builds (four independent collections, optionally laid out as a museum):

  1. Hogwarts Castle   — fan-made architectural interpretation
                         (not affiliated with Warner Bros. or J.K. Rowling)
  2. ETH Zürich        — Hauptgebäude after Semper / Gull (dome, Polyterrasse)
  3. MI6 / SIS         — Vauxhall Cross, Terry Farrell 1994 (ziggurat)
  4. Sydney Opera House — Utzon spherical shells on Bennelong Point

Blender 4.0 – 5.x  |  Python 3.10+  |  Cycles or EEVEE  |  v1.3.0

N-Panel:  3D Viewport → Sidebar (N) → Landmark Forge
"""

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence, Tuple, Union
import math
import random
import sys

try:
    import bpy
    import bmesh
    from mathutils import Euler, Matrix, Vector, noise
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "This file is a Blender script. Open it in Blender's Scripting "
        "workspace and press Run Script.\n"
        "Original import error: %s" % exc
    )


# =============================================================================
# 0. VERSION / CONFIG
# =============================================================================

SCRIPT_VERSION = "1.3.0"
BLENDER_MIN = (4, 0, 0)

Vec3 = Tuple[float, float, float]
Vec2 = Tuple[float, float]
Color = Tuple[float, float, float, float]


@dataclass
class ForgeConfig:
    """All generation knobs. Tweak here or from the N-panel."""

    seed: int = 42
    build_hogwarts: bool = True
    build_eth: bool = True
    build_mi6: bool = True
    build_sydney: bool = True
    museum_layout: bool = True
    clear_scene: bool = True
    add_world: bool = True
    add_cameras: bool = True
    add_lights: bool = True
    add_ground: bool = True
    window_density: str = "high"  # low | medium | high
    night: bool = True
    export_glb: bool = False
    export_dir: str = "//exports/"
    spacing: float = 180.0
    unit_scale: float = 1.0  # 1 blender unit ≈ 1 metre

    hogwarts_origin: Vec3 = (0.0, 0.0, 0.0)
    eth_origin: Vec3 = (180.0, 0.0, 0.0)
    mi6_origin: Vec3 = (360.0, 0.0, 0.0)
    sydney_origin: Vec3 = (540.0, 0.0, 0.0)


CFG = ForgeConfig()
RNG = random.Random(CFG.seed)


# =============================================================================
# 1. SCENE UTILITIES
# =============================================================================

def blender_version_ok() -> bool:
    v = bpy.app.version
    return v >= BLENDER_MIN


def _unlink_and_remove_object(obj: bpy.types.Object) -> None:
    bpy.data.objects.remove(obj, do_unlink=True)


def clear_generated() -> None:
    """Remove previous Landmark Forge objects, keep the user's other work if possible."""
    doomed = [
        obj
        for obj in list(bpy.data.objects)
        if obj.name.startswith("LF_") or obj.get("landmark_forge")
    ]
    for obj in doomed:
        _unlink_and_remove_object(obj)

    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for mat in list(bpy.data.materials):
        if mat.name.startswith("LF_") and mat.users == 0:
            bpy.data.materials.remove(mat)
    for img in list(bpy.data.images):
        if img.name.startswith("LF_") and img.users == 0:
            bpy.data.images.remove(img)
    for col in list(bpy.data.collections):
        if col.name.startswith("LF_"):
            bpy.data.collections.remove(col)


def ensure_collection(name: str, parent: Optional[bpy.types.Collection] = None) -> bpy.types.Collection:
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        if parent is None:
            bpy.context.scene.collection.children.link(col)
        else:
            parent.children.link(col)
    return col


def link_object(obj: bpy.types.Object, collection: Optional[bpy.types.Collection] = None) -> bpy.types.Object:
    col = collection or bpy.context.scene.collection
    if obj.name not in col.objects:
        col.objects.link(obj)
    # Avoid double-link in the scene master collection when we already linked
    master = bpy.context.scene.collection
    if col is not master and obj.name in master.objects:
        try:
            master.objects.unlink(obj)
        except RuntimeError:
            pass
    obj["landmark_forge"] = True
    return obj


def new_empty(name: str, location: Vec3, collection: Optional[bpy.types.Collection] = None) -> bpy.types.Object:
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type = "PLAIN_AXES"
    empty.empty_display_size = 2.0
    empty.location = location
    empty["landmark_forge"] = True
    link_object(empty, collection)
    return empty


def tag_parent(obj: bpy.types.Object, parent: Optional[bpy.types.Object]) -> bpy.types.Object:
    if parent is not None:
        obj.parent = parent
        obj.matrix_parent_inverse = parent.matrix_world.inverted()
    return obj


def shade_smooth(obj: bpy.types.Object, angle_deg: float = 30.0) -> bpy.types.Object:
    mesh = obj.data
    if not isinstance(mesh, bpy.types.Mesh):
        return obj
    try:
        mesh.use_auto_smooth = True
        mesh.auto_smooth_angle = math.radians(angle_deg)
    except Exception:
        pass
    for poly in mesh.polygons:
        poly.use_smooth = True
    return obj


def shade_flat(obj: bpy.types.Object) -> bpy.types.Object:
    mesh = obj.data
    if not isinstance(mesh, bpy.types.Mesh):
        return obj
    for poly in mesh.polygons:
        poly.use_smooth = False
    return obj


def apply_material(obj: bpy.types.Object, mat: Optional[bpy.types.Material]) -> bpy.types.Object:
    if mat is None:
        return obj
    mesh = obj.data
    if hasattr(mesh, "materials"):
        if mesh.materials:
            mesh.materials[0] = mat
        else:
            mesh.materials.append(mat)
    return obj


def set_origin_to_geometry(obj: bpy.types.Object) -> None:
    # Keep origin as authored; helper exists for completeness.
    return


# =============================================================================
# 2. LOW-LEVEL MESH BUILDERS
# =============================================================================

def mesh_from_pydata(
    name: str,
    verts: Sequence[Vec3],
    faces: Sequence[Sequence[int]],
    collection: Optional[bpy.types.Collection] = None,
    parent: Optional[bpy.types.Object] = None,
    mat: Optional[bpy.types.Material] = None,
    location: Vec3 = (0.0, 0.0, 0.0),
    smooth: bool = False,
) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
    mesh.update()
    mesh.validate()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj["landmark_forge"] = True
    link_object(obj, collection)
    tag_parent(obj, parent)
    apply_material(obj, mat)
    if smooth:
        shade_smooth(obj)
    else:
        shade_flat(obj)
    return obj


def make_box(
    name: str,
    size: Union[float, Vec3],
    location: Vec3 = (0.0, 0.0, 0.0),
    collection: Optional[bpy.types.Collection] = None,
    parent: Optional[bpy.types.Object] = None,
    mat: Optional[bpy.types.Material] = None,
    rotation: Vec3 = (0.0, 0.0, 0.0),
) -> bpy.types.Object:
    if isinstance(size, (int, float)):
        sx = sy = sz = float(size)
    else:
        sx, sy, sz = size
    hx, hy, hz = sx * 0.5, sy * 0.5, sz * 0.5
    verts = [
        (-hx, -hy, -hz),
        (hx, -hy, -hz),
        (hx, hy, -hz),
        (-hx, hy, -hz),
        (-hx, -hy, hz),
        (hx, -hy, hz),
        (hx, hy, hz),
        (-hx, hy, hz),
    ]
    faces = [
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    ]
    obj = mesh_from_pydata(name, verts, faces, collection, parent, mat, location)
    if rotation != (0.0, 0.0, 0.0):
        obj.rotation_euler = Euler(rotation)
    return obj


def make_cylinder(
    name: str,
    radius: float,
    depth: float,
    location: Vec3 = (0.0, 0.0, 0.0),
    vertices: int = 24,
    collection: Optional[bpy.types.Collection] = None,
    parent: Optional[bpy.types.Object] = None,
    mat: Optional[bpy.types.Material] = None,
    rotation: Vec3 = (0.0, 0.0, 0.0),
    cap: bool = True,
) -> bpy.types.Object:
    verts: list[Vec3] = []
    faces: list[tuple] = []
    n = max(3, vertices)
    hz = depth * 0.5
    for ring_z in (-hz, hz):
        for i in range(n):
            a = (i / n) * math.tau
            verts.append((math.cos(a) * radius, math.sin(a) * radius, ring_z))
    for i in range(n):
        a = i
        b = (i + 1) % n
        faces.append((a, b, b + n, a + n))
    if cap:
        faces.append(tuple(range(n - 1, -1, -1)))
        faces.append(tuple(range(n, 2 * n)))
    obj = mesh_from_pydata(name, verts, faces, collection, parent, mat, location, smooth=True)
    if rotation != (0.0, 0.0, 0.0):
        obj.rotation_euler = Euler(rotation)
    return obj


def make_cone(
    name: str,
    radius1: float,
    depth: float,
    location: Vec3 = (0.0, 0.0, 0.0),
    vertices: int = 16,
    radius2: float = 0.0,
    collection: Optional[bpy.types.Collection] = None,
    parent: Optional[bpy.types.Object] = None,
    mat: Optional[bpy.types.Material] = None,
    rotation: Vec3 = (0.0, 0.0, 0.0),
) -> bpy.types.Object:
    n = max(3, vertices)
    hz = depth * 0.5
    verts: list[Vec3] = []
    for i in range(n):
        a = (i / n) * math.tau
        verts.append((math.cos(a) * radius1, math.sin(a) * radius1, -hz))
    for i in range(n):
        a = (i / n) * math.tau
        verts.append((math.cos(a) * radius2, math.sin(a) * radius2, hz))
    faces: list[tuple] = []
    for i in range(n):
        a = i
        b = (i + 1) % n
        faces.append((a, b, b + n, a + n))
    faces.append(tuple(range(n - 1, -1, -1)))
    if radius2 > 1e-4:
        faces.append(tuple(range(n, 2 * n)))
    obj = mesh_from_pydata(name, verts, faces, collection, parent, mat, location, smooth=True)
    if rotation != (0.0, 0.0, 0.0):
        obj.rotation_euler = Euler(rotation)
    return obj


def make_uv_sphere(
    name: str,
    radius: float,
    location: Vec3 = (0.0, 0.0, 0.0),
    segments: int = 24,
    rings: int = 12,
    collection: Optional[bpy.types.Collection] = None,
    parent: Optional[bpy.types.Object] = None,
    mat: Optional[bpy.types.Material] = None,
    hemisphere: bool = False,
) -> bpy.types.Object:
    verts: list[Vec3] = []
    faces: list[tuple] = []
    r_count = rings if not hemisphere else max(2, rings // 2)
    lat_max = math.pi * 0.5 if hemisphere else math.pi
    for j in range(r_count + 1):
        v = j / r_count
        phi = v * lat_max
        for i in range(segments):
            u = i / segments
            th = u * math.tau
            x = radius * math.sin(phi) * math.cos(th)
            y = radius * math.sin(phi) * math.sin(th)
            z = radius * math.cos(phi)
            if hemisphere:
                z = radius * math.sin(phi)  # dome sitting on XY, +Z up
                x = radius * math.cos(phi) * math.cos(th)
                y = radius * math.cos(phi) * math.sin(th)
            verts.append((x, y, z))
    for j in range(r_count):
        for i in range(segments):
            a = j * segments + i
            b = j * segments + (i + 1) % segments
            c = (j + 1) * segments + (i + 1) % segments
            d = (j + 1) * segments + i
            faces.append((a, b, c, d))
    return mesh_from_pydata(name, verts, faces, collection, parent, mat, location, smooth=True)


def make_plane(
    name: str,
    size: Union[float, Vec2],
    location: Vec3 = (0.0, 0.0, 0.0),
    collection: Optional[bpy.types.Collection] = None,
    parent: Optional[bpy.types.Object] = None,
    mat: Optional[bpy.types.Material] = None,
    rotation: Vec3 = (0.0, 0.0, 0.0),
) -> bpy.types.Object:
    if isinstance(size, (int, float)):
        sx = sy = float(size)
    else:
        sx, sy = size
    hx, hy = sx * 0.5, sy * 0.5
    verts = [(-hx, -hy, 0.0), (hx, -hy, 0.0), (hx, hy, 0.0), (-hx, hy, 0.0)]
    faces = [(0, 1, 2, 3)]
    obj = mesh_from_pydata(name, verts, faces, collection, parent, mat, location)
    if rotation != (0.0, 0.0, 0.0):
        obj.rotation_euler = Euler(rotation)
    return obj


def make_wedge(
    name: str,
    size: Vec3,
    location: Vec3 = (0.0, 0.0, 0.0),
    collection: Optional[bpy.types.Collection] = None,
    parent: Optional[bpy.types.Object] = None,
    mat: Optional[bpy.types.Material] = None,
) -> bpy.types.Object:
    """Right-angle roof wedge: triangular prism along Y."""
    sx, sy, sz = size
    hx, hy = sx * 0.5, sy * 0.5
    verts = [
        (-hx, -hy, 0.0),
        (hx, -hy, 0.0),
        (hx, hy, 0.0),
        (-hx, hy, 0.0),
        (0.0, -hy, sz),
        (0.0, hy, sz),
    ]
    faces = [
        (0, 1, 4),
        (3, 5, 2),
        (0, 4, 5, 3),
        (1, 2, 5, 4),
        (0, 3, 2, 1),
    ]
    return mesh_from_pydata(name, verts, faces, collection, parent, mat, location)


def make_gabled_roof(
    name: str,
    length: float,
    width: float,
    height: float,
    location: Vec3,
    collection: Optional[bpy.types.Collection],
    parent: Optional[bpy.types.Object],
    mat: Optional[bpy.types.Material],
    axis: str = "X",
) -> bpy.types.Object:
    """
    Steep gabled roof. axis='X' means the ridge runs along X (length).
    The box of the roof sits with its BOTTOM on location.z.
    """
    hl, hw = length * 0.5, width * 0.5
    if axis == "X":
        verts = [
            (-hl, -hw, 0.0),
            (hl, -hw, 0.0),
            (hl, hw, 0.0),
            (-hl, hw, 0.0),
            (-hl, 0.0, height),
            (hl, 0.0, height),
        ]
        faces = [
            (0, 1, 5, 4),
            (3, 4, 5, 2),
            (0, 4, 3),
            (1, 2, 5),
            (0, 3, 2, 1),
        ]
    else:
        verts = [
            (-hw, -hl, 0.0),
            (hw, -hl, 0.0),
            (hw, hl, 0.0),
            (-hw, hl, 0.0),
            (0.0, -hl, height),
            (0.0, hl, height),
        ]
        faces = [
            (0, 1, 5, 4),
            (3, 4, 5, 2),
            (0, 4, 3),
            (1, 2, 5),
            (0, 3, 2, 1),
        ]
    return mesh_from_pydata(name, verts, faces, collection, parent, mat, location)


def lathe_profile(
    name: str,
    profile: Sequence[Vec2],
    segments: int = 24,
    location: Vec3 = (0.0, 0.0, 0.0),
    collection: Optional[bpy.types.Collection] = None,
    parent: Optional[bpy.types.Object] = None,
    mat: Optional[bpy.types.Material] = None,
) -> bpy.types.Object:
    """Revolve an XZ profile (x = radius, z = height) around Z."""
    verts: list[Vec3] = []
    n = segments
    for r, z in profile:
        for i in range(n):
            a = (i / n) * math.tau
            verts.append((math.cos(a) * r, math.sin(a) * r, z))
    faces: list[tuple] = []
    rows = len(profile)
    for j in range(rows - 1):
        for i in range(n):
            a = j * n + i
            b = j * n + (i + 1) % n
            c = (j + 1) * n + (i + 1) % n
            d = (j + 1) * n + i
            faces.append((a, b, c, d))
    return mesh_from_pydata(name, verts, faces, collection, parent, mat, location, smooth=True)


def make_torus(
    name: str,
    major: float,
    minor: float,
    location: Vec3 = (0.0, 0.0, 0.0),
    major_seg: int = 24,
    minor_seg: int = 8,
    collection: Optional[bpy.types.Collection] = None,
    parent: Optional[bpy.types.Object] = None,
    mat: Optional[bpy.types.Material] = None,
) -> bpy.types.Object:
    verts: list[Vec3] = []
    for i in range(major_seg):
        u = (i / major_seg) * math.tau
        cx = math.cos(u) * major
        cy = math.sin(u) * major
        for j in range(minor_seg):
            v = (j / minor_seg) * math.tau
            r = major + math.cos(v) * minor
            x = math.cos(u) * r
            y = math.sin(u) * r
            z = math.sin(v) * minor
            verts.append((x, y, z))
    faces: list[tuple] = []
    for i in range(major_seg):
        for j in range(minor_seg):
            a = i * minor_seg + j
            b = i * minor_seg + (j + 1) % minor_seg
            c = ((i + 1) % major_seg) * minor_seg + (j + 1) % minor_seg
            d = ((i + 1) % major_seg) * minor_seg + j
            faces.append((a, b, c, d))
    return mesh_from_pydata(name, verts, faces, collection, parent, mat, location, smooth=True)


def make_arch_solid(
    name: str,
    width: float,
    height: float,
    depth: float,
    thickness: float,
    location: Vec3,
    collection: Optional[bpy.types.Collection],
    parent: Optional[bpy.types.Object],
    mat: Optional[bpy.types.Material],
    segments: int = 10,
) -> bpy.types.Object:
    """Simple Roman arch as a solid block with a semicircular opening (outer mass)."""
    # Outer box minus inner extruded semicircle approximated as a thick U.
    hw = width * 0.5
    hd = depth * 0.5
    t = thickness
    verts: list[Vec3] = []
    # Build as two piers + a vault ring.
    # Piers
    # We'll construct a 2D outline and extrude.
    outline: list[Vec2] = []
    outline.append((-hw, 0.0))
    outline.append((-hw, height - hw))
    for i in range(segments + 1):
        a = math.pi + (i / segments) * math.pi
        outline.append((math.cos(a) * hw, (height - hw) + math.sin(a) * hw * 0.0 + abs(math.sin((i / segments) * math.pi)) * hw))
    # Fix the semicircle properly
    outline = [(-hw, 0.0), (-hw, height - hw)]
    for i in range(segments + 1):
        a = math.pi - (i / segments) * math.pi
        outline.append((math.cos(a) * hw, (height - hw) + math.sin(a) * hw))
    outline.append((hw, 0.0))
    inner: list[Vec2] = []
    inner_w = hw - t
    inner_h = height - t
    inner.append((-inner_w, 0.0))
    inner.append((-inner_w, inner_h - inner_w))
    for i in range(segments + 1):
        a = math.pi - (i / segments) * math.pi
        inner.append((math.cos(a) * inner_w, (inner_h - inner_w) + math.sin(a) * inner_w))
    inner.append((inner_w, 0.0))

    # Solid arch without hole (piers + vault) — good enough for distant architecture.
    n = len(outline)
    for x, z in outline:
        verts.append((x, -hd, z))
    for x, z in outline:
        verts.append((x, hd, z))
    faces: list[tuple] = []
    for i in range(n - 1):
        faces.append((i, i + 1, i + 1 + n, i + n))
    faces.append(tuple(range(n)))
    faces.append(tuple(range(2 * n - 1, n - 1, -1)))
    return mesh_from_pydata(name, verts, faces, collection, parent, mat, location)


def make_icosphere_chunk(
    name: str,
    radius: float,
    location: Vec3,
    collection: Optional[bpy.types.Collection],
    parent: Optional[bpy.types.Object],
    mat: Optional[bpy.types.Material],
    subdivisions: int = 1,
) -> bpy.types.Object:
    # Golden-ratio icosahedron, optionally subdivided once.
    t = (1.0 + math.sqrt(5.0)) / 2.0
    raw = [
        (-1, t, 0),
        (1, t, 0),
        (-1, -t, 0),
        (1, -t, 0),
        (0, -1, t),
        (0, 1, t),
        (0, -1, -t),
        (0, 1, -t),
        (t, 0, -1),
        (t, 0, 1),
        (-t, 0, -1),
        (-t, 0, 1),
    ]
    verts = []
    for x, y, z in raw:
        l = math.sqrt(x * x + y * y + z * z)
        verts.append((x / l * radius, y / l * radius, z / l * radius))
    faces = [
        (0, 11, 5),
        (0, 5, 1),
        (0, 1, 7),
        (0, 7, 10),
        (0, 10, 11),
        (1, 5, 9),
        (5, 11, 4),
        (11, 10, 2),
        (10, 7, 6),
        (7, 1, 8),
        (3, 9, 4),
        (3, 4, 2),
        (3, 2, 6),
        (3, 6, 8),
        (3, 8, 9),
        (4, 9, 5),
        (2, 4, 11),
        (6, 2, 10),
        (8, 6, 7),
        (9, 8, 1),
    ]
    return mesh_from_pydata(name, verts, faces, collection, parent, mat, location, smooth=True)


# =============================================================================
# 3. MATERIAL LIBRARY
# =============================================================================

def _set_rna(id_data, name: str, value) -> None:
    """Assign an RNA property only if this Blender version still has it."""
    if hasattr(id_data, name):
        try:
            setattr(id_data, name, value)
        except Exception:
            pass


def _hash_color(key: str) -> None:
    return None


def principled_material(
    name: str,
    color: Vec3,
    roughness: float = 0.6,
    metallic: float = 0.0,
    specular: float = 0.5,
    transmission: float = 0.0,
    ior: float = 1.45,
    emission: Optional[Vec3] = None,
    emission_strength: float = 0.0,
    alpha: float = 1.0,
    subsurface: float = 0.0,
) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (400, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    # Blender 4 renamed a few sockets — write both when present.
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = specular
    elif "Specular" in bsdf.inputs:
        bsdf.inputs["Specular"].default_value = specular
    if "Transmission Weight" in bsdf.inputs:
        bsdf.inputs["Transmission Weight"].default_value = transmission
    elif "Transmission" in bsdf.inputs:
        bsdf.inputs["Transmission"].default_value = transmission
    if "IOR" in bsdf.inputs:
        bsdf.inputs["IOR"].default_value = ior
    if emission is not None:
        if "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = (*emission, 1.0)
        elif "Emission" in bsdf.inputs:
            bsdf.inputs["Emission"].default_value = (*emission, 1.0)
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = emission_strength
    if "Alpha" in bsdf.inputs:
        bsdf.inputs["Alpha"].default_value = alpha
    if subsurface > 0 and "Subsurface Weight" in bsdf.inputs:
        bsdf.inputs["Subsurface Weight"].default_value = subsurface
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    if alpha < 0.999:
        # Blend/shadow RNA names differ across 4.0, 4.2 (EEVEE Next) and 5.0.
        _set_rna(mat, "blend_method", "BLEND")
        _set_rna(mat, "shadow_method", "HASHED")
        _set_rna(mat, "surface_render_method", "BLENDED")
    return mat


def glass_material(name: str, color: Vec3, roughness: float = 0.05, ior: float = 1.45) -> bpy.types.Material:
    return principled_material(
        name,
        color=color,
        roughness=roughness,
        transmission=0.85,
        ior=ior,
        specular=0.8,
        metallic=0.0,
        alpha=0.55,
    )


def emission_material(name: str, color: Vec3, strength: float) -> bpy.types.Material:
    return principled_material(
        name,
        color=color,
        roughness=0.4,
        emission=color,
        emission_strength=strength,
    )


class Materials:
    """Named palette used by every landmark."""

    def __init__(self, night: bool = True) -> None:
        self.night = night
        glow = 4.2 if night else 0.15
        hall_glow = 6.5 if night else 0.3

        # Shared landscape
        self.soil = principled_material("LF_Soil", (0.18, 0.14, 0.10), roughness=0.95)
        self.grass = principled_material("LF_Grass", (0.12, 0.22, 0.09), roughness=0.92)
        self.dark_grass = principled_material("LF_DarkGrass", (0.07, 0.14, 0.06), roughness=0.95)
        self.rock = principled_material("LF_Rock", (0.22, 0.20, 0.18), roughness=0.88)
        self.cliff = principled_material("LF_Cliff", (0.36, 0.34, 0.31), roughness=0.94)
        self.water = principled_material(
            "LF_Water",
            (0.05, 0.12, 0.16),
            roughness=0.08,
            transmission=0.55,
            ior=1.33,
            specular=0.9,
            alpha=0.85,
        )
        self.water_deep = principled_material(
            "LF_WaterDeep",
            (0.02, 0.05, 0.08),
            roughness=0.12,
            metallic=0.15,
            specular=0.8,
        )
        self.sand = principled_material("LF_Sand", (0.55, 0.48, 0.36), roughness=0.9)
        self.cobble = principled_material("LF_Cobble", (0.32, 0.30, 0.28), roughness=0.85)
        self.plaza = principled_material("LF_Plaza", (0.42, 0.40, 0.37), roughness=0.7)
        self.bark = principled_material("LF_Bark", (0.16, 0.10, 0.06), roughness=0.95)
        self.leaf = principled_material("LF_Leaf", (0.10, 0.28, 0.08), roughness=0.8)
        self.pine = principled_material("LF_Pine", (0.06, 0.16, 0.07), roughness=0.85)
        self.gold = principled_material("LF_Gold", (0.72, 0.52, 0.18), roughness=0.28, metallic=0.85)
        self.iron = principled_material("LF_Iron", (0.12, 0.12, 0.13), roughness=0.45, metallic=0.7)
        self.lead = principled_material("LF_Lead", (0.18, 0.19, 0.20), roughness=0.4, metallic=0.6)
        self.wood = principled_material("LF_Wood", (0.28, 0.17, 0.08), roughness=0.75)
        self.wood_dark = principled_material("LF_WoodDark", (0.14, 0.08, 0.04), roughness=0.8)
        self.flag_red = principled_material("LF_FlagRed", (0.45, 0.05, 0.05), roughness=0.7)
        self.flag_gold = principled_material("LF_FlagGold", (0.62, 0.48, 0.12), roughness=0.6)
        self.night_glow = emission_material("LF_NightGlow", (1.0, 0.78, 0.42), glow)
        self.hall_glow = emission_material("LF_HallGlow", (1.0, 0.72, 0.32), hall_glow)
        self.cool_glow = emission_material("LF_CoolGlow", (0.55, 0.78, 1.0), 2.4 if night else 0.1)

        # Hogwarts
        self.hog_stone = principled_material("LF_HogStone", (0.48, 0.46, 0.42), roughness=0.86)
        self.hog_stone_dark = principled_material("LF_HogStoneDark", (0.30, 0.28, 0.25), roughness=0.9)
        self.hog_slate = principled_material("LF_HogSlate", (0.10, 0.12, 0.16), roughness=0.5)
        self.cliff = principled_material("LF_Cliff", (0.36, 0.34, 0.31), roughness=0.94)
        self.hog_slate_green = principled_material("LF_HogSlateGreen", (0.14, 0.18, 0.16), roughness=0.5)
        self.hog_mortar = principled_material("LF_HogMortar", (0.42, 0.40, 0.36), roughness=0.9)
        self.hog_glass = principled_material(
            "LF_HogGlass",
            (0.12, 0.16, 0.10) if not night else (0.55, 0.38, 0.12),
            roughness=0.15,
            transmission=0.3,
            emission=(1.0, 0.72, 0.32) if night else (0.2, 0.22, 0.18),
            emission_strength=glow if night else 0.0,
            alpha=0.9,
        )
        self.hog_trim = principled_material("LF_HogTrim", (0.45, 0.42, 0.36), roughness=0.7)

        # ETH Zürich
        self.eth_sandstone = principled_material("LF_ETHSandstone", (0.62, 0.56, 0.46), roughness=0.72)
        self.eth_sandstone_dark = principled_material("LF_ETHSandstoneDark", (0.48, 0.43, 0.35), roughness=0.78)
        self.eth_rustica = principled_material("LF_ETHRustica", (0.54, 0.49, 0.40), roughness=0.85)
        self.eth_roof = principled_material("LF_ETHRoof", (0.16, 0.16, 0.17), roughness=0.55)
        self.eth_dome = principled_material("LF_ETHDome", (0.14, 0.13, 0.13), roughness=0.48)
        self.eth_copper = principled_material("LF_ETHCopper", (0.22, 0.32, 0.24), roughness=0.4, metallic=0.55)
        self.eth_window = principled_material(
            "LF_ETHWindow",
            (0.22, 0.26, 0.28),
            roughness=0.12,
            metallic=0.1,
            emission=(0.95, 0.82, 0.55) if night else (0.25, 0.28, 0.3),
            emission_strength=(1.8 if night else 0.0),
        )
        self.eth_plaza = principled_material("LF_ETHPlaza", (0.72, 0.70, 0.66), roughness=0.55)
        self.eth_column = principled_material("LF_ETHColumn", (0.66, 0.60, 0.50), roughness=0.65)

        # MI6
        self.mi6_cream = principled_material("LF_MI6Cream", (0.72, 0.66, 0.52), roughness=0.55)
        self.mi6_cream_dark = principled_material("LF_MI6CreamDark", (0.58, 0.53, 0.42), roughness=0.6)
        self.mi6_glass = principled_material(
            "LF_MI6Glass",
            (0.08, 0.28, 0.26),
            roughness=0.12,
            metallic=0.25,
            transmission=0.15,
            emission=(0.12, 0.45, 0.42) if night else (0.08, 0.28, 0.26),
            emission_strength=0.6 if night else 0.0,
        )
        self.mi6_glass_dark = principled_material("LF_MI6GlassDark", (0.04, 0.16, 0.16), roughness=0.18, metallic=0.3)
        self.mi6_frame = principled_material("LF_MI6Frame", (0.12, 0.22, 0.20), roughness=0.4, metallic=0.4)
        self.mi6_river = principled_material(
            "LF_MI6River",
            (0.08, 0.16, 0.18),
            roughness=0.06,
            metallic=0.2,
            specular=0.95,
        )
        self.mi6_embankment = principled_material("LF_MI6Embankment", (0.18, 0.18, 0.17), roughness=0.7)
        self.mi6_accent = principled_material("LF_MI6Accent", (0.42, 0.12, 0.10), roughness=0.5)

        # Sydney
        self.syd_tile = principled_material("LF_SydTile", (0.93, 0.93, 0.90), roughness=0.22, specular=0.7)
        self.syd_tile_gloss = principled_material("LF_SydTileGloss", (0.96, 0.96, 0.94), roughness=0.08, specular=0.9)
        self.syd_rib = principled_material("LF_SydRib", (0.85, 0.85, 0.82), roughness=0.35)
        self.syd_podium = principled_material("LF_SydPodium", (0.42, 0.28, 0.20), roughness=0.7)
        self.syd_step = principled_material("LF_SydStep", (0.38, 0.25, 0.18), roughness=0.65)
        self.syd_glass = glass_material("LF_SydGlass", (0.15, 0.22, 0.28), roughness=0.04)
        self.syd_harbour = principled_material(
            "LF_SydHarbour",
            (0.05, 0.22, 0.32),
            roughness=0.05,
            metallic=0.15,
            specular=0.95,
        )
        self.syd_concrete = principled_material("LF_SydConcrete", (0.55, 0.53, 0.50), roughness=0.8)

        self.label = principled_material("LF_Label", (0.92, 0.90, 0.86), roughness=0.4)


MATS: Optional[Materials] = None


def get_mats() -> Materials:
    global MATS
    if MATS is None:
        MATS = Materials(night=CFG.night)
    return MATS


# =============================================================================
# 4. ARCHITECTURAL VOCABULARY
# =============================================================================

def window_density() -> float:
    return {"low": 0.45, "medium": 0.75, "high": 1.0}.get(CFG.window_density, 1.0)


def add_window_grid(
    name: str,
    origin: Vec3,
    count_x: int,
    count_z: int,
    spacing: Vec2,
    size: Vec2,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    mat: bpy.types.Material,
    rotation_z: float = 0.0,
    skip_chance: float = 0.0,
) -> list[bpy.types.Object]:
    """Place a grid of window panes. origin is the centre of the lower-left window."""
    objs = []
    dens = window_density()
    nx = max(1, int(round(count_x * dens)))
    nz = max(1, int(round(count_z * dens)))
    # Keep original spacing by stretching
    sx, sz = spacing
    for iz in range(count_z):
        for ix in range(count_x):
            if dens < 0.99 and (ix % 2 == 1 and iz % 2 == 1):
                continue
            if skip_chance and RNG.random() < skip_chance:
                continue
            x = origin[0] + ix * sx
            y = origin[1]
            z = origin[2] + iz * sz
            obj = make_box(
                f"{name}_{ix}_{iz}",
                (size[0], 0.12, size[1]),
                (x, y, z),
                collection,
                parent,
                mat,
                rotation=(0.0, 0.0, rotation_z),
            )
            objs.append(obj)
    return objs


def add_windows_on_wall(
    name: str,
    wall_center: Vec3,
    wall_length: float,
    wall_height: float,
    facing: str,
    floors: int,
    bays: int,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    mat: bpy.types.Material,
    sill: float = 1.2,
    win_w: float = 0.7,
    win_h: float = 1.5,
    inset: float = 0.42,
) -> None:
    """
    facing: +Y, -Y, +X, -X  — the outward normal of the wall.
    Windows sit slightly in front of the wall centreline.
    """
    if facing in ("+Y", "-Y"):
        sign = 1.0 if facing == "+Y" else -1.0
        y = wall_center[1] + sign * inset
        span = wall_length
        start_x = wall_center[0] - span * 0.5 + span / (bays + 1)
        step = span / (bays + 1)
        floor_h = (wall_height - sill - 0.6) / max(floors, 1)
        for f in range(floors):
            z = wall_center[2] - wall_height * 0.5 + sill + f * floor_h + win_h * 0.5
            for b in range(bays):
                x = start_x + b * step
                make_box(
                    f"{name}_{facing}_{f}_{b}",
                    (win_w, 0.14, win_h),
                    (x, y, z),
                    collection,
                    parent,
                    mat,
                )
    else:
        sign = 1.0 if facing == "+X" else -1.0
        x = wall_center[0] + sign * inset
        span = wall_length
        start_y = wall_center[1] - span * 0.5 + span / (bays + 1)
        step = span / (bays + 1)
        floor_h = (wall_height - sill - 0.6) / max(floors, 1)
        for f in range(floors):
            z = wall_center[2] - wall_height * 0.5 + sill + f * floor_h + win_h * 0.5
            for b in range(bays):
                y = start_y + b * step
                make_box(
                    f"{name}_{facing}_{f}_{b}",
                    (0.14, win_w, win_h),
                    (x, y, z),
                    collection,
                    parent,
                    mat,
                )


def crenellations(
    name: str,
    origin: Vec3,
    length: float,
    axis: str,
    count: int,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    mat: bpy.types.Material,
    merlon: Vec3 = (0.55, 0.55, 0.7),
    width_axis: str = "Y",
) -> None:
    """Row of merlons along a parapet. origin is the start (left) of the run, at merlon centre height."""
    if count <= 0:
        return
    step = length / count
    for i in range(count):
        t = (i + 0.5) * step
        if axis == "X":
            loc = (origin[0] + t, origin[1], origin[2])
        else:
            loc = (origin[0], origin[1] + t, origin[2])
        make_box(f"{name}_{i}", merlon, loc, collection, parent, mat)


def battlement_ring(
    name: str,
    cx: float,
    cy: float,
    cz: float,
    radius: float,
    count: int,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    mat: bpy.types.Material,
    merlon: Vec3 = (0.5, 0.5, 0.7),
) -> None:
    for i in range(count):
        a = (i / count) * math.tau
        x = cx + math.cos(a) * radius
        y = cy + math.sin(a) * radius
        make_box(
            f"{name}_{i}",
            merlon,
            (x, y, cz),
            collection,
            parent,
            mat,
            rotation=(0.0, 0.0, a),
        )


def gothic_window(
    name: str,
    location: Vec3,
    width: float,
    height: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    glass: bpy.types.Material,
    stone: bpy.types.Material,
    depth: float = 0.22,
) -> None:
    """Pointed-arch window: rectangular body + triangular head, with a thin stone frame."""
    body_h = height * 0.72
    make_box(f"{name}_body", (width, depth, body_h), (location[0], location[1], location[2] - (height - body_h) * 0.5), collection, parent, glass)
    # pointed head as a wedge sitting on top of the glass
    head_h = height - body_h
    make_wedge(
        f"{name}_head",
        (width, depth, head_h),
        (location[0], location[1], location[2] + body_h * 0.5 - (height - body_h) * 0.5),
        collection,
        parent,
        glass,
    )
    # stone mullion
    make_box(
        f"{name}_mullion",
        (0.08, depth + 0.04, body_h * 0.9),
        (location[0], location[1], location[2] - (height - body_h) * 0.5),
        collection,
        parent,
        stone,
    )


def flying_buttress(
    name: str,
    base: Vec3,
    height: float,
    reach: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    mat: bpy.types.Material,
    toward: str = "+Y",
) -> None:
    """Pier + diagonal flyer."""
    make_box(f"{name}_pier", (0.7, 0.7, height), (base[0], base[1], base[2] + height * 0.5), collection, parent, mat)
    # flyer as a rotated box
    flyer = make_box(
        f"{name}_flyer",
        (0.35, reach, 0.35),
        (base[0], base[1] + (reach * 0.5 if toward == "+Y" else -reach * 0.5), base[2] + height * 0.78),
        collection,
        parent,
        mat,
        rotation=(math.radians(-28 if toward == "+Y" else 28), 0.0, 0.0),
    )
    del flyer


def column_doric(
    name: str,
    location: Vec3,
    height: float,
    radius: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    shaft: bpy.types.Material,
    cap: bpy.types.Material,
) -> None:
    make_cylinder(f"{name}_plinth", radius * 1.35, 0.28, (location[0], location[1], location[2] + 0.14), 16, collection, parent, cap)
    make_cylinder(
        f"{name}_shaft",
        radius,
        height - 0.7,
        (location[0], location[1], location[2] + height * 0.5),
        16,
        collection,
        parent,
        shaft,
    )
    make_cylinder(
        f"{name}_echinus",
        radius * 1.25,
        0.18,
        (location[0], location[1], location[2] + height - 0.38),
        16,
        collection,
        parent,
        cap,
    )
    make_box(
        f"{name}_abacus",
        (radius * 2.6, radius * 2.6, 0.16),
        (location[0], location[1], location[2] + height - 0.16),
        collection,
        parent,
        cap,
    )


def cornice(
    name: str,
    center: Vec3,
    size_xy: Vec2,
    thickness: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    mat: bpy.types.Material,
    overhang: float = 0.25,
) -> None:
    make_box(
        name,
        (size_xy[0] + overhang * 2, size_xy[1] + overhang * 2, thickness),
        center,
        collection,
        parent,
        mat,
    )


def stairs(
    name: str,
    origin: Vec3,
    steps: int,
    step: Vec3,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    mat: bpy.types.Material,
    direction: str = "+Y",
) -> None:
    """origin is the centre of the first (lowest) tread."""
    sx, sy, sz = step
    for i in range(steps):
        if direction == "+Y":
            loc = (origin[0], origin[1] + i * sy, origin[2] + i * sz)
            size = (sx, sy * (steps - i), sz)
            loc = (origin[0], origin[1] + sy * i + size[1] * 0.5, origin[2] + sz * 0.5 + i * sz)
            size = (sx, sy, sz)
            loc = (origin[0], origin[1] + i * sy, origin[2] + i * sz)
        elif direction == "-Y":
            loc = (origin[0], origin[1] - i * sy, origin[2] + i * sz)
        elif direction == "+X":
            loc = (origin[0] + i * sx, origin[1], origin[2] + i * sz)
        else:
            loc = (origin[0] - i * sx, origin[1], origin[2] + i * sz)
        make_box(f"{name}_{i}", (sx, sy, sz), loc, collection, parent, mat)


def monumental_stair(
    name: str,
    origin: Vec3,
    width: float,
    steps: int,
    tread: float,
    riser: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    mat: bpy.types.Material,
    direction: str = "+Y",
) -> None:
    for i in range(steps):
        remaining = steps - i
        depth = remaining * tread
        if direction == "+Y":
            loc = (origin[0], origin[1] + depth * 0.5, origin[2] + i * riser + riser * 0.5)
            size = (width, depth, riser)
        elif direction == "-Y":
            loc = (origin[0], origin[1] - depth * 0.5, origin[2] + i * riser + riser * 0.5)
            size = (width, depth, riser)
        else:
            loc = (origin[0] + depth * 0.5, origin[1], origin[2] + i * riser + riser * 0.5)
            size = (depth, width, riser)
        make_box(f"{name}_{i}", size, loc, collection, parent, mat)


def pitched_dormer(
    name: str,
    location: Vec3,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    wall: bpy.types.Material,
    roof: bpy.types.Material,
    glass: bpy.types.Material,
    w: float = 0.7,
    d: float = 0.8,
    h: float = 1.1,
) -> None:
    make_box(f"{name}_body", (w, d, h * 0.65), (location[0], location[1], location[2]), collection, parent, wall)
    make_gabled_roof(
        f"{name}_roof",
        w,
        d,
        h * 0.45,
        (location[0], location[1], location[2] + h * 0.32),
        collection,
        parent,
        roof,
        axis="Y",
    )
    make_box(
        f"{name}_win",
        (w * 0.45, 0.08, h * 0.35),
        (location[0], location[1] + d * 0.52, location[2]),
        collection,
        parent,
        glass,
    )


def flag_on_pole(
    name: str,
    base: Vec3,
    height: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    pole_mat: bpy.types.Material,
    cloth_mat: bpy.types.Material,
) -> None:
    make_cylinder(f"{name}_pole", 0.05, height, (base[0], base[1], base[2] + height * 0.5), 8, collection, parent, pole_mat)
    make_box(
        f"{name}_cloth",
        (1.2, 0.04, 0.7),
        (base[0] + 0.62, base[1], base[2] + height - 0.45),
        collection,
        parent,
        cloth_mat,
    )


def simple_tree(
    name: str,
    location: Vec3,
    collection: bpy.types.Collection,
    parent: Optional[bpy.types.Object],
    height: float = 8.0,
    conifer: bool = False,
) -> None:
    m = get_mats()
    trunk_h = height * (0.45 if conifer else 0.35)
    make_cylinder(
        f"{name}_trunk",
        height * 0.04,
        trunk_h,
        (location[0], location[1], location[2] + trunk_h * 0.5),
        8,
        collection,
        parent,
        m.bark,
    )
    if conifer:
        for i, (r, z, d) in enumerate(
            (
                (height * 0.28, trunk_h * 0.55, height * 0.28),
                (height * 0.22, trunk_h * 0.55 + height * 0.18, height * 0.24),
                (height * 0.14, trunk_h * 0.55 + height * 0.36, height * 0.22),
            )
        ):
            make_cone(
                f"{name}_needles_{i}",
                r,
                d,
                (location[0], location[1], location[2] + z + d * 0.35),
                10,
                0.02,
                collection,
                parent,
                m.pine,
            )
    else:
        make_uv_sphere(
            f"{name}_crown",
            height * 0.28,
            (location[0], location[1], location[2] + trunk_h + height * 0.18),
            10,
            6,
            collection,
            parent,
            m.leaf,
        )


def hedge_box(
    name: str,
    size: Vec3,
    location: Vec3,
    collection: bpy.types.Collection,
    parent: Optional[bpy.types.Object],
) -> None:
    make_box(name, size, location, collection, parent, get_mats().leaf)


def lamp_post(
    name: str,
    location: Vec3,
    collection: bpy.types.Collection,
    parent: Optional[bpy.types.Object],
    height: float = 4.2,
) -> None:
    m = get_mats()
    make_cylinder(f"{name}_pole", 0.07, height, (location[0], location[1], location[2] + height * 0.5), 8, collection, parent, m.iron)
    make_uv_sphere(f"{name}_lamp", 0.22, (location[0], location[1], location[2] + height), 10, 6, collection, parent, m.night_glow)


# =============================================================================
# 5. LANDSCAPE HELPERS
# =============================================================================

def noisy_terrain(
    name: str,
    size: float,
    resolution: int,
    height: float,
    location: Vec3,
    collection: bpy.types.Collection,
    parent: Optional[bpy.types.Object],
    mat: bpy.types.Material,
    seed: int = 0,
    falloff: bool = True,
) -> bpy.types.Object:
    """Displaced grid. Z up. Centre at location."""
    verts: list[Vec3] = []
    faces: list[tuple] = []
    n = max(4, resolution)
    rng = random.Random(seed)
    # Use mathutils.noise when available, else hash.
    for j in range(n + 1):
        for i in range(n + 1):
            u = i / n
            v = j / n
            x = (u - 0.5) * size
            y = (v - 0.5) * size
            nx = u * 4.0 + seed * 0.17
            ny = v * 4.0 - seed * 0.13
            h = 0.0
            try:
                h = noise.noise(Vector((nx, ny, seed * 0.05))) * height
                h += 0.45 * noise.noise(Vector((nx * 2.3, ny * 2.1, 2.0))) * height
            except Exception:
                h = (math.sin(nx * 3.1) * math.cos(ny * 2.7) * 0.5 + rng.random() * 0.15) * height
            if falloff:
                d = math.hypot(u - 0.5, v - 0.5) * 2.0
                h *= max(0.0, 1.0 - d * d)
            verts.append((x, y, h))
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            b = a + 1
            c = a + n + 2
            d = a + n + 1
            faces.append((a, b, c, d))
    obj = mesh_from_pydata(name, verts, faces, collection, parent, mat, location, smooth=True)
    return obj


def water_plane(
    name: str,
    size: Union[float, Vec2],
    location: Vec3,
    collection: bpy.types.Collection,
    parent: Optional[bpy.types.Object],
    mat: Optional[bpy.types.Material] = None,
) -> bpy.types.Object:
    return make_plane(name, size, location, collection, parent, mat or get_mats().water)


# =============================================================================
# 6. WORLD / LIGHT / CAMERA
# =============================================================================

def setup_world(night: bool) -> None:
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("LF_World")
        bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    if night:
        bg.inputs["Color"].default_value = (0.012, 0.016, 0.03, 1.0)
        bg.inputs["Strength"].default_value = 0.35
    else:
        bg.inputs["Color"].default_value = (0.42, 0.55, 0.72, 1.0)
        bg.inputs["Strength"].default_value = 0.85
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])


def add_sun(name: str, location: Vec3, rotation: Vec3, energy: float, color: Vec3, collection: bpy.types.Collection) -> bpy.types.Object:
    light = bpy.data.lights.new(name, "SUN")
    light.energy = energy
    light.color = color
    light.angle = math.radians(5.0)
    obj = bpy.data.objects.new(name, light)
    obj.location = location
    obj.rotation_euler = Euler(rotation)
    obj["landmark_forge"] = True
    link_object(obj, collection)
    return obj


def add_area(name: str, location: Vec3, size: float, energy: float, color: Vec3, collection: bpy.types.Collection, parent: Optional[bpy.types.Object] = None) -> bpy.types.Object:
    light = bpy.data.lights.new(name, "AREA")
    light.energy = energy
    light.color = color
    light.size = size
    obj = bpy.data.objects.new(name, light)
    obj.location = location
    obj["landmark_forge"] = True
    link_object(obj, collection)
    tag_parent(obj, parent)
    return obj


def add_point(name: str, location: Vec3, energy: float, color: Vec3, collection: bpy.types.Collection, parent: Optional[bpy.types.Object] = None) -> bpy.types.Object:
    light = bpy.data.lights.new(name, "POINT")
    light.energy = energy
    light.color = color
    obj = bpy.data.objects.new(name, light)
    obj.location = location
    obj["landmark_forge"] = True
    link_object(obj, collection)
    tag_parent(obj, parent)
    return obj


def add_camera(name: str, location: Vec3, look_at: Vec3, collection: bpy.types.Collection, lens: float = 35.0) -> bpy.types.Object:
    cam = bpy.data.cameras.new(name)
    cam.lens = lens
    obj = bpy.data.objects.new(name, cam)
    obj.location = location
    obj["landmark_forge"] = True
    link_object(obj, collection)
    direction = Vector(look_at) - Vector(location)
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return obj


def setup_render() -> None:
    s = bpy.context.scene
    s.render.engine = "CYCLES" if "CYCLES" in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items else s.render.engine
    s.render.resolution_x = 1920
    s.render.resolution_y = 1080
    s.render.resolution_percentage = 100
    s.render.film_transparent = False
    try:
        s.cycles.samples = 64
        s.cycles.use_denoising = True
    except Exception:
        pass
    try:
        s.eevee.taa_render_samples = 64
    except Exception:
        pass
    try:
        s.eevee.use_ssr = True
        s.eevee.use_gtao = True
        s.eevee.use_bloom = True
    except Exception:
        pass


print("LF: core loaded", SCRIPT_VERSION)


# =============================================================================
# 7. HOGWARTS CASTLE  (fan-made architectural interpretation)
# =============================================================================
#
# Layout, looking north:
#   Black Lake in the south, cliff dropping into water.
#   Stone viaduct approaches from the south-west.
#   Great Hall sits on the south terrace (long E–W gothic hall).
#   Round clock tower attached at the south-west corner.
#   Cloister courtyard north of the hall.
#   Central keep / grand staircase tower.
#   Astronomy tower furthest north (tallest).
#   Cluster of conical-roof round towers on the west.
#   Greenhouses on the east terrace.
#   Covered wooden bridge on the west ravine.
#   Boathouse at the lake edge.
#
# This is an original procedural interpretation inspired by publicly known
# massing of a Scottish Gothic castle-school. It is fan work, not a copy of
# any studio mesh, and is not affiliated with Warner Bros. or J.K. Rowling.

def _hog_round_tower(
    name: str,
    x: float,
    y: float,
    z0: float,
    radius: float,
    height: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    roof_height: float = 6.0,
    crenel: bool = True,
    floors: int = 5,
    roof: bool = True,
) -> None:
    m = get_mats()
    make_cylinder(
        f"{name}_shaft",
        radius,
        height,
        (x, y, z0 + height * 0.5),
        24,
        collection,
        parent,
        m.hog_stone,
    )
    # slight batter / string courses
    for k, frac in enumerate((0.25, 0.5, 0.75)):
        make_cylinder(
            f"{name}_string_{k}",
            radius + 0.12,
            0.22,
            (x, y, z0 + height * frac),
            24,
            collection,
            parent,
            m.hog_stone_dark,
        )
    # windows around
    rings = max(3, floors)
    around = max(6, int(radius * 2.2))
    for r in range(rings):
        z = z0 + 2.2 + r * (height - 3.5) / max(rings - 1, 1)
        for i in range(around):
            if i % 2 == 1:
                continue
            a = (i / around) * math.tau + r * 0.2
            wx = x + math.cos(a) * (radius + 0.08)
            wy = y + math.sin(a) * (radius + 0.08)
            make_box(
                f"{name}_win_{r}_{i}",
                (0.14, 0.55, 1.15),
                (wx, wy, z),
                collection,
                parent,
                m.hog_glass,
                rotation=(0.0, 0.0, a),
            )
    if crenel:
        battlement_ring(
            f"{name}_crenel",
            x,
            y,
            z0 + height + 0.3,
            radius - 0.15,
            max(10, int(radius * 3.5)),
            collection,
            parent,
            m.hog_stone_dark,
            merlon=(0.42, 0.42, 0.7),
        )
        make_cylinder(
            f"{name}_walk",
            radius - 0.05,
            0.28,
            (x, y, z0 + height + 0.05),
            24,
            collection,
            parent,
            m.hog_stone_dark,
        )
    if roof:
        make_cone(
            f"{name}_roof",
            radius + 0.35,
            roof_height,
            (x, y, z0 + height + roof_height * 0.45),
            16,
            0.02,
            collection,
            parent,
            m.hog_slate,
        )
        make_cylinder(
            f"{name}_finial",
            0.08,
            1.4,
            (x, y, z0 + height + roof_height + 0.4),
            6,
            collection,
            parent,
            m.iron,
        )


def _hog_square_tower(
    name: str,
    x: float,
    y: float,
    z0: float,
    footprint: float,
    height: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    roof_h: float = 5.0,
    machicolations: bool = True,
) -> None:
    m = get_mats()
    make_box(
        f"{name}_body",
        (footprint, footprint, height),
        (x, y, z0 + height * 0.5),
        collection,
        parent,
        m.hog_stone,
    )
    if machicolations:
        make_box(
            f"{name}_corbel",
            (footprint + 0.7, footprint + 0.7, 0.55),
            (x, y, z0 + height - 0.4),
            collection,
            parent,
            m.hog_stone_dark,
        )
        battlement_ring  # noqa: keep imported
        # merlons on four sides
        n = max(4, int(footprint))
        for axis, sign in (("X", 1), ("X", -1), ("Y", 1), ("Y", -1)):
            for i in range(n):
                t = -footprint * 0.5 + (i + 0.5) * (footprint / n)
                if axis == "X":
                    loc = (x + sign * (footprint * 0.5 + 0.15), y + t, z0 + height + 0.25)
                else:
                    loc = (x + t, y + sign * (footprint * 0.5 + 0.15), z0 + height + 0.25)
                make_box(f"{name}_merlon_{axis}_{sign}_{i}", (0.45, 0.45, 0.7), loc, collection, parent, m.hog_stone_dark)
    make_gabled_roof(
        f"{name}_roof",
        footprint + 0.4,
        footprint + 0.4,
        roof_h,
        (x, y, z0 + height),
        collection,
        parent,
        m.hog_slate,
        axis="X",
    )
    add_windows_on_wall(
        f"{name}_wY",
        (x, y + footprint * 0.5, z0 + height * 0.5),
        footprint * 0.8,
        height,
        "+Y",
        max(3, int(height / 4)),
        2,
        collection,
        parent,
        m.hog_glass,
        win_w=0.55,
        win_h=1.3,
        inset=0.08,
    )
    add_windows_on_wall(
        f"{name}_wX",
        (x + footprint * 0.5, y, z0 + height * 0.5),
        footprint * 0.8,
        height,
        "+X",
        max(3, int(height / 4)),
        2,
        collection,
        parent,
        m.hog_glass,
        win_w=0.55,
        win_h=1.3,
        inset=0.08,
    )


def _hog_great_hall(
    ox: float,
    oy: float,
    oz: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
) -> None:
    m = get_mats()
    length, width, height = 52.0, 16.0, 20.0
    # plinth
    make_box(
        "LF_Hog_GH_plinth",
        (length + 4.0, width + 6.0, 1.6),
        (ox, oy, oz + 0.8),
        collection,
        parent,
        m.hog_stone_dark,
    )
    make_box(
        "LF_Hog_GH_body",
        (length, width, height),
        (ox, oy, oz + 1.6 + height * 0.5),
        collection,
        parent,
        m.hog_stone,
    )
    # clerestory setback
    make_box(
        "LF_Hog_GH_clere",
        (length - 1.2, width - 1.6, 4.2),
        (ox, oy, oz + 1.6 + height + 2.0),
        collection,
        parent,
        m.hog_stone,
    )
    make_gabled_roof(
        "LF_Hog_GH_roof",
        length + 1.4,
        width + 1.0,
        9.5,
        (ox, oy, oz + 1.6 + height + 4.2),
        collection,
        parent,
        m.hog_slate,
        axis="X",
    )
    # roof ridge finials / pinnacles
    for i, fx in enumerate((-18, -9, 0, 9, 18)):
        make_cone(
            f"LF_Hog_GH_pinnacle_{i}",
            0.45,
            3.2,
            (ox + fx, oy, oz + 1.6 + height + 4.2 + 9.5 + 0.4),
            6,
            0.0,
            collection,
            parent,
            m.hog_stone_dark,
        )
    # tall gothic windows along both long walls
    bays = 10
    for side, facing, yoff in (("S", "-Y", -width * 0.5 - 0.08), ("N", "+Y", width * 0.5 + 0.08)):
        for i in range(bays):
            x = ox - length * 0.5 + (i + 0.5) * (length / bays)
            gothic_window(
                f"LF_Hog_GH_win_{side}_{i}",
                (x, oy + yoff, oz + 1.6 + 7.2),
                1.55,
                8.8,
                collection,
                parent,
                m.hog_glass,
                m.hog_trim,
            )
            flying_buttress(
                f"LF_Hog_GH_butt_{side}_{i}",
                (x, oy + yoff + (-2.4 if side == "S" else 2.4), oz + 1.6),
                11.0,
                3.2,
                collection,
                parent,
                m.hog_stone_dark,
                toward="+Y" if side == "S" else "-Y",
            )
    # east rose / entrance
    gothic_window(
        "LF_Hog_GH_east",
        (ox + length * 0.5 + 0.1, oy, oz + 1.6 + 7.0),
        3.2,
        9.5,
        collection,
        parent,
        m.hall_glow,
        m.hog_trim,
        depth=0.3,
    )
    make_box(
        "LF_Hog_GH_door",
        (0.3, 3.2, 5.5),
        (ox - length * 0.5 - 0.05, oy, oz + 1.6 + 2.8),
        collection,
        parent,
        m.wood_dark,
    )
    # interior glow volume
    make_box(
        "LF_Hog_GH_glow",
        (length - 2.0, width - 2.0, 0.4),
        (ox, oy, oz + 1.6 + 0.4),
        collection,
        parent,
        m.hall_glow,
    )
    # dormers on the south roof pitch
    for i, fx in enumerate((-14, -7, 0, 7, 14)):
        pitched_dormer(
            f"LF_Hog_GH_dormer_{i}",
            (ox + fx, oy - width * 0.18, oz + 1.6 + height + 6.4),
            collection,
            parent,
            m.hog_stone,
            m.hog_slate,
            m.hog_glass,
        )
    # chimneys
    for i, fx in enumerate((-12, 12)):
        make_box(
            f"LF_Hog_GH_chimney_{i}",
            (1.4, 1.1, 5.5),
            (ox + fx, oy + 1.2, oz + 1.6 + height + 4.2 + 6.5),
            collection,
            parent,
            m.hog_stone_dark,
        )
        make_box(
            f"LF_Hog_GH_chimcap_{i}",
            (1.7, 1.4, 0.35),
            (ox + fx, oy + 1.2, oz + 1.6 + height + 4.2 + 9.3),
            collection,
            parent,
            m.hog_stone,
        )


def _hog_clock_tower(
    ox: float,
    oy: float,
    oz: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
) -> None:
    m = get_mats()
    h = 48.0
    r = 5.6
    make_cylinder("LF_Hog_Clock_shaft", r, h, (ox, oy, oz + h * 0.5), 28, collection, parent, m.hog_stone)
    make_cylinder("LF_Hog_Clock_belt", r + 0.25, 1.1, (ox, oy, oz + 22.0), 28, collection, parent, m.hog_stone_dark)
    make_cylinder("LF_Hog_Clock_topdrum", r + 0.4, 4.5, (ox, oy, oz + h - 1.5), 28, collection, parent, m.hog_stone_dark)
    battlement_ring("LF_Hog_Clock_cren", ox, oy, oz + h + 0.9, r + 0.15, 18, collection, parent, m.hog_stone_dark, (0.5, 0.5, 0.85))
    make_cone("LF_Hog_Clock_roof", r + 1.4, 22.0, (ox, oy, oz + h + 11.5), 24, 0.05, collection, parent, m.hog_slate)
    make_cylinder("LF_Hog_Clock_finial", 0.08, 2.4, (ox, oy, oz + h + 24.0), 6, collection, parent, m.iron)
    # clock faces
    for i, a in enumerate((0.0, math.pi * 0.5, math.pi, math.pi * 1.5)):
        fx = ox + math.cos(a) * (r + 0.12)
        fy = oy + math.sin(a) * (r + 0.12)
        make_cylinder(
            f"LF_Hog_Clock_face_{i}",
            1.7,
            0.18,
            (fx, fy, oz + 24.5),
            20,
            collection,
            parent,
            m.syd_tile,
            rotation=(math.pi * 0.5, 0.0, a + math.pi * 0.5),
        )
        make_box(
            f"LF_Hog_Clock_hand_{i}",
            (0.12, 0.08, 1.3),
            (fx + math.cos(a) * 0.1, fy + math.sin(a) * 0.1, oz + 24.7),
            collection,
            parent,
            m.iron,
            rotation=(0.0, 0.0, a),
        )
    # attached stair turret
    _hog_round_tower("LF_Hog_ClockTurret", ox + 5.2, oy - 4.4, oz, 2.1, 28.0, collection, parent, roof_height=5.5, floors=6)
    # windows
    for r_i in range(6):
        z = oz + 4.0 + r_i * 5.0
        for i in range(8):
            a = (i / 8) * math.tau
            make_box(
                f"LF_Hog_Clock_win_{r_i}_{i}",
                (0.16, 0.7, 1.6),
                (ox + math.cos(a) * (r + 0.05), oy + math.sin(a) * (r + 0.05), z),
                collection,
                parent,
                m.hog_glass,
                rotation=(0.0, 0.0, a),
            )


def _hog_astronomy(
    ox: float,
    oy: float,
    oz: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
) -> None:
    m = get_mats()
    h = 70.0
    fp = 7.5
    make_box("LF_Hog_Astro_body", (fp, fp, h), (ox, oy, oz + h * 0.5), collection, parent, m.hog_stone)
    make_box("LF_Hog_Astro_corbel", (fp + 1.1, fp + 1.1, 0.7), (ox, oy, oz + h - 0.2), collection, parent, m.hog_stone_dark)
    make_box("LF_Hog_Astro_lantern", (fp * 0.62, fp * 0.62, 6.5), (ox, oy, oz + h + 3.2), collection, parent, m.hog_stone)
    make_gabled_roof("LF_Hog_Astro_roof", fp * 0.7, fp * 0.7, 4.2, (ox, oy, oz + h + 6.5), collection, parent, m.hog_slate, axis="X")
    # viewing balcony
    make_cylinder("LF_Hog_Astro_balc", fp * 0.7, 0.28, (ox, oy, oz + h + 0.2), 16, collection, parent, m.hog_stone_dark)
    battlement_ring("LF_Hog_Astro_cren", ox, oy, oz + h + 0.7, fp * 0.62, 14, collection, parent, m.hog_stone_dark)
    # tall slit windows
    for f in range(8):
        z = oz + 4 + f * 5.5
        for facing, loc in (
            ("+Y", (ox, oy + fp * 0.5 + 0.05, z)),
            ("-Y", (ox, oy - fp * 0.5 - 0.05, z)),
            ("+X", (ox + fp * 0.5 + 0.05, oy, z)),
            ("-X", (ox - fp * 0.5 - 0.05, oy, z)),
        ):
            size = (0.7, 0.14, 2.4) if facing.endswith("Y") else (0.14, 0.7, 2.4)
            make_box(f"LF_Hog_Astro_win_{f}_{facing}", size, loc, collection, parent, m.hog_glass)
    flag_on_pole("LF_Hog_Astro_flag", (ox, oy, oz + h + 10.5), 6.0, collection, parent, m.iron, m.flag_red)


def _hog_cloister(
    ox: float,
    oy: float,
    oz: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
) -> None:
    m = get_mats()
    inner_w, inner_d = 18.0, 14.0
    wall_t, wall_h = 3.2, 5.5
    # four cloister walks
    make_box("LF_Hog_Clo_N", (inner_w + wall_t * 2, wall_t, wall_h), (ox, oy + inner_d * 0.5 + wall_t * 0.5, oz + wall_h * 0.5), collection, parent, m.hog_stone)
    make_box("LF_Hog_Clo_S", (inner_w + wall_t * 2, wall_t, wall_h), (ox, oy - inner_d * 0.5 - wall_t * 0.5, oz + wall_h * 0.5), collection, parent, m.hog_stone)
    make_box("LF_Hog_Clo_E", (wall_t, inner_d, wall_h), (ox + inner_w * 0.5 + wall_t * 0.5, oy, oz + wall_h * 0.5), collection, parent, m.hog_stone)
    make_box("LF_Hog_Clo_W", (wall_t, inner_d, wall_h), (ox - inner_w * 0.5 - wall_t * 0.5, oy, oz + wall_h * 0.5), collection, parent, m.hog_stone)
    make_box("LF_Hog_Clo_yard", (inner_w, inner_d, 0.15), (ox, oy, oz + 0.08), collection, parent, m.dark_grass)
    make_box("LF_Hog_Clo_roof", (inner_w + wall_t * 2 + 0.4, inner_d + wall_t * 2 + 0.4, 0.35), (ox, oy, oz + wall_h + 0.1), collection, parent, m.hog_slate)
    # arcade openings
    for i in range(5):
        x = ox - 7.2 + i * 3.6
        make_box(f"LF_Hog_Clo_archN_{i}", (1.5, 0.4, 2.6), (x, oy + inner_d * 0.5 + 0.1, oz + 2.0), collection, parent, m.hog_glass)
        make_box(f"LF_Hog_Clo_archS_{i}", (1.5, 0.4, 2.6), (x, oy - inner_d * 0.5 - 0.1, oz + 2.0), collection, parent, m.hog_glass)
    # fountain
    make_cylinder("LF_Hog_Clo_fount", 1.6, 0.45, (ox, oy, oz + 0.4), 16, collection, parent, m.hog_stone_dark)
    make_cylinder("LF_Hog_Clo_fount2", 0.7, 1.2, (ox, oy, oz + 1.1), 12, collection, parent, m.hog_stone)
    make_uv_sphere("LF_Hog_Clo_water", 1.2, (ox, oy, oz + 0.55), 12, 6, collection, parent, m.water, hemisphere=True)


def _hog_greenhouses(
    ox: float,
    oy: float,
    oz: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
) -> None:
    m = get_mats()
    glass = glass_material("LF_HogGreenhouseGlass", (0.35, 0.55, 0.32), roughness=0.12)
    for i in range(4):
        x = ox + i * 9.5
        make_box(f"LF_Hog_GHHouse_{i}_base", (8.0, 14.0, 0.4), (x, oy, oz + 0.2), collection, parent, m.hog_stone_dark)
        make_box(f"LF_Hog_GHHouse_{i}_body", (7.6, 13.4, 4.2), (x, oy, oz + 2.5), collection, parent, glass)
        make_gabled_roof(f"LF_Hog_GHHouse_{i}_roof", 8.0, 14.0, 2.8, (x, oy, oz + 4.6), collection, parent, glass, axis="Y")
        # iron ribs
        for k in range(5):
            y = oy - 6.0 + k * 3.0
            make_box(f"LF_Hog_GHHouse_{i}_rib_{k}", (0.08, 0.08, 4.2), (x - 3.7, y, oz + 2.5), collection, parent, m.iron)
            make_box(f"LF_Hog_GHHouse_{i}_rib2_{k}", (0.08, 0.08, 4.2), (x + 3.7, y, oz + 2.5), collection, parent, m.iron)


def _hog_viaduct(
    ox: float,
    oy: float,
    oz: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
) -> None:
    m = get_mats()
    length = 78.0
    make_box("LF_Hog_Via_deck", (6.5, length, 1.1), (ox, oy, oz + 10.5), collection, parent, m.hog_stone)
    crenellations("LF_Hog_Via_merlonL", (ox - 3.1, oy - length * 0.5, oz + 11.4), length, "Y", 22, collection, parent, m.hog_stone_dark, (0.4, 0.5, 0.7))
    crenellations("LF_Hog_Via_merlonR", (ox + 3.1, oy - length * 0.5, oz + 11.4), length, "Y", 22, collection, parent, m.hog_stone_dark, (0.4, 0.5, 0.7))
    arches = 10
    for i in range(arches):
        y = oy - length * 0.5 + (i + 0.5) * (length / arches)
        make_box(f"LF_Hog_Via_pier_{i}", (5.2, 2.2, 10.2), (ox, y, oz + 5.1), collection, parent, m.hog_stone_dark)
        make_arch_solid(f"LF_Hog_Via_arch_{i}", 7.5, 8.5, 5.0, 1.1, (ox, y + 3.6, oz), collection, parent, m.hog_stone)


def _hog_wooden_bridge(
    ox: float,
    oy: float,
    oz: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
) -> None:
    m = get_mats()
    make_box("LF_Hog_WBridge_deck", (4.0, 28.0, 0.35), (ox, oy, oz + 6.0), collection, parent, m.wood)
    make_gabled_roof("LF_Hog_WBridge_roof", 4.4, 28.0, 3.2, (ox, oy, oz + 9.2), collection, parent, m.hog_slate, axis="Y")
    for i, y in enumerate((-12, -6, 0, 6, 12)):
        make_box(f"LF_Hog_WBridge_postL_{i}", (0.28, 0.28, 3.4), (ox - 1.7, oy + y, oz + 7.6), collection, parent, m.wood_dark)
        make_box(f"LF_Hog_WBridge_postR_{i}", (0.28, 0.28, 3.4), (ox + 1.7, oy + y, oz + 7.6), collection, parent, m.wood_dark)
        make_box(f"LF_Hog_WBridge_legL_{i}", (0.4, 0.4, 6.0), (ox - 1.5, oy + y, oz + 3.0), collection, parent, m.wood_dark)
        make_box(f"LF_Hog_WBridge_legR_{i}", (0.4, 0.4, 6.0), (ox + 1.5, oy + y, oz + 3.0), collection, parent, m.wood_dark)


def _hog_boathouse(
    ox: float,
    oy: float,
    oz: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
) -> None:
    m = get_mats()
    make_box("LF_Hog_Boat_body", (10.0, 16.0, 5.0), (ox, oy, oz + 2.5), collection, parent, m.hog_stone)
    make_gabled_roof("LF_Hog_Boat_roof", 10.5, 16.5, 4.0, (ox, oy, oz + 5.0), collection, parent, m.hog_slate, axis="Y")
    make_box("LF_Hog_Boat_door", (3.5, 0.2, 3.2), (ox, oy - 8.1, oz + 1.8), collection, parent, m.wood_dark)
    make_box("LF_Hog_Boat_dock", (8.0, 10.0, 0.35), (ox, oy - 14.0, oz + 0.3), collection, parent, m.wood)
    for i, x in enumerate((-2.2, 2.2)):
        make_box(f"LF_Hog_Boat_hull_{i}", (1.4, 5.5, 0.7), (ox + x, oy - 14.0, oz + 0.6), collection, parent, m.wood_dark)


def _hog_keep(
    ox: float,
    oy: float,
    oz: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
) -> None:
    m = get_mats()
    make_box("LF_Hog_Keep", (22.0, 18.0, 18.0), (ox, oy, oz + 9.0), collection, parent, m.hog_stone)
    make_box("LF_Hog_Keep_upper", (18.0, 14.0, 8.0), (ox, oy, oz + 22.0), collection, parent, m.hog_stone_dark)
    make_gabled_roof("LF_Hog_Keep_roof", 19.0, 15.0, 7.0, (ox, oy, oz + 26.0), collection, parent, m.hog_slate, axis="X")
    add_windows_on_wall("LF_Hog_KeepS", (ox, oy - 9.0, oz + 9.0), 18.0, 18.0, "-Y", 4, 5, collection, parent, m.hog_glass, win_h=1.8)
    add_windows_on_wall("LF_Hog_KeepN", (ox, oy + 9.0, oz + 9.0), 18.0, 18.0, "+Y", 4, 5, collection, parent, m.hog_glass, win_h=1.8)
    add_windows_on_wall("LF_Hog_KeepE", (ox + 11.0, oy, oz + 9.0), 14.0, 18.0, "+X", 4, 4, collection, parent, m.hog_glass, win_h=1.8)
    add_windows_on_wall("LF_Hog_KeepW", (ox - 11.0, oy, oz + 9.0), 14.0, 18.0, "-X", 4, 4, collection, parent, m.hog_glass, win_h=1.8)
    # grand door
    make_box("LF_Hog_Keep_door", (3.6, 0.35, 6.5), (ox, oy - 9.15, oz + 3.4), collection, parent, m.wood_dark)
    make_box("LF_Hog_Keep_portal", (5.2, 1.2, 8.0), (ox, oy - 9.6, oz + 4.1), collection, parent, m.hog_stone_dark)


def _hog_landscape(
    ox: float,
    oy: float,
    oz: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
) -> None:
    m = get_mats()
    noisy_terrain("LF_Hog_Cliff", 130.0, 28, 18.0, (ox, oy + 8.0, oz - 2.0), collection, parent, m.cliff, seed=7, falloff=True)
    make_box("LF_Hog_Terrace", (90.0, 70.0, 3.5), (ox, oy + 6.0, oz + 1.2), collection, parent, m.hog_stone_dark)
    make_box("LF_Hog_Grass", (86.0, 66.0, 0.3), (ox, oy + 6.0, oz + 3.05), collection, parent, m.dark_grass)
    water_plane("LF_Hog_Lake", (160.0, 90.0), (ox, oy - 48.0, oz - 0.4), collection, parent, m.water_deep)
    # shoreline rocks
    for i in range(18):
        a = RNG.uniform(-40, 40)
        b = RNG.uniform(-70, -28)
        s = RNG.uniform(1.5, 4.5)
        make_box(
            f"LF_Hog_Rock_{i}",
            (s, s * 1.2, s * 0.7),
            (ox + a, oy + b, oz + s * 0.2),
            collection,
            parent,
            m.rock,
            rotation=(0.0, 0.0, RNG.random() * math.tau),
        )
    # pines
    for i in range(22):
        simple_tree(
            f"LF_Hog_Pine_{i}",
            (ox + RNG.uniform(-48, 48), oy + RNG.uniform(18, 48), oz + 3.1),
            collection,
            parent,
            height=RNG.uniform(9.0, 16.0),
            conifer=True,
        )
    # distant hills
    for i, (dx, dy, h) in enumerate(((-70, 55, 22), (75, 60, 18), (-20, 70, 26))):
        make_uv_sphere(f"LF_Hog_Hill_{i}", h, (ox + dx, oy + dy, oz + h * 0.15), 10, 6, collection, parent, m.cliff)


def build_hogwarts(origin: Vec3, collection: Optional[bpy.types.Collection] = None) -> bpy.types.Object:
    col = collection or ensure_collection("LF_Hogwarts")
    root = new_empty("LF_Hogwarts_Root", origin, col)
    ox, oy, oz = 0.0, 0.0, 0.0
    _hog_landscape(ox, oy, oz, col, root)
    _hog_great_hall(ox + 4.0, oy - 8.0, oz + 3.2, col, root)
    _hog_clock_tower(ox - 18.0, oy - 14.0, oz + 3.2, col, root)
    _hog_cloister(ox + 4.0, oy + 8.0, oz + 3.2, col, root)
    _hog_keep(ox + 2.0, oy + 22.0, oz + 3.2, col, root)
    _hog_astronomy(ox + 8.0, oy + 38.0, oz + 3.2, col, root)
    _hog_round_tower("LF_Hog_Gryff", ox - 16.0, oy + 16.0, oz + 3.2, 4.8, 36.0, col, root, roof_height=12.0, floors=7)
    _hog_round_tower("LF_Hog_Rav", ox - 24.0, oy + 6.0, oz + 3.2, 4.2, 32.0, col, root, roof_height=11.0, floors=6)
    _hog_round_tower("LF_Hog_Huff", ox + 26.0, oy + 12.0, oz + 3.2, 4.4, 30.0, col, root, roof_height=10.0, floors=6)
    _hog_round_tower("LF_Hog_Slyth", ox + 22.0, oy - 18.0, oz + 3.2, 3.8, 28.0, col, root, roof_height=9.5, floors=6)
    _hog_square_tower("LF_Hog_BellL", ox - 8.0, oy - 22.0, oz + 3.2, 5.5, 34.0, col, root, roof_h=8.0)
    _hog_square_tower("LF_Hog_BellR", ox + 16.0, oy - 22.0, oz + 3.2, 5.5, 34.0, col, root, roof_h=8.0)
    _hog_greenhouses(ox + 34.0, oy + 28.0, oz + 3.2, col, root)
    _hog_viaduct(ox - 28.0, oy - 52.0, oz - 2.0, col, root)
    _hog_wooden_bridge(ox - 34.0, oy + 4.0, oz + 1.0, col, root)
    _hog_boathouse(ox + 18.0, oy - 38.0, oz - 0.2, col, root)
    # walls linking clusters
    m = get_mats()
    make_box("LF_Hog_CurtainW", (4.0, 28.0, 10.0), (ox - 20.0, oy + 2.0, oz + 8.2), col, root, m.hog_stone)
    make_box("LF_Hog_CurtainE", (18.0, 4.0, 10.0), (ox + 16.0, oy + 2.0, oz + 8.2), col, root, m.hog_stone)
    make_box("LF_Hog_CurtainN", (28.0, 4.0, 12.0), (ox + 2.0, oy + 30.0, oz + 9.2), col, root, m.hog_stone)
    crenellations("LF_Hog_CurtainW_c", (ox - 22.0, oy - 12.0, oz + 13.6), 28.0, "Y", 10, col, root, m.hog_stone_dark)
    # owlery
    _hog_round_tower("LF_Hog_Owlery", ox - 30.0, oy + 32.0, oz + 3.2, 3.2, 18.0, col, root, roof_height=6.0, floors=4)
    # lamps along viaduct
    for i in range(6):
        lamp_post(f"LF_Hog_Lamp_{i}", (ox - 8.5, oy - 60.0 + i * 8.0, oz + 7.4), col, root, height=3.6)
    add_camera(
        "LF_Hog_Cam",
        (origin[0] + 10.0, origin[1] - 78.0, origin[2] + 10.0),
        (origin[0] - 6.0, origin[1] + 8.0, origin[2] + 28.0),
        col,
        lens=34.0,
    )
    add_point("LF_Hog_Fill", (origin[0] + 6.0, origin[1] - 40.0, origin[2] + 18.0), 900.0, (1.0, 0.84, 0.55), col, root)
    add_point("LF_Hog_WashL", (origin[0] - 12.0, origin[1] - 36.0, origin[2] + 10.0), 600.0, (1.0, 0.78, 0.42), col, root)
    add_point("LF_Hog_WashR", (origin[0] + 22.0, origin[1] - 32.0, origin[2] + 12.0), 520.0, (1.0, 0.80, 0.48), col, root)
    print("LF: Hogwarts built")
    return root


# =============================================================================
# 8. ETH ZÜRICH  — Hauptgebäude (Gottfried Semper 1864, dome Gustav Gull ~1920)
# =============================================================================
#
# Massing:
#   Long E–W wings around two inner courtyards.
#   Circular colonnaded drum + dark dome on the east (Rämistrasse) plaza.
#   Polyterrasse as a wide south terrace overlooking the city.
#   Neoclassical sandstone, rusticated ground floor, arched piano-nobile
#   windows, dark tiled roofs, copper lantern on the dome.

def _eth_wing(
    name: str,
    cx: float,
    cy: float,
    cz: float,
    sx: float,
    sy: float,
    floors: int,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    long_axis: str = "X",
) -> None:
    m = get_mats()
    storey = 4.4
    rust_h = 5.2
    body_h = floors * storey
    total = rust_h + body_h
    make_box(f"{name}_rustica", (sx, sy, rust_h), (cx, cy, cz + rust_h * 0.5), collection, parent, m.eth_rustica)
    make_box(f"{name}_body", (sx - 0.4, sy - 0.4, body_h), (cx, cy, cz + rust_h + body_h * 0.5), collection, parent, m.eth_sandstone)
    # attic
    make_box(f"{name}_attic", (sx - 0.8, sy - 0.8, 2.4), (cx, cy, cz + total + 1.1), collection, parent, m.eth_sandstone_dark)
    make_gabled_roof(
        f"{name}_roof",
        sx + 0.6 if long_axis == "X" else sy + 0.6,
        sy + 0.6 if long_axis == "X" else sx + 0.6,
        4.8,
        (cx, cy, cz + total + 2.4),
        collection,
        parent,
        m.eth_roof,
        axis=long_axis,
    )
    cornice(f"{name}_cornice", (cx, cy, cz + total + 0.15), (sx, sy), 0.35, collection, parent, m.eth_column, overhang=0.35)
    # string course
    make_box(f"{name}_string", (sx + 0.15, sy + 0.15, 0.28), (cx, cy, cz + rust_h + 0.1), collection, parent, m.eth_sandstone_dark)
    # windows
    long = sx if long_axis == "X" else sy
    bays = max(6, int(long / 3.4))
    if long_axis == "X":
        add_windows_on_wall(f"{name}_N", (cx, cy + sy * 0.5, cz + rust_h + body_h * 0.5), sx - 4, body_h, "+Y", floors, bays, collection, parent, m.eth_window, sill=0.8, win_w=1.15, win_h=2.3, inset=0.05)
        add_windows_on_wall(f"{name}_S", (cx, cy - sy * 0.5, cz + rust_h + body_h * 0.5), sx - 4, body_h, "-Y", floors, bays, collection, parent, m.eth_window, sill=0.8, win_w=1.15, win_h=2.3, inset=0.05)
        add_windows_on_wall(f"{name}_Nr", (cx, cy + sy * 0.5, cz + rust_h * 0.5), sx - 4, rust_h, "+Y", 1, bays, collection, parent, m.eth_window, sill=1.4, win_w=1.3, win_h=2.6, inset=0.05)
        add_windows_on_wall(f"{name}_Sr", (cx, cy - sy * 0.5, cz + rust_h * 0.5), sx - 4, rust_h, "-Y", 1, bays, collection, parent, m.eth_window, sill=1.4, win_w=1.3, win_h=2.6, inset=0.05)
    else:
        add_windows_on_wall(f"{name}_E", (cx + sx * 0.5, cy, cz + rust_h + body_h * 0.5), sy - 4, body_h, "+X", floors, bays, collection, parent, m.eth_window, sill=0.8, win_w=1.15, win_h=2.3, inset=0.05)
        add_windows_on_wall(f"{name}_W", (cx - sx * 0.5, cy, cz + rust_h + body_h * 0.5), sy - 4, body_h, "-X", floors, bays, collection, parent, m.eth_window, sill=0.8, win_w=1.15, win_h=2.3, inset=0.05)
    # roof dormers / skylights
    n_sky = max(4, int(long / 8))
    for i in range(n_sky):
        t = -long * 0.35 + i * (long * 0.7 / max(n_sky - 1, 1))
        if long_axis == "X":
            loc = (cx + t, cy, cz + total + 4.6)
        else:
            loc = (cx, cy + t, cz + total + 4.6)
        make_box(f"{name}_sky_{i}", (1.6, 1.2, 0.3), loc, collection, parent, m.eth_window)


def _eth_dome(
    cx: float,
    cy: float,
    cz: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
) -> None:
    m = get_mats()
    r_base = 14.0
    # rusticated circular podium
    make_cylinder("LF_ETH_DomePodium", r_base, 7.5, (cx, cy, cz + 3.75), 32, collection, parent, m.eth_rustica)
    # doors in podium
    for i, a in enumerate((0.0, math.pi * 0.5, math.pi, math.pi * 1.5)):
        make_box(
            f"LF_ETH_DomeDoor_{i}",
            (2.4, 0.4, 3.6),
            (cx + math.cos(a) * (r_base - 0.1), cy + math.sin(a) * (r_base - 0.1), cz + 2.0),
            collection,
            parent,
            m.wood_dark,
            rotation=(0.0, 0.0, a + math.pi * 0.5),
        )
    # piano nobile drum
    make_cylinder("LF_ETH_Drum", r_base - 0.6, 9.5, (cx, cy, cz + 7.5 + 4.75), 32, collection, parent, m.eth_sandstone)
    # colonnade — 16 giant order columns
    n_col = 16
    for i in range(n_col):
        a = (i / n_col) * math.tau + math.pi / n_col
        column_doric(
            f"LF_ETH_Col_{i}",
            (cx + math.cos(a) * (r_base - 1.3), cy + math.sin(a) * (r_base - 1.3), cz + 7.5),
            9.0,
            0.55,
            collection,
            parent,
            m.eth_column,
            m.eth_sandstone,
        )
    # entablature ring
    make_torus("LF_ETH_Entablature", r_base - 0.4, 0.55, (cx, cy, cz + 17.2), 32, 8, collection, parent, m.eth_sandstone)
    make_cylinder("LF_ETH_AtticRing", r_base - 1.6, 2.4, (cx, cy, cz + 18.6), 32, collection, parent, m.eth_sandstone_dark)
    # dark dome
    make_uv_sphere("LF_ETH_Dome", 11.5, (cx, cy, cz + 19.8), 28, 12, collection, parent, m.eth_dome, hemisphere=True)
    # lantern / cupola
    make_cylinder("LF_ETH_Lantern", 2.6, 3.8, (cx, cy, cz + 19.8 + 11.5 + 1.6), 16, collection, parent, m.eth_column)
    for i in range(8):
        a = (i / 8) * math.tau
        column_doric(
            f"LF_ETH_LanternCol_{i}",
            (cx + math.cos(a) * 2.1, cy + math.sin(a) * 2.1, cz + 31.2),
            3.2,
            0.16,
            collection,
            parent,
            m.eth_column,
            m.eth_copper,
        )
    make_uv_sphere("LF_ETH_LanternDome", 2.8, (cx, cy, cz + 34.6), 16, 8, collection, parent, m.eth_copper, hemisphere=True)
    make_cone("LF_ETH_Finial", 0.25, 1.8, (cx, cy, cz + 38.0), 8, 0.0, collection, parent, m.eth_copper)
    make_uv_sphere("LF_ETH_Orb", 0.35, (cx, cy, cz + 39.0), 8, 6, collection, parent, m.gold)
    # inner drum windows
    for i in range(12):
        a = (i / 12) * math.tau
        make_box(
            f"LF_ETH_DrumWin_{i}",
            (1.4, 0.2, 2.8),
            (cx + math.cos(a) * (r_base - 0.7), cy + math.sin(a) * (r_base - 0.7), cz + 12.2),
            collection,
            parent,
            m.eth_window,
            rotation=(0.0, 0.0, a + math.pi * 0.5),
        )


def _eth_courtyard(
    name: str,
    cx: float,
    cy: float,
    cz: float,
    sx: float,
    sy: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
) -> None:
    m = get_mats()
    make_box(f"{name}_floor", (sx, sy, 0.2), (cx, cy, cz + 0.1), collection, parent, m.eth_plaza)
    hedge_box(f"{name}_hedgeN", (sx - 2, 0.7, 1.2), (cx, cy + sy * 0.5 - 0.8, cz + 0.8), collection, parent)
    hedge_box(f"{name}_hedgeS", (sx - 2, 0.7, 1.2), (cx, cy - sy * 0.5 + 0.8, cz + 0.8), collection, parent)
    make_cylinder(f"{name}_fount", 2.4, 0.4, (cx, cy, cz + 0.35), 20, collection, parent, m.eth_sandstone)
    make_uv_sphere(f"{name}_water", 2.0, (cx, cy, cz + 0.45), 12, 6, collection, parent, m.water, hemisphere=True)


def build_eth(origin: Vec3, collection: Optional[bpy.types.Collection] = None) -> bpy.types.Object:
    col = collection or ensure_collection("LF_ETH_Zurich")
    root = new_empty("LF_ETH_Root", origin, col)
    m = get_mats()
    ox, oy, oz = 0.0, 0.0, 0.0
    # ground / polyterrasse
    make_box("LF_ETH_Ground", (120.0, 90.0, 2.0), (ox, oy, oz + 1.0), col, root, m.eth_plaza)
    make_box("LF_ETH_Polyterrasse", (110.0, 22.0, 1.4), (ox, oy - 38.0, oz + 2.4), col, root, m.eth_plaza)
    monumental_stair("LF_ETH_PolyStairs", (ox, oy - 52.0, oz), 28.0, 12, 1.1, 0.22, col, root, m.eth_sandstone, direction="-Y")
    # U / H plan: north, south, west, east wings + two courtyards
    _eth_wing("LF_ETH_North", ox, oy + 22.0, oz + 2.0, 96.0, 16.0, 4, col, root, "X")
    _eth_wing("LF_ETH_South", ox, oy - 18.0, oz + 2.0, 96.0, 16.0, 4, col, root, "X")
    _eth_wing("LF_ETH_West", ox - 40.0, oy + 2.0, oz + 2.0, 16.0, 28.0, 4, col, root, "Y")
    _eth_wing("LF_ETH_East", ox + 40.0, oy + 2.0, oz + 2.0, 16.0, 28.0, 4, col, root, "Y")
    _eth_courtyard("LF_ETH_CourtW", ox - 18.0, oy + 2.0, oz + 2.0, 22.0, 22.0, col, root)
    _eth_courtyard("LF_ETH_CourtE", ox + 18.0, oy + 2.0, oz + 2.0, 22.0, 22.0, col, root)
    # dome sits in the north plaza (Rämistrasse) — the iconic courtyard view
    _eth_dome(ox, oy + 52.0, oz + 2.0, col, root)
    make_cylinder("LF_ETH_DomePlaza", 38.0, 0.35, (ox, oy + 68.0, oz + 2.15), 40, col, root, m.eth_plaza)
    hedge_box("LF_ETH_DomeHedge", (1.0, 28.0, 1.1), (ox + 42.0, oy + 2.0, oz + 2.8), col, root)
    # central connecting hall
    make_box("LF_ETH_Aula", (18.0, 14.0, 12.0), (ox, oy + 2.0, oz + 8.0), col, root, m.eth_sandstone)
    make_gabled_roof("LF_ETH_AulaRoof", 18.5, 14.5, 5.0, (ox, oy + 2.0, oz + 14.0), col, root, m.eth_roof, axis="Y")
    # west entrance risalit + pediment
    make_box("LF_ETH_Risalit", (8.0, 18.0, 22.0), (ox - 52.0, oy + 2.0, oz + 13.0), col, root, m.eth_sandstone)
    make_wedge("LF_ETH_Pediment", (8.5, 18.5, 4.5), (ox - 52.0, oy + 2.0, oz + 24.2), col, root, m.eth_sandstone)
    make_box("LF_ETH_Portal", (0.4, 4.5, 7.5), (ox - 56.2, oy + 2.0, oz + 6.0), col, root, m.wood_dark)
    for i, y in enumerate((-5.5, -1.8, 1.8, 5.5)):
        column_doric(f"LF_ETH_Portico_{i}", (ox - 56.5, oy + 2.0 + y, oz + 2.0), 10.0, 0.42, col, root, m.eth_column, m.eth_sandstone)
    # city-side trees
    for i in range(10):
        simple_tree(f"LF_ETH_Tree_{i}", (ox - 50 + i * 11.0, oy - 44.0, oz + 3.1), col, root, height=RNG.uniform(7.0, 11.0), conifer=False)
    for i in range(8):
        lamp_post(f"LF_ETH_Lamp_{i}", (ox - 40 + i * 12.0, oy - 36.0, oz + 3.1), col, root)
    add_camera("LF_ETH_Cam", (origin[0] + 8.0, origin[1] + 95.0, origin[2] + 16.0), (origin[0], origin[1] + 40.0, origin[2] + 24.0), col, 32.0)
    print("LF: ETH Zürich built")
    return root


# =============================================================================
# 9. MI6 / SIS BUILDING  — Vauxhall Cross, Terry Farrell 1994
# =============================================================================
#
# Postmodern ziggurat: cream limestone, green glass, stepped terraces,
# central aedicule, cylindrical side volumes, Thames embankment.

def _mi6_glass_block(
    name: str,
    size: Vec3,
    location: Vec3,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    mullion_step: float = 2.4,
) -> None:
    m = get_mats()
    make_box(name, size, location, collection, parent, m.mi6_glass)
    # mullions
    sx, sy, sz = size
    nx = max(2, int(sx / mullion_step))
    nz = max(2, int(sz / 3.2))
    for i in range(nx + 1):
        x = location[0] - sx * 0.5 + i * (sx / nx)
        make_box(f"{name}_mulV_{i}", (0.08, sy + 0.04, sz), (x, location[1], location[2]), collection, parent, m.mi6_frame)
    for j in range(nz + 1):
        z = location[2] - sz * 0.5 + j * (sz / nz)
        make_box(f"{name}_mulH_{j}", (sx, sy + 0.04, 0.08), (location[0], location[1], z), collection, parent, m.mi6_frame)


def _mi6_step_layer(
    name: str,
    cx: float,
    cy: float,
    z: float,
    sx: float,
    sy: float,
    h: float,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    glass_inset: bool = True,
) -> None:
    m = get_mats()
    make_box(f"{name}_mass", (sx, sy, h), (cx, cy, z + h * 0.5), collection, parent, m.mi6_cream)
    # terrace slab
    make_box(f"{name}_slab", (sx + 1.6, sy + 1.6, 0.45), (cx, cy, z + h + 0.1), collection, parent, m.mi6_cream_dark)
    if glass_inset:
        _mi6_glass_block(f"{name}_gN", (sx * 0.72, 0.35, h * 0.72), (cx, cy + sy * 0.5 + 0.05, z + h * 0.5), collection, parent)
        _mi6_glass_block(f"{name}_gS", (sx * 0.72, 0.35, h * 0.72), (cx, cy - sy * 0.5 - 0.05, z + h * 0.5), collection, parent)
        _mi6_glass_block(f"{name}_gE", (0.35, sy * 0.55, h * 0.72), (cx + sx * 0.5 + 0.05, cy, z + h * 0.5), collection, parent)
        _mi6_glass_block(f"{name}_gW", (0.35, sy * 0.55, h * 0.72), (cx - sx * 0.5 - 0.05, cy, z + h * 0.5), collection, parent)


def build_mi6(origin: Vec3, collection: Optional[bpy.types.Collection] = None) -> bpy.types.Object:
    col = collection or ensure_collection("LF_MI6_Vauxhall")
    root = new_empty("LF_MI6_Root", origin, col)
    m = get_mats()
    ox, oy, oz = 0.0, 0.0, 0.0
    # Thames
    make_box("LF_MI6_River", (140.0, 50.0, 1.2), (ox, oy - 48.0, oz - 0.4), col, root, m.mi6_river)
    make_box("LF_MI6_Embankment", (90.0, 8.0, 3.2), (ox, oy - 22.0, oz + 1.4), col, root, m.mi6_embankment)
    for i in range(12):
        make_box(f"LF_MI6_Pile_{i}", (0.5, 0.5, 2.6), (ox - 40 + i * 7.4, oy - 26.5, oz + 0.4), col, root, m.iron)
    # podium
    make_box("LF_MI6_Podium", (72.0, 48.0, 4.5), (ox, oy, oz + 2.25), col, root, m.mi6_cream)
    # ziggurat layers (south is river)
    layers = [
        ("L1", 64.0, 40.0, 6.5, 0.0),
        ("L2", 54.0, 32.0, 6.0, 6.5),
        ("L3", 44.0, 26.0, 5.5, 12.5),
        ("L4", 34.0, 20.0, 5.2, 18.0),
        ("L5", 24.0, 15.0, 4.8, 23.2),
    ]
    z_off = oz + 4.5
    for name, sx, sy, h, z0 in layers:
        _mi6_step_layer(f"LF_MI6_{name}", ox, oy + 1.5, z_off + z0, sx, sy, h, col, root, glass_inset=True)
    # green glass wings — behind and to the sides of the cream mass
    _mi6_glass_block("LF_MI6_ShoulderE", (16.0, 28.0, 26.0), (ox + 34.0, oy + 8.0, oz + 4.5 + 13.0), col, root, 2.2)
    _mi6_glass_block("LF_MI6_ShoulderW", (16.0, 28.0, 26.0), (ox - 34.0, oy + 8.0, oz + 4.5 + 13.0), col, root, 2.2)
    _mi6_glass_block("LF_MI6_CrownE", (12.0, 20.0, 12.0), (ox + 28.0, oy + 10.0, oz + 4.5 + 28.0), col, root, 2.0)
    _mi6_glass_block("LF_MI6_CrownW", (12.0, 20.0, 12.0), (ox - 28.0, oy + 10.0, oz + 4.5 + 28.0), col, root, 2.0)
    # central aedicule / temple front on the river elevation
    make_box("LF_MI6_Aedicule", (10.0, 6.0, 9.0), (ox, oy - 16.0, oz + 4.5 + 10.5), col, root, m.mi6_cream)
    for i, x in enumerate((-3.2, -1.05, 1.05, 3.2)):
        column_doric(f"LF_MI6_AedCol_{i}", (ox + x, oy - 18.6, oz + 4.5 + 6.5), 7.2, 0.32, col, root, m.mi6_cream, m.mi6_cream_dark)
    make_box("LF_MI6_AedPed", (10.6, 2.2, 1.4), (ox, oy - 18.4, oz + 4.5 + 15.2), col, root, m.mi6_cream_dark)
    make_wedge("LF_MI6_AedGable", (10.6, 2.2, 2.4), (ox, oy - 18.4, oz + 4.5 + 16.1), col, root, m.mi6_cream)
    # cylindrical corner turrets (postmodern drums)
    for i, (x, y) in enumerate(((-22, -12), (22, -12), (-22, 14), (22, 14))):
        make_cylinder(f"LF_MI6_Drum_{i}", 4.2, 11.0, (ox + x, oy + y, oz + 4.5 + 5.5), 20, col, root, m.mi6_cream)
        make_cylinder(f"LF_MI6_DrumTop_{i}", 3.4, 4.0, (ox + x, oy + y, oz + 4.5 + 12.5), 16, col, root, m.mi6_cream_dark)
        _mi6_glass_block(f"LF_MI6_DrumG_{i}", (0.3, 3.2, 7.5), (ox + x, oy + y - 4.2, oz + 4.5 + 6.0), col, root, 1.8)
    # rooftop plant / penthouse cubes
    make_box("LF_MI6_PentL", (8.0, 6.0, 6.5), (ox - 6.0, oy + 2.0, oz + 4.5 + 32.5), col, root, m.mi6_cream)
    make_box("LF_MI6_PentR", (8.0, 6.0, 6.5), (ox + 6.0, oy + 2.0, oz + 4.5 + 32.5), col, root, m.mi6_cream)
    make_box("LF_MI6_Mast", (0.4, 0.4, 8.0), (ox + 4.0, oy + 4.0, oz + 4.5 + 40.0), col, root, m.iron)
    # red accent cylinder (the "periscope")
    make_cylinder("LF_MI6_Accent", 0.9, 6.5, (ox + 2.5, oy - 10.0, oz + 4.5 + 14.0), 12, col, root, m.mi6_accent)
    # river-front colonnade / service colonnade
    for i in range(7):
        x = ox - 18 + i * 6.0
        make_cylinder(f"LF_MI6_RiverCol_{i}", 0.55, 4.0, (x, oy - 22.5, oz + 4.5), 12, col, root, m.mi6_cream)
    make_box("LF_MI6_RiverBeam", (40.0, 1.4, 0.5), (ox, oy - 22.5, oz + 6.7), col, root, m.mi6_cream_dark)
    # plane trees along the embankment
    for i in range(7):
        simple_tree(f"LF_MI6_Tree_{i}", (ox - 24 + i * 8.0, oy - 20.0, oz + 3.2), col, root, height=RNG.uniform(7.5, 10.5), conifer=False)
    # Vauxhall-ish bridge stub
    make_box("LF_MI6_BridgeDeck", (8.0, 60.0, 1.2), (ox + 52.0, oy - 20.0, oz + 8.0), col, root, m.cobble)
    for i in range(4):
        make_box(f"LF_MI6_BridgePier_{i}", (2.4, 3.2, 8.0), (ox + 52.0, oy - 40 + i * 14.0, oz + 3.5), col, root, m.mi6_embankment)
    add_camera("LF_MI6_Cam", (origin[0] - 20.0, origin[1] - 80.0, origin[2] + 18.0), (origin[0], origin[1] + 2.0, origin[2] + 18.0), col, 35.0)
    print("LF: MI6 built")
    return root


# =============================================================================
# 10. SYDNEY OPERA HOUSE  — Jørn Utzon, spherical shells on Bennelong Point
# =============================================================================
#
# Two halls side by side on a granite podium, shells derived from the same
# sphere (the "spherical solution"), restaurant shells to the west,
# harbour water, monumental stair to the south.

def _syd_shell_sphere_section(
    name: str,
    radius: float,
    location: Vec3,
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
    mat: bpy.types.Material,
    yaw: float,
    pitch: float,
    scale: Vec3,
    segs: int = 18,
    rings: int = 10,
    start_v: float = 0.08,
    end_v: float = 0.52,
    start_u: float = 0.18,
    end_u: float = 0.82,
) -> bpy.types.Object:
    """
    Slice of a sphere: u in [start_u, end_u] around Z, v in [start_v, end_v]
    from the pole. Then rotate/scale into a sail.
    """
    verts: list[Vec3] = []
    nu = segs
    nv = rings
    for j in range(nv + 1):
        v = start_v + (end_v - start_v) * (j / nv)
        phi = v * math.pi  # 0 at +Z pole
        for i in range(nu + 1):
            u = start_u + (end_u - start_u) * (i / nu)
            th = u * math.tau
            x = radius * math.sin(phi) * math.cos(th)
            y = radius * math.sin(phi) * math.sin(th)
            z = radius * math.cos(phi)
            verts.append((x, y, z))
    faces: list[tuple] = []
    for j in range(nv):
        for i in range(nu):
            a = j * (nu + 1) + i
            b = a + 1
            c = a + nu + 2
            d = a + nu + 1
            faces.append((a, b, c, d))
    obj = mesh_from_pydata(name, verts, faces, collection, parent, mat, location, smooth=True)
    obj.scale = scale
    obj.rotation_euler = Euler((pitch, 0.0, yaw))
    # rib lines: duplicate a slightly smaller inner shell in rib material? skip — outer is enough
    return obj


def _syd_shell_pair(
    name: str,
    origin: Vec3,
    yaw: float,
    sizes: Sequence[Tuple[float, float, float, float]],
    collection: bpy.types.Collection,
    parent: bpy.types.Object,
) -> None:
    """
    A hall: sequence of nested sails pointing along yaw.
    sizes: list of (radius, scale_x, scale_y, scale_z)
    """
    m = get_mats()
    dx = math.cos(yaw)
    dy = math.sin(yaw)
    for i, (radius, sx, sy, sz) in enumerate(sizes):
        loc = (
            origin[0] + dx * i * 4.6,
            origin[1] + dy * i * 4.6,
            origin[2],
        )
        _syd_shell_sphere_section(
            f"{name}_{i}",
            radius,
            loc,
            collection,
            parent,
            m.syd_tile if i % 2 == 0 else m.syd_tile_gloss,
            yaw=yaw,
            pitch=math.radians(18.0 - i * 2.0),
            scale=(sx, sy, sz),
            segs=16,
            rings=9,
        )
        # glass wall under the mouth of the shell
        make_box(
            f"{name}_glass_{i}",
            (2.4 + i * 0.4, 0.18, 3.5 + i * 1.1),
            (
                origin[0] + dx * (i * 4.6 + 3.2),
                origin[1] + dy * (i * 4.6 + 3.2),
                origin[2] + 2.2 + i * 0.4,
            ),
            collection,
            parent,
            m.syd_glass,
            rotation=(0.0, 0.0, yaw),
        )


def build_sydney(origin: Vec3, collection: Optional[bpy.types.Collection] = None) -> bpy.types.Object:
    col = collection or ensure_collection("LF_Sydney_Opera")
    root = new_empty("LF_Sydney_Root", origin, col)
    m = get_mats()
    ox, oy, oz = 0.0, 0.0, 0.0
    # harbour
    make_box("LF_Syd_Harbour", (160.0, 140.0, 1.4), (ox, oy + 10.0, oz - 0.9), col, root, m.syd_harbour)
    # Bennelong peninsula
    make_box("LF_Syd_Point", (70.0, 95.0, 2.2), (ox, oy, oz + 0.9), col, root, m.syd_podium)
    # podium platform
    make_box("LF_Syd_Podium", (52.0, 78.0, 4.8), (ox, oy + 4.0, oz + 4.2), col, root, m.syd_podium)
    make_box("LF_Syd_PodiumTop", (50.0, 76.0, 0.4), (ox, oy + 4.0, oz + 6.8), col, root, m.syd_concrete)
    # monumental stair (south / forecourt)
    monumental_stair("LF_Syd_Stairs", (ox, oy - 48.0, oz + 1.2), 48.0, 18, 1.35, 0.28, col, root, m.syd_step, direction="-Y")
    make_box("LF_Syd_Forecourt", (70.0, 24.0, 0.4), (ox, oy - 62.0, oz + 1.3), col, root, m.syd_concrete)
    # Concert Hall (larger, west-ish / left when looking from stairs = -X slightly, actually looking north the concert hall is to the right / east)
    # From the south stairs, the larger concert hall is on the right (east) and the opera theatre on the left (west).
    concert_sizes = [
        (19.5, 0.88, 0.58, 1.18),
        (15.5, 0.80, 0.50, 1.06),
        (12.0, 0.70, 0.44, 0.94),
        (8.8, 0.54, 0.36, 0.74),
    ]
    opera_sizes = [
        (16.2, 0.82, 0.52, 1.08),
        (13.0, 0.74, 0.46, 0.94),
        (9.6, 0.60, 0.38, 0.78),
    ]
    rest_sizes = [
        (9.0, 0.62, 0.40, 0.72),
        (7.0, 0.50, 0.34, 0.55),
    ]
    hall_yaw = math.radians(90.0)  # shells open toward +Y (harbour)
    _syd_shell_pair("LF_Syd_Concert", (ox + 10.0, oy + 2.0, oz + 7.0), hall_yaw, concert_sizes, col, root)
    _syd_shell_pair("LF_Syd_Opera", (ox - 10.0, oy + 4.0, oz + 7.0), hall_yaw, opera_sizes, col, root)
    _syd_shell_pair("LF_Syd_Rest", (ox - 22.0, oy - 6.0, oz + 7.0), math.radians(210.0), rest_sizes, col, root)
    # hall bodies under the shells
    make_box("LF_Syd_ConcertHall", (16.0, 28.0, 8.0), (ox + 10.0, oy + 8.0, oz + 10.8), col, root, m.syd_concrete)
    make_box("LF_Syd_OperaHall", (13.0, 24.0, 7.0), (ox - 10.0, oy + 8.0, oz + 10.4), col, root, m.syd_concrete)
    make_box("LF_Syd_RestHall", (9.0, 12.0, 5.0), (ox - 22.0, oy - 4.0, oz + 9.4), col, root, m.syd_concrete)
    # glass foyer walls facing the stairs (south)
    make_box("LF_Syd_FoyerGlassC", (12.0, 0.25, 7.5), (ox + 10.0, oy - 6.5, oz + 10.6), col, root, m.syd_glass)
    make_box("LF_Syd_FoyerGlassO", (10.0, 0.25, 6.5), (ox - 10.0, oy - 5.0, oz + 10.2), col, root, m.syd_glass)
    # podium edge colonnade / glass lobby
    for i in range(9):
        x = ox - 20 + i * 5.0
        make_box(f"LF_Syd_Pilotis_{i}", (0.35, 0.35, 4.2), (x, oy - 32.0, oz + 4.0), col, root, m.syd_concrete)
    # rib indication on podium sides
    for i in range(14):
        y = oy - 30 + i * 5.0
        make_box(f"LF_Syd_PodiumRibE_{i}", (0.35, 0.8, 4.4), (ox + 26.0, y, oz + 4.2), col, root, m.syd_podium)
        make_box(f"LF_Syd_PodiumRibW_{i}", (0.35, 0.8, 4.4), (ox - 26.0, y, oz + 4.2), col, root, m.syd_podium)
    # harbour pylons / sea wall
    for i in range(10):
        make_box(f"LF_Syd_Seawall_{i}", (6.0, 1.6, 1.8), (ox - 30 + i * 7.0, oy + 48.0, oz + 1.4), col, root, m.syd_concrete)
    add_camera("LF_Syd_Cam", (origin[0] - 10.0, origin[1] - 95.0, origin[2] + 18.0), (origin[0], origin[1] + 10.0, origin[2] + 18.0), col, 28.0)
    print("LF: Sydney Opera House built")
    return root


# =============================================================================
# 11. MUSEUM PLAZA / LABELS / LIGHTING RIG
# =============================================================================

def make_text_label(
    name: str,
    body: str,
    location: Vec3,
    collection: bpy.types.Collection,
    parent: Optional[bpy.types.Object],
    size: float = 3.4,
) -> Optional[bpy.types.Object]:
    try:
        curve = bpy.data.curves.new(name, "FONT")
        curve.body = body
        curve.size = size
        curve.align_x = "CENTER"
        curve.align_y = "CENTER"
        obj = bpy.data.objects.new(name, curve)
        obj.location = location
        obj.rotation_euler = Euler((math.radians(90.0), 0.0, 0.0))
        obj["landmark_forge"] = True
        link_object(obj, collection)
        tag_parent(obj, parent)
        apply_material(obj, get_mats().label)
        return obj
    except Exception as exc:
        print("LF: text label skipped (%s)" % exc)
        return None


def build_museum_ground(collection: bpy.types.Collection) -> bpy.types.Object:
    m = get_mats()
    root = new_empty("LF_Museum_Root", (0.0, 0.0, 0.0), collection)
    span = CFG.spacing * 3.5 + 80.0
    make_box("LF_Museum_Floor", (span, 220.0, 1.2), (CFG.spacing * 1.5, 0.0, -0.6), collection, root, m.plaza)
    make_box("LF_Museum_Trim", (span + 6.0, 226.0, 0.4), (CFG.spacing * 1.5, 0.0, -1.4), collection, root, m.cobble)
    return root


def setup_global_lights(collection: bpy.types.Collection) -> None:
    if CFG.night:
        add_sun("LF_Moon", (90.0, -70.0, 110.0), (math.radians(58.0), 0.0, math.radians(30.0)), 1.6, (0.78, 0.84, 1.0), collection)
        add_sun("LF_WarmWash", (8.0, -78.0, 28.0), (math.radians(25.0), 0.0, math.radians(8.0)), 2.2, (1.0, 0.84, 0.62), collection)
        add_sun("LF_SkyFill", (-40.0, 60.0, 50.0), (math.radians(70.0), 0.0, math.radians(-25.0)), 0.45, (0.55, 0.68, 0.9), collection)
    else:
        add_sun("LF_Sun", (80.0, -60.0, 140.0), (math.radians(48.0), 0.0, math.radians(35.0)), 4.5, (1.0, 0.96, 0.88), collection)
        add_sun("LF_SkyFill", (-40.0, 80.0, 60.0), (math.radians(70.0), 0.0, math.radians(-20.0)), 1.1, (0.65, 0.78, 1.0), collection)


def setup_overview_camera(collection: bpy.types.Collection) -> bpy.types.Object:
    mid = CFG.spacing * 1.5
    cam = add_camera(
        "LF_Overview_Cam",
        (mid - 40.0, -210.0, 95.0),
        (mid, 10.0, 18.0),
        collection,
        lens=28.0,
    )
    bpy.context.scene.camera = cam
    return cam


def animate_turntable(camera: bpy.types.Object, frames: int = 240) -> None:
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = frames
    scene.render.fps = 24
    mid = Vector((CFG.spacing * 1.5, 10.0, 18.0))
    radius = 220.0
    height = 70.0
    for f in range(1, frames + 1):
        t = (f - 1) / frames * math.tau
        camera.location = (mid.x + math.cos(t) * radius, mid.y + math.sin(t) * radius, height)
        direction = mid - Vector(camera.location)
        camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        camera.keyframe_insert("location", frame=f)
        camera.keyframe_insert("rotation_euler", frame=f)


def export_glb(path: str) -> None:
    try:
        bpy.ops.export_scene.gltf(
            filepath=bpy.path.abspath(path),
            export_format="GLB",
            use_selection=False,
            export_apply=True,
        )
        print("LF: exported", path)
    except Exception as exc:
        print("LF: glb export failed:", exc)


# =============================================================================
# 12. ADDON-STYLE N-PANEL
# =============================================================================

class LF_Props(bpy.types.PropertyGroup):
    seed: bpy.props.IntProperty(name="Seed", default=42, min=0, max=999999)
    hogwarts: bpy.props.BoolProperty(name="Hogwarts", default=True)
    eth: bpy.props.BoolProperty(name="ETH Zürich", default=True)
    mi6: bpy.props.BoolProperty(name="MI6 London", default=True)
    sydney: bpy.props.BoolProperty(name="Sydney Opera", default=True)
    museum: bpy.props.BoolProperty(name="Museum layout", default=True)
    night: bpy.props.BoolProperty(name="Night lighting", default=True)
    density: bpy.props.EnumProperty(
        name="Windows",
        items=(
            ("low", "Low", "Fewer windows"),
            ("medium", "Medium", "Balanced"),
            ("high", "High", "Full façade detail"),
        ),
        default="high",
    )
    export: bpy.props.BoolProperty(name="Export GLB after build", default=False)


class LF_OT_Build(bpy.types.Operator):
    bl_idname = "landmark_forge.build"
    bl_label = "Build Landmarks"
    bl_description = "Generate the selected Landmark Forge models"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        p = context.scene.lf_props
        CFG.seed = p.seed
        CFG.build_hogwarts = p.hogwarts
        CFG.build_eth = p.eth
        CFG.build_mi6 = p.mi6
        CFG.build_sydney = p.sydney
        CFG.museum_layout = p.museum
        CFG.night = p.night
        CFG.window_density = p.density
        CFG.export_glb = p.export
        global RNG, MATS
        RNG = random.Random(CFG.seed)
        MATS = None
        run_forge()
        self.report({"INFO"}, "Landmark Forge finished")
        return {"FINISHED"}


class LF_OT_Clear(bpy.types.Operator):
    bl_idname = "landmark_forge.clear"
    bl_label = "Clear Landmark Forge"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        clear_generated()
        self.report({"INFO"}, "Landmark Forge objects removed")
        return {"FINISHED"}


class LF_PT_Panel(bpy.types.Panel):
    bl_label = "Landmark Forge"
    bl_idname = "VIEW3D_PT_landmark_forge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Landmark Forge"

    def draw(self, context):
        p = context.scene.lf_props
        layout = self.layout
        layout.label(text="Procedural landmarks  v%s" % SCRIPT_VERSION)
        col = layout.column(align=True)
        col.prop(p, "hogwarts")
        col.prop(p, "eth")
        col.prop(p, "mi6")
        col.prop(p, "sydney")
        layout.separator()
        layout.prop(p, "museum")
        layout.prop(p, "night")
        layout.prop(p, "density")
        layout.prop(p, "seed")
        layout.prop(p, "export")
        layout.separator()
        layout.operator("landmark_forge.build", icon="MESH_CUBE")
        layout.operator("landmark_forge.clear", icon="TRASH")


_CLASSES = (LF_Props, LF_OT_Build, LF_OT_Clear, LF_PT_Panel)


def register_addon() -> None:
    for cls in _CLASSES:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            bpy.utils.unregister_class(cls)
            bpy.utils.register_class(cls)
    if not hasattr(bpy.types.Scene, "lf_props"):
        bpy.types.Scene.lf_props = bpy.props.PointerProperty(type=LF_Props)


def unregister_addon() -> None:
    if hasattr(bpy.types.Scene, "lf_props"):
        del bpy.types.Scene.lf_props
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


# =============================================================================
# 13. MAIN
# =============================================================================

def parse_cli() -> None:
    argv = sys.argv
    if "--" in argv:
        args = argv[argv.index("--") + 1 :]
    else:
        args = []
    mapping = {
        "--no-hogwarts": ("build_hogwarts", False),
        "--no-eth": ("build_eth", False),
        "--no-mi6": ("build_mi6", False),
        "--no-sydney": ("build_sydney", False),
        "--day": ("night", False),
        "--night": ("night", True),
        "--export": ("export_glb", True),
        "--no-museum": ("museum_layout", False),
    }
    for a in args:
        if a in mapping:
            key, val = mapping[a]
            setattr(CFG, key, val)
        elif a.startswith("--seed="):
            CFG.seed = int(a.split("=", 1)[1])
        elif a.startswith("--spacing="):
            CFG.spacing = float(a.split("=", 1)[1])


def run_forge() -> None:
    global RNG, MATS
    RNG = random.Random(CFG.seed)
    MATS = None

    if not blender_version_ok():
        print("LF: warning — Blender %s is older than recommended %s" % (bpy.app.version, BLENDER_MIN))

    if CFG.clear_scene:
        clear_generated()

    get_mats()
    master = ensure_collection("LF_Landmarks")

    if CFG.add_world:
        setup_world(CFG.night)
    if CFG.add_lights:
        setup_global_lights(master)
    setup_render()

    if CFG.museum_layout:
        build_museum_ground(master)
        CFG.hogwarts_origin = (0.0, 0.0, 0.0)
        CFG.eth_origin = (CFG.spacing, 0.0, 0.0)
        CFG.mi6_origin = (CFG.spacing * 2.0, 0.0, 0.0)
        CFG.sydney_origin = (CFG.spacing * 3.0, 0.0, 0.0)

    roots = []
    if CFG.build_hogwarts:
        roots.append(build_hogwarts(CFG.hogwarts_origin, ensure_collection("LF_Hogwarts", master)))
        make_text_label("LF_Label_Hogwarts", "HOGWARTS  CASTLE", (CFG.hogwarts_origin[0], CFG.hogwarts_origin[1] - 85.0, 2.5), master, None)
    if CFG.build_eth:
        roots.append(build_eth(CFG.eth_origin, ensure_collection("LF_ETH_Zurich", master)))
        make_text_label("LF_Label_ETH", "ETH  ZUERICH", (CFG.eth_origin[0], CFG.eth_origin[1] - 85.0, 2.5), master, None)
    if CFG.build_mi6:
        roots.append(build_mi6(CFG.mi6_origin, ensure_collection("LF_MI6_Vauxhall", master)))
        make_text_label("LF_Label_MI6", "MI6  VAUXHALL  CROSS", (CFG.mi6_origin[0], CFG.mi6_origin[1] - 85.0, 2.5), master, None)
    if CFG.build_sydney:
        roots.append(build_sydney(CFG.sydney_origin, ensure_collection("LF_Sydney_Opera", master)))
        make_text_label("LF_Label_Sydney", "SYDNEY  OPERA  HOUSE", (CFG.sydney_origin[0], CFG.sydney_origin[1] - 85.0, 2.5), master, None)

    if CFG.add_cameras:
        cam = setup_overview_camera(master)
        try:
            animate_turntable(cam, 240)
        except Exception as exc:
            print("LF: turntable skipped:", exc)

    if CFG.export_glb:
        export_glb(CFG.export_dir + "landmark_forge.glb")

    print("LF: done — %d landmark root(s)" % len(roots))
    print("LF: collections created. Look in the Outliner for LF_Hogwarts / LF_ETH_Zurich / LF_MI6_Vauxhall / LF_Sydney_Opera")


def main() -> None:
    parse_cli()
    try:
        register_addon()
    except Exception as exc:
        print("LF: N-panel not registered:", exc)
    run_forge()


if __name__ == "__main__":
    main()


