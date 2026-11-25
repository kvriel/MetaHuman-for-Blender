"""Face board: one knob bone per GUI control, moved within its range to drive RigLogic.

Our own drawing (core/board_layout.py): a face with each knob over the part of the face it moves,
panels for the mouth shapes beside it, and a second head for the fine tweaker controls. A knob's
local X moves `.tx` controls and its local Y moves `.ty` controls; a Limit Location constraint keeps
it in range.
"""
from __future__ import annotations

from collections import OrderedDict

import bpy
import numpy as np
from mathutils import Vector

from ..core import board_layout
from .ctx import set_mode

PROP_BOARD = "mfb_faceboard"
PROP_GUI_MAP = "mfb_gui_map"     # list of [bone index, axis] per GUI control
PROP_UNIT = "mfb_board_unit"


def _split(gui_name: str) -> tuple[str, int]:
    """'CTRL_L_brow_down.ty' -> ('CTRL_L_brow_down', 1); tx -> 0, ty -> 1."""
    obj, _, attr = gui_name.rpartition(".")
    if not obj:
        return gui_name, 1
    return obj, 0 if attr.lower().endswith("x") else 1


def _circle_widget(name: str, radius: float = 1.0) -> bpy.types.Object:
    existing = bpy.data.objects.get(name)
    if existing:
        return existing
    angles = np.linspace(0, 2 * np.pi, 17)[:-1]
    verts = [(radius * np.cos(a), radius * np.sin(a), 0.0) for a in angles]
    edges = [(i, (i + 1) % len(verts)) for i in range(len(verts))]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, edges, [])
    return bpy.data.objects.new(name, me)


def _labels_mesh(name: str, labels) -> bpy.types.Mesh:
    """All board labels as one filled mesh: one object, so Blender draws one parent line, not one per label.

    labels: (text, position in board space, size, centred).
    """
    import bmesh
    from mathutils import Matrix
    bm = bmesh.new()
    for text, pos, size, centred in labels:
        cu = bpy.data.curves.new("_mfb_label", "FONT")
        cu.body = text
        cu.size = size
        cu.align_x = "CENTER" if centred else "LEFT"
        tmp = bpy.data.objects.new("_mfb_label", cu)
        try:
            me = bpy.data.meshes.new_from_object(tmp)
            me.transform(Matrix.Translation(pos) @ Matrix.Rotation(np.pi / 2, 4, "X"))
            bm.from_mesh(me)
            bpy.data.meshes.remove(me)
        finally:
            bpy.data.objects.remove(tmp)
            bpy.data.curves.remove(cu)
    out = bpy.data.meshes.new(name)
    bm.to_mesh(out)
    bm.free()
    mat = bpy.data.materials.get("MFB_board_labels") or bpy.data.materials.new("MFB_board_labels")
    mat.diffuse_color = (0.85, 0.85, 0.85, 1.0)
    out.materials.append(mat)
    return out


