"""Live RigLogic in Blender.

Each update: read the face board (or the raw control properties), blend in eye aim,
evaluate RigLogic, then write bones, shape keys, face-root attributes and wrinkle maps.
"""
from __future__ import annotations

import math

import bpy
import numpy as np
from mathutils import Matrix, Vector

from ..core.riglogic import RigLogic
from ..core.skeleton import euler_to_matrix
from ..core.space import Space
from ..dna.reader import read_dna
from . import faceboard

PROP_RIG = "mfb_rig"                # armature: True when RigLogic drives it
PROP_BOARD_NAME = "mfb_board"       # armature -> face board object name
PROP_AIM_NAME = "mfb_eye_aim"       # armature -> eye aim control (empty) name
PROP_LOD = "mfb_rig_lod"
PROP_DRIVE = "mfb_drive"            # armature: "BOARD" or "RAW"
PROP_MAPS = "mfb_wrinkle_maps"      # armature: dict of wrinkle map values (for display)
PROP_BODY_DNA = "mfb_body_dna"      # armature: body DNA whose RigLogic (correctives, twist) runs live
PROP_BODY_RIG = "mfb_body_rig"      # armature: True to run the body RigLogic
PROP_MUTED = "mfb_muted_solvers"    # armature: list of muted RBF solver names
PROP_AIM_SWITCH = "look_at"         # aim control: 0 = board drives eyes, 1 = aim target drives eyes
PROP_FOLLOW = "follow_head"         # board / aim control: 0 = stay in world, 1 = follow head
FACE_ROOT = "FACIAL_C_FacialRoot"
EYE_BONES = ("FACIAL_L_Eye", "FACIAL_R_Eye")
EYE_GUI = (("CTRL_L_eye.tx", "CTRL_L_eye.ty"), ("CTRL_R_eye.tx", "CTRL_R_eye.ty"))
EYE_YAW, EYE_PITCH = 40.0, 30.0     # degrees for a full (1.0) eye control


def head_dna_path(arm) -> str:
    return arm.get("mfb_head_dna") or arm["mfb_dna_path"]


def raw_attr_name(raw_name: str) -> str:
    """'CTRL_expressions.browDownL' -> 'browDownL' (the name Unreal reads as a curve).

    Other raw controls (joint quaternions such as 'neck_01.qx') keep their joint name: 'neck_01_qx'.
    """
    obj, _, attr = raw_name.partition(".")
    if not attr:
        return raw_name
    return attr if obj == "CTRL_expressions" else f"{obj}_{attr}"


def map_attr_name(map_name: str) -> str:
    return map_name.replace(".", "_")


class _Runtime:
    """Cached per-armature data so each update is numpy plus a few foreach_set calls."""

    def __init__(self, arm: bpy.types.Object, kind: str = "head"):
        self.kind = kind
        dna = read_dna(head_dna_path(arm) if kind == "head" else arm[PROP_BODY_DNA], geometry=False)
        self.dna = dna
        self.lod = int(arm.get(PROP_LOD, 0))
        self.rig = RigLogic(dna, self.lod)
        self.space = Space.for_dna(dna)
        d = dna.definition
        names = d.joint_names
        bone_index = {pb.name: i for i, pb in enumerate(arm.pose.bones)}
        self.bone_count = len(arm.pose.bones)
        rows = [(j, bone_index[n]) for j, n in enumerate(names) if n in bone_index]
        self.joint_idx = np.array([r[0] for r in rows], np.intp)
        self.bone_idx = np.array([r[1] for r in rows], np.intp)
        rest_r = euler_to_matrix(d.neutral_rotations, degrees=dna.descriptor.rotation_unit == "degrees")
        self.rest_rt = np.transpose(rest_r[self.joint_idx], (0, 2, 1)).astype(np.float32)
        self.to_radians = np.float32(np.pi / 180 if self.rig.degrees else 1.0)
        driven = self.rig.driven_joints()
        name_index = {n: j for j, n in enumerate(names)}
        for joint, _ in self.rig.quat_inputs:           # never overwrite a driver joint
            if joint in name_index:
                driven[name_index[joint]] = False
        self.driven = driven[self.joint_idx]
        # bones whose rotation feeds RigLogic as a quaternion (RBF and twist/swing drivers)
        self.quat_bones = [bone_index.get(joint, -1) for joint, _ in self.rig.quat_inputs]
        self.solver_names = [s.name for s in self.rig.rbf.solvers]
        self.channel_names = d.blend_shape_channel_names
        self.raw_attrs = [raw_attr_name(n) for n in d.raw_control_names]
        self.map_attrs = [map_attr_name(n) for n in d.animated_map_names]
        self.map_short = [n.split(".")[-1] for n in d.animated_map_names]
        gui = {n: i for i, n in enumerate(d.gui_control_names)}
        self.eye_gui = [(gui.get(x, -1), gui.get(y, -1)) for x, y in EYE_GUI]
        self.last_key: bytes | None = None
        self.mesh_keys: dict[str, np.ndarray] = {}

    def key_map(self, mesh_obj) -> np.ndarray:
        cached = self.mesh_keys.get(mesh_obj.name)
        kb = mesh_obj.data.shape_keys.key_blocks
        if cached is None or len(cached) != len(kb):
            # keys record their DNA channel at build time (names may be cut to 63 bytes);
            # keys made by hand fall back to a name match
            recorded = dict(mesh_obj.get("mfb_shape_channels", {}))
            lookup = {n: i for i, n in enumerate(self.channel_names)}
            cached = np.array([recorded.get(k.name, lookup.get(k.name, -1)) for k in kb], np.intp)
            self.mesh_keys[mesh_obj.name] = cached
        return cached