def build_faceboard(name: str, gui_names: list[str], gui_min: np.ndarray, gui_max: np.ndarray,
                    collection: bpy.types.Collection, origin: Vector, unit: float = 0.006) -> bpy.types.Object:
    """Create the board armature, a mesh with the knob range frames and the board drawing, and labels.

    unit: Blender distance for one control unit (knob travel from 0 to 1). The board's top left
    corner is at `origin`; it stands in the XZ plane, facing -Y like the character.
    """
    # group attributes by control object, keep DNA order
    controls: "OrderedDict[str, dict]" = OrderedDict()
    for i, g in enumerate(gui_names):
        obj, axis = _split(g)
        c = controls.setdefault(obj, {"axes": {}})
        c["axes"][axis] = (i, float(gui_min[i]), float(gui_max[i]))
    ranges = {obj: {ax: (lo, hi) for ax, (_, lo, hi) in c["axes"].items()} for obj, c in controls.items()}
    lay = board_layout.layout(ranges)
    bx0, _, _, by1 = board_layout.bounds(lay, ranges)

    def to_board(x: float, y: float) -> Vector:
        return Vector(((x - bx0) * unit, 0.0, (y - by1) * unit))

    placements = {obj: to_board(*lay.knobs[obj]) for obj in controls}

    arm_data = bpy.data.armatures.new(name)
    arm_data.display_type = "WIRE"
    arm_data.show_bone_colors = True
    board = bpy.data.objects.new(name, arm_data)
    board.location = origin
    collection.objects.link(board)
    board.show_in_front = True
    widget = _circle_widget("WGT_mfb_knob", 0.45)

    vl = bpy.context.view_layer
    prev = vl.objects.active
    vl.objects.active = board
    set_mode(board, "EDIT")
    order = []
    try:
        for obj, pos in placements.items():
            eb = arm_data.edit_bones.new(obj)
            eb.head = pos
            eb.tail = pos + Vector((0.0, 0.0, unit))   # bone Y = world +Z, bone X = world +X
            eb.roll = 0.0
            order.append(obj)
    finally:
        set_mode(board, "OBJECT")
        vl.objects.active = prev

    gui_map = [[-1, 1] for _ in gui_names]
    verts, edges = [], []

    def polyline(points):
        base = len(verts)
        verts.extend(points)
        edges.extend((base + k, base + k + 1) for k in range(len(points) - 1))

    for b_index, obj in enumerate(order):
        pb = board.pose.bones[obj]
        pb.custom_shape = widget
        pb.custom_shape_scale_xyz = (1.0, 1.0, 1.0)
        pb.color.palette = "THEME09"                     # yellow knobs
        pb.bone.color.palette = "THEME09"
        pb.lock_location = (0 not in controls[obj]["axes"], 1 not in controls[obj]["axes"], True)
        pb.lock_rotation = (True, True, True); pb.lock_rotation_w = True
        pb.lock_scale = (True, True, True)
        lim = pb.constraints.new("LIMIT_LOCATION")
        lim.owner_space = "LOCAL"
        lim.use_transform_limit = True
        lo = [0.0, 0.0]; hi = [0.0, 0.0]
        for axis, (gi, gmin, gmax) in controls[obj]["axes"].items():
            gui_map[gi] = [b_index, axis]
            lo[axis], hi[axis] = gmin * unit, gmax * unit
        lim.use_min_x = lim.use_max_x = lim.use_min_y = lim.use_max_y = lim.use_min_z = lim.use_max_z = True
        lim.min_x, lim.max_x = lo[0], hi[0]
        lim.min_y, lim.max_y = lo[1], hi[1]
        lim.min_z = lim.max_z = 0.0

    # range frames (board space: bone X -> world X, bone Y -> world Z) and the drawing
    for obj, (x0, x1, y0, y1) in board_layout.frames(lay, ranges).items():
        polyline([to_board(x0, y0), to_board(x1, y0), to_board(x1, y1), to_board(x0, y1), to_board(x0, y0)])
    for line in lay.lines:
        polyline([to_board(x, y) for x, y in line])
    frame_me = bpy.data.meshes.new(f"{name}_frames")
    frame_me.from_pydata([tuple(v) for v in verts], edges, [])
    frames = bpy.data.objects.new(f"{name}_frames", frame_me)
    frames.parent = board
    frames.hide_select = True
    frames.display_type = "WIRE"
    collection.objects.link(frames)

    labels = _labels_mesh(f"{name}_labels", [(text, to_board(x, y - size * 0.7), size * unit, centred)
                                             for text, x, y, size, centred in lay.labels])
    lab = bpy.data.objects.new(f"{name}_labels", labels)
    lab.parent = board
    lab.hide_select = True
    collection.objects.link(lab)

    board[PROP_GUI_MAP] = [v for pair in gui_map for v in pair]
    board[PROP_UNIT] = unit
    return board


def read_gui(board: bpy.types.Object) -> np.ndarray:
    flat = np.asarray(board[PROP_GUI_MAP], dtype=np.int64).reshape(-1, 2)
    unit = float(board[PROP_UNIT])
    locs = np.zeros(len(board.pose.bones) * 3, np.float32)
    board.pose.bones.foreach_get("location", locs)
    locs = locs.reshape(-1, 3)
    gui = np.zeros(len(flat), np.float32)
    ok = flat[:, 0] >= 0
    gui[ok] = locs[flat[ok, 0], flat[ok, 1]] / unit
    return gui


def write_gui(board: bpy.types.Object, gui: np.ndarray) -> None:
    flat = np.asarray(board[PROP_GUI_MAP], dtype=np.int64).reshape(-1, 2)
    unit = float(board[PROP_UNIT])
    locs = np.zeros(len(board.pose.bones) * 3, np.float32)
    board.pose.bones.foreach_get("location", locs)
    locs = locs.reshape(-1, 3)
    ok = flat[:, 0] >= 0
    locs[flat[ok, 0], flat[ok, 1]] = np.asarray(gui, np.float32)[ok] * unit
    board.pose.bones.foreach_set("location", locs.ravel())