_runtimes: dict[str, _Runtime] = {}
_busy = False


def runtime(arm, kind: str = "head") -> _Runtime:
    key = f"{arm.name}|{kind}"
    rt = _runtimes.get(key)
    if rt is None or rt.bone_count != len(arm.pose.bones) or rt.lod != int(arm.get(PROP_LOD, 0)):
        rt = _runtimes[key] = _Runtime(arm, kind)
    muted = set(arm.get(PROP_MUTED, []))
    rt.rig.muted_solvers = {i for i, n in enumerate(rt.solver_names) if n in muted}
    return rt


def read_quats(arm, rt: _Runtime) -> np.ndarray:
    """Driver joint rotations relative to their neutral (rest) frame, as (x, y, z, w)."""
    out = np.tile(np.float32([0, 0, 0, 1]), (len(rt.quat_bones), 1))
    pbs = arm.pose.bones
    for i, b in enumerate(rt.quat_bones):
        if b >= 0:
            q = pbs[b].matrix_basis.to_quaternion()
            out[i] = (q.x, q.y, q.z, q.w)
    return out


def clear_cache() -> None:
    _runtimes.clear()


# --------------------------------------------------------------------------- inputs


def _face_root(arm):
    return arm.pose.bones.get(FACE_ROOT)


def read_raw(arm, rt: _Runtime) -> np.ndarray:
    root = _face_root(arm)
    raw = np.zeros(rt.rig.raw_count, np.float32)
    if root is not None:
        for i, attr in enumerate(rt.raw_attrs):
            raw[i] = float(root.get(attr, 0.0))
    return raw


def _eye_aim_gui(arm, rt: _Runtime, gui: np.ndarray) -> np.ndarray:
    """Blend the eye controls toward the eye aim target, by the aim control's look_at switch."""
    aim = bpy.data.objects.get(arm.get(PROP_AIM_NAME, ""))
    if aim is None:
        return gui
    weight = float(aim.get(PROP_AIM_SWITCH, 0.0))
    if weight <= 0.0:
        return gui
    root_pb = _face_root(arm) or arm.pose.bones.get("head")
    if root_pb is None:
        return gui
    root_world = arm.matrix_world @ root_pb.matrix
    rest_root = root_pb.bone.matrix_local
    to_root = root_world.inverted()
    targets = [c for c in aim.children if c.name.startswith(("CTRL_L_eyeAim", "CTRL_R_eyeAim"))]
    by_side = {("L" if "_L_" in c.name else "R"): c for c in targets}
    out = gui.copy()
    for side, bone_name, (gx, gy) in zip(("L", "R"), EYE_BONES, rt.eye_gui):
        eye = arm.data.bones.get(bone_name)
        target = by_side.get(side, aim)
        if eye is None or gx < 0 or gy < 0:
            continue
        eye_local = rest_root.inverted() @ eye.head_local
        tgt_local = to_root @ target.matrix_world.translation
        d = rest_root.to_3x3() @ (tgt_local - eye_local)   # back in character axes at rest
        left, up, front = d.x, d.z, -d.y
        yaw = math.degrees(math.atan2(left, max(front, 1e-6)))
        pitch = math.degrees(math.atan2(up, math.hypot(left, front)))
        out[gx] = (1 - weight) * gui[gx] + weight * float(np.clip(yaw / EYE_YAW, -1, 1))
        out[gy] = (1 - weight) * gui[gy] + weight * float(np.clip(pitch / EYE_PITCH, -1, 1))
    return out


# --------------------------------------------------------------------------- outputs


def _write_outputs(arm, rt: _Runtime, res, write_raw: bool) -> None:
    n = rt.bone_count
    loc = np.zeros(n * 3, np.float32); rot = np.zeros(n * 3, np.float32); scl = np.zeros(n * 3, np.float32)
    pbs = arm.pose.bones
    pbs.foreach_get("location", loc); pbs.foreach_get("rotation_euler", rot); pbs.foreach_get("scale", scl)
    loc = loc.reshape(n, 3); rot = rot.reshape(n, 3); scl = scl.reshape(n, 3)
    dl = res.joint_deltas[rt.joint_idx]
    b, drv = rt.bone_idx[rt.driven], rt.driven
    loc[b] = np.einsum("nij,nj->ni", rt.rest_rt[drv], dl[drv, 0:3] * np.float32(rt.space.s))
    rot[b] = dl[drv, 3:6] * rt.to_radians
    scl[b] = 1.0 + dl[drv, 6:9]
    pbs.foreach_set("location", loc.ravel()); pbs.foreach_set("rotation_euler", rot.ravel())
    pbs.foreach_set("scale", scl.ravel())
    if rt.kind == "body":
        arm.update_tag()
        return

    maps = {short: float(v) for short, v in zip(rt.map_short, res.animated_maps)}
    for obj in arm.children:
        if obj.type != "MESH" or obj.get("mfb_role") == "body":
            continue
        if obj.data.shape_keys:
            kmap = rt.key_map(obj)
            values = np.zeros(len(kmap), np.float32)
            ok = kmap >= 0
            values[ok] = res.blend_shapes[kmap[ok]]
            obj.data.shape_keys.key_blocks.foreach_set("value", values)
            obj.data.update_tag()
        if obj.get("mfb_wrinkles"):          # materials read wrinkle weights from the mesh object
            for attr, v in zip(rt.map_attrs, res.animated_maps):
                obj[f"wm_{attr}"] = float(v)
            obj.update_tag()

    root = _face_root(arm)
    if root is not None:
        if write_raw:
            for attr, v in zip(rt.raw_attrs, res.controls[:rt.rig.raw_count]):
                root[attr] = float(v)
        for attr, v in zip(rt.map_attrs, res.animated_maps):
            root[attr] = float(v)
    arm[PROP_MAPS] = maps
    arm.update_tag()


def _mute_key(rt: _Runtime) -> bytes:
    return ",".join(str(i) for i in sorted(rt.rig.muted_solvers)).encode()


PROP_EDITING = "mfb_pose_editing"   # armature: "body"/"head" while the Pose Editor holds that rig still


def update_body(arm: bpy.types.Object, force: bool = False) -> bool:
    """Body RigLogic: driver joint rotations -> RBF correctives and twist/swing joints."""
    if not (arm.get(PROP_BODY_RIG) and arm.get(PROP_BODY_DNA)) or arm.get(PROP_EDITING) == "body":
        return False
    rt = runtime(arm, "body")
    quats = read_quats(arm, rt)
    key = b"B" + quats.tobytes() + _mute_key(rt)
    if not force and key == rt.last_key:
        return False
    rt.last_key = key
    _write_outputs(arm, rt, rt.rig.evaluate_raw(np.zeros(rt.rig.raw_count, np.float32), quats), write_raw=False)
    return True


def update_face(arm: bpy.types.Object, force: bool = False) -> bool:
    if not arm.get("mfb_head_dna") or arm.get(PROP_EDITING) == "head":
        return False
    rt = runtime(arm, "head")
    quats = read_quats(arm, rt)
    mode = arm.get(PROP_DRIVE, "BOARD")
    if mode == "RAW":
        raw = read_raw(arm, rt)
        key = b"R" + raw.tobytes() + quats.tobytes() + _mute_key(rt)
        if not force and key == rt.last_key:
            return False
        rt.last_key = key
        _write_outputs(arm, rt, rt.rig.evaluate_raw(raw, quats), write_raw=False)
        return True
    board = bpy.data.objects.get(arm.get(PROP_BOARD_NAME, ""))
    if board is None:
        return False
    gui = _eye_aim_gui(arm, rt, faceboard.read_gui(board))
    key = b"G" + gui.tobytes() + quats.tobytes() + _mute_key(rt)
    if not force and key == rt.last_key:
        return False
    rt.last_key = key
    _write_outputs(arm, rt, rt.rig.evaluate_gui(gui, quats), write_raw=True)
    return True


def update_rig(arm: bpy.types.Object, force: bool = False) -> bool:
    body = update_body(arm, force)
    face = update_face(arm, force)
    return body or face


def apply_gui(arm, gui: np.ndarray) -> None:
    """Evaluate a given GUI vector (used by tests and tools)."""
    rt = runtime(arm)
    _write_outputs(arm, rt, rt.rig.evaluate_gui(np.asarray(gui, np.float32), read_quats(arm, rt)), write_raw=True)


def setup_body_rig(arm: bpy.types.Object, body_dna_path: str, lod: int = 0) -> None:
    """Run the body DNA's RigLogic live: RBF correctives and twist/swing joints follow the body pose."""
    arm[PROP_BODY_DNA] = body_dna_path
    arm[PROP_BODY_RIG] = True
    if PROP_LOD not in arm:
        arm[PROP_LOD] = lod
    arm[PROP_RIG] = True
    rt = runtime(arm, "body")
    driven_names = {rt.dna.definition.joint_names[j] for j in np.nonzero(rt.rig.driven_joints())[0]}
    for pb in arm.pose.bones:
        if pb.name in driven_names:
            pb.rotation_mode = "XYZ"
    update_body(arm, force=True)


@bpy.app.handlers.persistent
def _on_update(*_args) -> None:
    global _busy
    if _busy:
        return
    _busy = True
    try:
        for arm in bpy.data.objects:
            if arm.type == "ARMATURE" and arm.get(PROP_RIG):
                try:
                    update_rig(arm)
                except Exception as exc:  # keep Blender responsive if one rig fails
                    print(f"[mfb] RigLogic update failed for {arm.name}: {exc}")
    finally:
        _busy = False


# --------------------------------------------------------------------------- setup


def _add_switch(obj, prop: str, value: float, description: str) -> None:
    obj[prop] = value
    ui = obj.id_properties_ui(prop)
    ui.update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, description=description)


def _follow_head(obj, arm, bone_name: str) -> None:
    """Child Of the head bone, with influence driven by the object's follow_head switch."""
    _add_switch(obj, PROP_FOLLOW, 1.0, "1: follow the head bone. 0: stay where it is in the world")
    con = obj.constraints.new("CHILD_OF")
    con.name = "mfb_follow_head"
    con.target = arm
    con.subtarget = bone_name
    pb = arm.pose.bones[bone_name]
    con.inverse_matrix = (arm.matrix_world @ pb.bone.matrix_local).inverted()
    fc = con.driver_add("influence")
    drv = fc.driver
    drv.type = "AVERAGE"
    var = drv.variables.new()
    var.name = "follow"
    var.targets[0].id = obj
    var.targets[0].data_path = f'["{PROP_FOLLOW}"]'


def build_eye_aim(arm, collection, eye_distance: float = 0.35) -> bpy.types.Object | None:
    """Eye aim controls: a centre control in front of the face with one target per eye."""
    eyes = [arm.data.bones.get(n) for n in EYE_BONES]
    if not all(eyes):
        return None
    mw = arm.matrix_world
    left, right = mw @ eyes[0].head_local, mw @ eyes[1].head_local
    centre = (left + right) / 2 + Vector((0.0, -eye_distance, 0.0))
    c = bpy.data.objects.new("CTRL_C_eyesAim", None)
    c.empty_display_type = "CIRCLE"
    c.empty_display_size = 0.04
    c.rotation_euler = (math.pi / 2, 0, 0)
    c.location = centre
    collection.objects.link(c)
    _add_switch(c, PROP_AIM_SWITCH, 0.0, "1: the eyes look at the aim targets. 0: the face board drives the eyes")
    for side, eye_pos in (("L", left), ("R", right)):
        t = bpy.data.objects.new(f"CTRL_{side}_eyeAim", None)
        t.empty_display_type = "SPHERE"
        t.empty_display_size = 0.012
        collection.objects.link(t)
        t.parent = c
        # c is turned 90° about X, so its local X is still world X
        t.location = Vector((eye_pos.x - centre.x, 0.0, 0.0))
    c.lock_rotation = (True, True, True)
    return c


def setup_rig(arm: bpy.types.Object, collection: bpy.types.Collection, lod: int = 0) -> bpy.types.Object:
    """Create the face board, eye aim controls and face-root attributes; switch on live RigLogic."""
    dna = read_dna(head_dna_path(arm), geometry=False)
    rl = RigLogic(dna, lod)
    for pb in arm.pose.bones:
        pb.rotation_mode = "XYZ"
    root_bone = arm.data.bones.get(FACE_ROOT) or arm.data.bones[0]
    origin = arm.matrix_world @ root_bone.head_local
    origin.x += 0.25
    origin.z += 0.12
    board = faceboard.build_faceboard(f"{arm.name}_faceboard", dna.definition.gui_control_names,
                                      rl.gui_min, rl.gui_max, collection, origin)
    arm[PROP_BOARD_NAME] = board.name
    arm[PROP_LOD] = lod
    arm[PROP_DRIVE] = "BOARD"

    # face-root attributes Unreal reads as animation curves, keyed at frame 0
    root = _face_root(arm)
    if root is not None:
        for name in dna.definition.raw_control_names:
            root[raw_attr_name(name)] = 0.0
        for name in dna.definition.animated_map_names:
            root[map_attr_name(name)] = 0.0

    aim = build_eye_aim(arm, collection)
    if aim is not None:
        arm[PROP_AIM_NAME] = aim.name
    follow_bone = "head" if "head" in arm.pose.bones else (FACE_ROOT if root is not None else None)
    if follow_bone:
        bpy.context.view_layer.update()
        _follow_head(board, arm, follow_bone)
        if aim is not None:
            _follow_head(aim, arm, follow_bone)

    arm[PROP_RIG] = True
    _runtimes.pop(arm.name, None)
    update_rig(arm, force=True)
    key_face_root(arm, frame=0)
    return board


# --------------------------------------------------------------------------- keys


def key_face_root(arm, frame: int | None = None) -> int:
    """Key every raw control and wrinkle map attribute on the face root."""
    root = _face_root(arm)
    if root is None:
        return 0
    frame = bpy.context.scene.frame_current if frame is None else frame
    count = 0
    for key in list(root.keys()):
        if isinstance(root[key], float):
            root.keyframe_insert(f'["{key}"]', frame=frame, group=FACE_ROOT)
            count += 1
    return count


def key_face_board(arm, frame: int | None = None) -> int:
    """Key every face board knob (and the eye aim controls) at a frame."""
    frame = bpy.context.scene.frame_current if frame is None else frame
    count = 0
    board = bpy.data.objects.get(arm.get(PROP_BOARD_NAME, ""))
    if board is not None:
        for pb in board.pose.bones:
            locked = tuple(pb.lock_location)
            for axis in range(3):
                if not locked[axis]:
                    pb.keyframe_insert("location", index=axis, frame=frame, group=pb.name)
                    count += 1
    aim = bpy.data.objects.get(arm.get(PROP_AIM_NAME, ""))
    if aim is not None:
        aim.keyframe_insert(f'["{PROP_AIM_SWITCH}"]', frame=frame)
        for obj in (aim, *aim.children):
            obj.keyframe_insert("location", frame=frame)
            count += 1
    return count


@bpy.app.handlers.persistent
def _on_load(*_args) -> None:
    """A newly opened file has new objects: drop cached rigs, then evaluate once."""
    clear_cache()
    _on_update()


def register() -> None:
    for handlers in (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.frame_change_post):
        if _on_update not in handlers:
            handlers.append(_on_update)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)


def unregister() -> None:
    for handlers in (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.frame_change_post):
        if _on_update in handlers:
            handlers.remove(_on_update)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    clear_cache()
