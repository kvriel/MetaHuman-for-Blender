"""Pose Editor: edit RBF corrective poses and twist/swing joints, validate, export DNA.

Edits go to an in-memory DNA and a working-copy file the live rig reads, so they
show at once and survive saving the .blend. Export writes a final DNA.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import bpy
import numpy as np
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)
from mathutils import Quaternion

from ..core import dna_edit as E
from ..core import rbf as R
from ..core.skeleton import euler_to_matrix
from ..core.space import Space
from ..dna.reader import read_dna
from ..dna.writer import write_dna
from . import rig

KIND_PROP = {"body": rig.PROP_BODY_DNA, "head": "mfb_head_dna"}
ORIGINAL_PROP = "mfb_{kind}_dna_original"
_dna_cache: dict[tuple[str, str], object] = {}
_clipboard: dict = {}


# --------------------------------------------------------------------------- state


def _state(context):
    return context.scene.mfb_pose


def _arm(context):
    st = _state(context)
    return st.armature if st.armature and st.armature.type == "ARMATURE" else None


def working_dna(arm, kind):
    key = (arm.name, kind)
    if key not in _dna_cache:
        _dna_cache[key] = read_dna(arm[KIND_PROP[kind]])
        E.ensure_rbf_layers(_dna_cache[key])
    return _dna_cache[key]


def log(context, level: str, text: str) -> None:
    st = _state(context)
    item = st.log.add()
    item.level, item.text, item.time = level, text, time.strftime("%H:%M:%S")
    st.log_index = len(st.log) - 1


def commit(context, message: str) -> None:
    """Write the working copy, refresh the live rig and the lists."""
    st = _state(context)
    arm = _arm(context)
    dna = working_dna(arm, st.kind)
    prop = KIND_PROP[st.kind]
    original_key = ORIGINAL_PROP.format(kind=st.kind)
    if original_key not in arm:
        arm[original_key] = arm[prop]
    path = Path(arm[prop])
    if not path.stem.endswith("_edit"):
        folder = Path(bpy.path.abspath("//")) if bpy.data.filepath else Path(bpy.app.tempdir)
        path = folder / f"{Path(arm[original_key]).stem}_edit.dna"
    write_dna(dna, path)
    arm[prop] = str(path)
    rig._runtimes.pop(f"{arm.name}|{st.kind}", None)
    refresh_lists(context)
    log(context, "INFO", message)


_refreshing = False


def refresh_lists(context) -> None:
    """Rebuild the lists from the working DNA (setting the indexes here must not re-enter)."""
    global _refreshing
    if _refreshing:
        return
    _refreshing = True
    try:
        _refresh_lists(context)
    finally:
        _refreshing = False


def _refresh_lists(context) -> None:
    st = _state(context)
    arm = _arm(context)
    st.solvers.clear(); st.poses.clear(); st.driven.clear(); st.drivers.clear(); st.twists.clear()
    if arm is None or not arm.get(KIND_PROP[st.kind]):
        return
    dna = working_dna(arm, st.kind)
    muted = set(arm.get(rig.PROP_MUTED, []))
    for s in dna.rbf.solvers:
        item = st.solvers.add(); item.name = s.name; item.muted = s.name in muted
    st.solver_index = min(st.solver_index, max(len(st.solvers) - 1, 0))
    if st.solvers:
        si = st.solver_index
        s = dna.rbf.solvers[si]
        for p in s.pose_indices:
            item = st.poses.add(); item.name = dna.rbf.poses[int(p)].name; item.scale = dna.rbf.poses[int(p)].scale
            item.index = int(p)
        st.pose_index = min(st.pose_index, max(len(st.poses) - 1, 0))
        for j in E.solver_drivers(dna, si):
            st.drivers.add().name = j
        control = E.pose_control(dna, st.poses[st.pose_index].index, create=False) if st.poses else -1
        if control >= 0:
            col = E.joint_column(dna, control)
            for j in sorted(col, key=lambda j: dna.definition.joint_names[j]):
                st.driven.add().name = dna.definition.joint_names[j]
        _load_settings(st, s)
    names = dna.definition.joint_names
    for kind, items in (("Twist", dna.twist_swing.twists), ("Swing", dna.twist_swing.swings)):
        for t in items:
            item = st.twists.add()
            item.name = f"{kind}: {dna.definition.raw_control_names[int(t.input_control_indices[0])].rpartition('.')[0]}" \
                        f" -> {', '.join(names[int(j)] for j in t.output_joint_indices)}"


def _load_settings(st, s) -> None:
    st.solver_type = str(s.solver_type); st.distance_method = str(s.distance_method)
    st.function_type = str(s.function_type); st.normalize_method = str(s.normalize_method)
    st.twist_axis = str(s.twist_axis); st.radius = s.radius; st.automatic_radius = s.automatic_radius == 0
    st.weight_threshold = s.weight_threshold


# --------------------------------------------------------------------------- bone <-> DNA conversion


def _joint_frames(arm, kind):
    rt = rig.runtime(arm, kind)
    names = rt.dna.definition.joint_names
    frames = {}
    for j, b, rt_t in zip(rt.joint_idx, rt.bone_idx, rt.rest_rt):
        frames[names[int(j)]] = (int(j), int(b), rt_t.T)          # rest rotation (not transposed)
    return rt, frames


def bone_delta(arm, kind, joint_name) -> np.ndarray:
    """A pose bone's current transform as a DNA joint delta (cm, degrees, scale-1)."""
    rt, frames = _joint_frames(arm, kind)
    _, b, rest_r = frames[joint_name]
    pb = arm.pose.bones[b]
    d = np.zeros(9, np.float32)
    d[0:3] = rest_r @ np.asarray(pb.location) / rt.space.s
    q = pb.matrix_basis.to_quaternion()
    d[3:6] = R.quat_to_euler_xyz(np.array([q.x, q.y, q.z, q.w]), rt.rig.degrees)
    d[6:9] = np.asarray(pb.scale) - 1.0
    return d


def set_drivers_to_target(arm, dna, si, target) -> None:
    for qi, joint in enumerate(E.solver_drivers(dna, si)):
        x, y, z, w = target[qi * 4:qi * 4 + 4]
        pb = arm.pose.bones.get(joint)
        if pb:
            pb.rotation_mode = "QUATERNION"
            pb.rotation_quaternion = Quaternion((w, x, y, z))


def driver_target_now(arm, dna, si) -> np.ndarray:
    out = []
    for joint in E.solver_drivers(dna, si):
        pb = arm.pose.bones.get(joint)
        q = pb.matrix_basis.to_quaternion() if pb else Quaternion()
        out += [q.x, q.y, q.z, q.w]
    return np.array(out, np.float32)


def bone_selected(pb) -> bool:
    """Blender 5 keeps selection on the pose bone, 4.x on the bone."""
    return pb.select if hasattr(pb, "select") else pb.bone.select


def _selected_bone_names(context, arm) -> list[str]:
    return [pb.name for pb in arm.pose.bones if bone_selected(pb)]


# --------------------------------------------------------------------------- property groups


class MFB_PoseSolverItem(bpy.types.PropertyGroup):
    name: StringProperty()
    muted: BoolProperty()


class MFB_PoseItem(bpy.types.PropertyGroup):
    name: StringProperty()
    scale: FloatProperty(name="Multiplier", default=1.0)
    index: IntProperty()


class MFB_NameItem(bpy.types.PropertyGroup):
    name: StringProperty()


class MFB_LogItem(bpy.types.PropertyGroup):
    level: StringProperty()
    text: StringProperty()
    time: StringProperty()


def _upd_lists(self, context):
    refresh_lists(context)


class MFB_PoseState(bpy.types.PropertyGroup):
    armature: PointerProperty(type=bpy.types.Object, name="Armature", poll=lambda s, o: o.type == "ARMATURE",
                              update=_upd_lists)
    kind: EnumProperty(name="Rig", items=(("body", "Body", "Body correctives (body DNA)"),
                                          ("head", "Face / Neck", "Neck correctives (head DNA)")), update=_upd_lists)
    solvers: CollectionProperty(type=MFB_PoseSolverItem)
    solver_index: IntProperty(update=_upd_lists)
    poses: CollectionProperty(type=MFB_PoseItem)
    pose_index: IntProperty(update=_upd_lists)
    drivers: CollectionProperty(type=MFB_NameItem)
    driven: CollectionProperty(type=MFB_NameItem)
    twists: CollectionProperty(type=MFB_NameItem)
    twists_index: IntProperty()
    driven_index: IntProperty()
    drivers_index: IntProperty()
    log: CollectionProperty(type=MFB_LogItem)
    log_index: IntProperty()
    show_errors: BoolProperty(name="Errors", default=True)
    show_warnings: BoolProperty(name="Warnings", default=True)
    show_info: BoolProperty(name="Info", default=True)
    log_expanded: BoolProperty(name="Expand Log", default=False)
    # solver settings
    solver_type: EnumProperty(name="Solver", items=[(str(i), n, "") for i, n in enumerate(R.SOLVER_NAMES)])
    distance_method: EnumProperty(name="Distance", items=[(str(i), n, "") for i, n in enumerate(R.DISTANCE_NAMES)])
    function_type: EnumProperty(name="Function", items=[(str(i), n, "") for i, n in enumerate(R.FUNCTION_NAMES)])
    normalize_method: EnumProperty(name="Normalize", items=(("0", "Only Above One", ""), ("1", "Always", "")))
    twist_axis: EnumProperty(name="Twist Axis", items=[(str(i), n, "") for i, n in enumerate(R.AXIS_NAMES)])
    radius: FloatProperty(name="Radius", default=45.0, min=0.0)
    automatic_radius: BoolProperty(name="Automatic Radius", default=False)
    weight_threshold: FloatProperty(name="Weight Threshold", default=0.001, min=0.0, max=1.0)
    # tools
    new_name: StringProperty(name="Name")
    mirror_rotation: EnumProperty(name="Mirror Rotation Across", items=(("0", "X", ""), ("1", "Y", ""), ("2", "Z", "")))
    mirror_translation: EnumProperty(name="Mirror Translation Across", items=(("0", "X", ""), ("1", "Y", ""), ("2", "Z", "")),
                                     default="1")
    inbetweens: IntProperty(name="In-betweens", default=1, min=1, max=9)
    search: StringProperty(name="Search For")
    replace: StringProperty(name="Replace With")
    use_regex: BoolProperty(name="Use Regex Pattern")
    export_path: StringProperty(name="Export DNA", subtype="FILE_PATH")
    selected_only: BoolProperty(name="Selected Solver Only", description="Export only the selected solver's edits "
                                "on top of the original DNA")
    editing: BoolProperty(name="Editing Pose")


# --------------------------------------------------------------------------- operators


class _PoseOp(bpy.types.Operator):
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        arm = _arm(context)
        return arm is not None and bool(arm.get(KIND_PROP[_state(context).kind]))

    def ctx(self, context):
        st = _state(context)
        arm = _arm(context)
        return st, arm, working_dna(arm, st.kind)


class MFB_OT_pose_unpack(_PoseOp):
    """Load the rig's RBF solvers, poses and twist/swing joints into the Pose Editor"""
    bl_idname = "mfb.pose_unpack"
    bl_label = "Unpack RigLogic"

    @classmethod
    def poll(cls, context):
        return _arm(context) is not None

    def execute(self, context):
        st = _state(context)
        arm = _arm(context)
        if not arm.get(KIND_PROP[st.kind]):
            self.report({"ERROR"}, f"{arm.name} has no {st.kind} DNA; assemble it with the Character Assembler first")
            return {"CANCELLED"}
        _dna_cache.pop((arm.name, st.kind), None)
        dna = working_dna(arm, st.kind)
        refresh_lists(context)
        log(context, "INFO", f"Unpacked {len(dna.rbf.solvers)} RBF solvers, {len(dna.rbf.poses)} poses, "
                             f"{len(dna.twist_swing.twists) + len(dna.twist_swing.swings)} twist/swing setups")
        return {"FINISHED"}


class MFB_OT_pose_edit(_PoseOp):
    """Put the drivers at the selected pose and pause the live rig so the driven joints can be adjusted"""
    bl_idname = "mfb.pose_edit"
    bl_label = "Edit Selected"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        if not st.poses:
            return {"CANCELLED"}
        rig.update_rig(arm, force=True)
        si, pose = st.solver_index, st.poses[st.pose_index]
        k = list(dna.rbf.solvers[si].pose_indices).index(pose.index)
        set_drivers_to_target(arm, dna, si, E.solver_targets(dna, si)[k])
        bpy.context.view_layer.update()
        rig.update_rig(arm, force=True)                     # show the pose as RigLogic makes it
        arm[rig.PROP_EDITING] = st.kind
        st.editing = True
        log(context, "INFO", f"Editing {pose.name}: adjust the driven joints, then Update")
        return {"FINISHED"}


class MFB_OT_pose_finish(_PoseOp):
    """Stop editing and let the live rig run again"""
    bl_idname = "mfb.pose_finish"
    bl_label = "Finish Editing"

    def execute(self, context):
        st, arm, _ = self.ctx(context)
        if rig.PROP_EDITING in arm:
            del arm[rig.PROP_EDITING]
        st.editing = False
        rig.update_rig(arm, force=True)
        return {"FINISHED"}


def _capture(arm, kind, dna, joints) -> dict[int, np.ndarray]:
    names = dna.definition.joint_names
    return {names.index(j): bone_delta(arm, kind, j) for j in joints if j in names}


class MFB_OT_pose_update(_PoseOp):
    """Store the driven joints' current transforms as the selected pose's correction"""
    bl_idname = "mfb.pose_update"
    bl_label = "Update"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        pose = st.poses[st.pose_index]
        joints = [d.name for d in st.driven]
        E.set_joint_column(dna, E.pose_control(dna, pose.index), _capture(arm, st.kind, dna, joints))
        commit(context, f"Updated {pose.name} ({len(joints)} driven joints)")
        return {"FINISHED"}


class MFB_OT_pose_driven(_PoseOp):
    """Add the selected bones to (or remove them from) the selected pose's driven joints"""
    bl_idname = "mfb.pose_driven"
    bl_label = "Driven Joints"
    add: BoolProperty(default=True)

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        pose = st.poses[st.pose_index]
        control = E.pose_control(dna, pose.index)
        current = E.joint_column(dna, control)
        names = dna.definition.joint_names
        chosen = [n for n in _selected_bone_names(context, arm) if n in names]
        if self.add:
            for n in chosen:
                current.setdefault(names.index(n), bone_delta(arm, st.kind, n) + np.float32([0] * 9))
                if not np.any(current[names.index(n)]):
                    current[names.index(n)][6] = 1e-5        # keep it listed until it gets a real value
        else:
            for n in chosen:
                current.pop(names.index(n), None)
        E.set_joint_column(dna, control, current, tolerance=1e-7)
        commit(context, f"{'Added' if self.add else 'Removed'} driven joints {chosen} on {pose.name}")
        return {"FINISHED"}


class MFB_OT_pose_driver(_PoseOp):
    """Add the selected bones as drivers of the selected solver (or remove them)"""
    bl_idname = "mfb.pose_driver"
    bl_label = "Driver Joints"
    add: BoolProperty(default=True)

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        si = st.solver_index
        for n in _selected_bone_names(context, arm):
            if n in dna.definition.joint_names:
                (E.add_driver if self.add else E.remove_driver)(dna, si, n)
        commit(context, f"{'Added' if self.add else 'Removed'} drivers on {dna.rbf.solvers[si].name}")
        return {"FINISHED"}


class MFB_OT_pose_create(_PoseOp):
    """New pose on the selected solver from the drivers' current rotation and the driven joints' transforms"""
    bl_idname = "mfb.pose_create"
    bl_label = "Create Pose"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        si = st.solver_index
        name = st.new_name.strip() or f"{dna.rbf.solvers[si].name}_pose{len(st.poses)}"
        if name in [p.name for p in dna.rbf.poses]:
            self.report({"ERROR"}, f"A pose named {name} already exists")
            return {"CANCELLED"}
        joints = [d.name for d in st.driven] or _selected_bone_names(context, arm)
        E.add_pose(dna, si, name, driver_target_now(arm, dna, si), _capture(arm, st.kind, dna, joints))
        commit(context, f"Created pose {name}")
        st.pose_index = len(st.poses) - 1
        return {"FINISHED"}


class MFB_OT_pose_rename(_PoseOp):
    """Rename the selected pose"""
    bl_idname = "mfb.pose_rename"
    bl_label = "Rename Pose"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        if not st.new_name.strip():
            self.report({"ERROR"}, "Type the new name in Name first")
            return {"CANCELLED"}
        old = st.poses[st.pose_index].name
        E.rename_pose(dna, st.poses[st.pose_index].index, st.new_name.strip())
        commit(context, f"Renamed {old} to {st.new_name.strip()}")
        return {"FINISHED"}


class MFB_OT_pose_delete(_PoseOp):
    """Delete the selected pose and its correction"""
    bl_idname = "mfb.pose_delete"
    bl_label = "Delete Pose"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        pose = st.poses[st.pose_index]
        E.remove_pose(dna, pose.index)
        commit(context, f"Deleted pose {pose.name}")
        return {"FINISHED"}


class MFB_OT_pose_scale(_PoseOp):
    """Set the selected pose's multiplier (how strongly its correction applies)"""
    bl_idname = "mfb.pose_scale"
    bl_label = "Set Multiplier"
    value: FloatProperty(name="Multiplier", default=1.0, min=0.0, max=2.0)

    def invoke(self, context, event):
        self.value = _state(context).poses[_state(context).pose_index].scale
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        pose = st.poses[st.pose_index]
        dna.rbf.poses[pose.index].scale = float(self.value)
        commit(context, f"{pose.name} multiplier set to {self.value:.2f}")
        return {"FINISHED"}


class MFB_OT_pose_mute(_PoseOp):
    """Mute or unmute the selected solver"""
    bl_idname = "mfb.pose_mute"
    bl_label = "Toggle Mute"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        name = st.solvers[st.solver_index].name
        muted = list(arm.get(rig.PROP_MUTED, []))
        muted.remove(name) if name in muted else muted.append(name)
        arm[rig.PROP_MUTED] = muted
        rig.update_rig(arm, force=True)
        refresh_lists(context)
        log(context, "INFO", f"{name} {'muted' if name in muted else 'unmuted'}")
        return {"FINISHED"}


class MFB_OT_pose_mirror(_PoseOp):
    """Copy the selected pose to the other side (left/right) with mirrored targets and corrections"""
    bl_idname = "mfb.pose_mirror"
    bl_label = "Mirror"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        si = st.solver_index
        pose = st.poses[st.pose_index]
        src_solver = dna.rbf.solvers[si]
        mirror_solver = E.mirror_name(src_solver.name)
        mirror_pose = E.mirror_name(pose.name)
        if mirror_pose == pose.name:
            self.report({"ERROR"}, f"{pose.name} has no left/right marker to mirror")
            return {"CANCELLED"}
        names = dna.definition.joint_names
        if mirror_solver not in [s.name for s in dna.rbf.solvers]:
            drivers = [E.mirror_name(j) for j in E.solver_drivers(dna, si)]
            E.add_solver(dna, mirror_solver, drivers, solver_type=src_solver.solver_type,
                         distance_method=src_solver.distance_method, function_type=src_solver.function_type,
                         radius=src_solver.radius, automatic_radius=src_solver.automatic_radius,
                         weight_threshold=src_solver.weight_threshold, normalize_method=src_solver.normalize_method,
                         twist_axis=src_solver.twist_axis)
        msi = E.solver_index(dna, mirror_solver)
        rot_axis, trans_axis = int(st.mirror_rotation), int(st.mirror_translation)
        k = list(src_solver.pose_indices).index(pose.index)
        target = E.solver_targets(dna, si)[k].reshape(-1, 4)
        target = np.concatenate([E.mirror_quat(q, rot_axis) for q in target])
        deltas = {}
        src_control = E.pose_control(dna, pose.index, create=False)
        for j, d in (E.joint_column(dna, src_control) if src_control >= 0 else {}).items():
            other = E.mirror_name(names[j])
            if other in names:
                deltas[names.index(other)] = E.mirror_delta(d, rot_axis, trans_axis)
        existing = [p.name for p in dna.rbf.poses]
        if mirror_pose in existing:
            E.remove_pose(dna, existing.index(mirror_pose))
            msi = E.solver_index(dna, mirror_solver)
        E.add_pose(dna, msi, mirror_pose, target, deltas, scale=dna.rbf.poses[pose.index].scale)
        commit(context, f"Mirrored {pose.name} to {mirror_pose} on {mirror_solver}")
        return {"FINISHED"}


class MFB_OT_pose_inbetweens(_PoseOp):
    """Add poses between the solver's rest pose and the selected pose, with scaled corrections"""
    bl_idname = "mfb.pose_inbetweens"
    bl_label = "Generate In-betweens"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        si = st.solver_index
        pose = st.poses[st.pose_index]
        k = list(dna.rbf.solvers[si].pose_indices).index(pose.index)
        target = E.solver_targets(dna, si)[k].reshape(-1, 4).astype(np.float64)
        src_control = E.pose_control(dna, pose.index, create=False)
        column = E.joint_column(dna, src_control) if src_control >= 0 else {}
        n = st.inbetweens
        made = []
        for i in range(1, n + 1):
            t = i / (n + 1)
            qs = np.concatenate([R.q_slerp(np.array([0, 0, 0, 1.0]), q, t) for q in target])
            name = f"{pose.name}_ib{int(round(t * 100)):02d}"
            if name in [p.name for p in dna.rbf.poses]:
                continue
            E.add_pose(dna, si, name, qs.astype(np.float32), {j: d * t for j, d in column.items()})
            made.append(name)
        commit(context, f"Generated in-betweens {made}")
        return {"FINISHED"}


class MFB_OT_pose_copy(_PoseOp):
    """Copy the selected pose's driven joint transforms"""
    bl_idname = "mfb.pose_copy"
    bl_label = "Copy Driven"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        names = dna.definition.joint_names
        control = E.pose_control(dna, st.poses[st.pose_index].index, create=False)
        col = E.joint_column(dna, control) if control >= 0 else {}
        _clipboard["driven"] = {names[j]: d.copy() for j, d in col.items()}
        self.report({"INFO"}, f"Copied {len(col)} driven transforms")
        return {"FINISHED"}


class MFB_OT_pose_paste(_PoseOp):
    """Paste copied driven joint transforms onto the selected pose"""
    bl_idname = "mfb.pose_paste"
    bl_label = "Paste Driven"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        src = _clipboard.get("driven")
        if not src:
            self.report({"ERROR"}, "Nothing copied yet")
            return {"CANCELLED"}
        names = dna.definition.joint_names
        pose = st.poses[st.pose_index]
        E.set_joint_column(dna, E.pose_control(dna, pose.index),
                           {names.index(n): d for n, d in src.items() if n in names})
        commit(context, f"Pasted {len(src)} driven transforms onto {pose.name}")
        return {"FINISHED"}


class MFB_OT_pose_settings(_PoseOp):
    """Apply the solver settings shown above to the selected solver"""
    bl_idname = "mfb.pose_settings"
    bl_label = "Apply Settings"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        s = dna.rbf.solvers[st.solver_index]
        s.solver_type, s.distance_method = int(st.solver_type), int(st.distance_method)
        s.function_type, s.normalize_method = int(st.function_type), int(st.normalize_method)
        s.twist_axis, s.radius, s.weight_threshold = int(st.twist_axis), st.radius, st.weight_threshold
        s.automatic_radius = 0 if st.automatic_radius else 1
        commit(context, f"Updated settings of {s.name}")
        return {"FINISHED"}


class MFB_OT_pose_new_solver(_PoseOp):
    """New RBF solver driven by the selected bones (with a rest pose)"""
    bl_idname = "mfb.pose_new_solver"
    bl_label = "New Solver"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        drivers = [n for n in _selected_bone_names(context, arm) if n in dna.definition.joint_names]
        if not drivers:
            self.report({"ERROR"}, "Select the driver bones in Pose Mode first")
            return {"CANCELLED"}
        name = st.new_name.strip() or f"{drivers[0]}_UERBFSolver"
        E.add_solver(dna, name, drivers, radius=st.radius, distance_method=int(st.distance_method),
                     function_type=int(st.function_type))
        commit(context, f"Created solver {name} driven by {drivers}")
        st.solver_index = len(st.solvers) - 1
        return {"FINISHED"}


class MFB_OT_pose_delete_solver(_PoseOp):
    """Delete the selected solver and all its poses"""
    bl_idname = "mfb.pose_delete_solver"
    bl_label = "Delete Solver"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        name = dna.rbf.solvers[st.solver_index].name
        E.remove_solver(dna, st.solver_index)
        commit(context, f"Deleted solver {name}")
        return {"FINISHED"}


class MFB_OT_twist_default(_PoseOp):
    """Twist setup: the active bone drives the other selected bones with evenly spread weights"""
    bl_idname = "mfb.twist_default"
    bl_label = "Generate Default Swing Twist Setup"
    swing: BoolProperty(name="Swing", default=False)

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        active = context.active_pose_bone
        if active is None and arm.data.bones.active is not None:
            active = arm.pose.bones.get(arm.data.bones.active.name)
        others = [n for n in _selected_bone_names(context, arm) if active is None or n != active.name]
        if active is None or not others:
            self.report({"ERROR"}, "Select the twist bones, then the driver bone last (active)")
            return {"CANCELLED"}
        E.add_twist_setup(dna, active.name, others, axis=int(st.twist_axis), swing=self.swing)
        commit(context, f"{'Swing' if self.swing else 'Twist'} setup: {active.name} drives {others}")
        return {"FINISHED"}


class MFB_OT_twist_delete(_PoseOp):
    """Delete the selected twist/swing setup"""
    bl_idname = "mfb.twist_delete"
    bl_label = "Delete Setup"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        i = st.twists_index
        nt = len(dna.twist_swing.twists)
        (dna.twist_swing.twists.pop(i) if i < nt else dna.twist_swing.swings.pop(i - nt))
        commit(context, "Deleted a twist/swing setup")
        return {"FINISHED"}


class MFB_OT_pose_find_replace(_PoseOp):
    """Rename solvers and poses by search and replace"""
    bl_idname = "mfb.pose_find_replace"
    bl_label = "Replace Names"

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        try:
            n = E.find_replace(dna, st.search, st.replace, regex=st.use_regex)
        except Exception as exc:
            self.report({"ERROR"}, f"Pattern problem: {exc}")
            return {"CANCELLED"}
        commit(context, f"Renamed {n} solver/pose names")
        return {"FINISHED"}


class MFB_OT_pose_validate(_PoseOp):
    """Check the edited rig for RigLogic, or against the original DNA for MetaHuman Creator"""
    bl_idname = "mfb.pose_validate"
    bl_label = "Validate"
    target: EnumProperty(items=(("RIGLOGIC", "RigLogic", ""), ("MHC", "MetaHuman Creator", "")))

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        if self.target == "RIGLOGIC":
            msgs = E.validate_riglogic(dna)
        else:
            original = arm.get(ORIGINAL_PROP.format(kind=st.kind), arm[KIND_PROP[st.kind]])
            msgs = E.validate_against_reference(dna, read_dna(original))
        for m in msgs:
            log(context, m.level, m.text)
        errors = sum(m.level == "ERROR" for m in msgs)
        self.report({"ERROR" if errors else "INFO"}, f"{errors} errors, {len(msgs) - errors} other messages")
        return {"FINISHED"}


class MFB_OT_pose_export(_PoseOp):
    """Write the edited DNA (RBF poses, twist/swing and, optionally, edited geometry)"""
    bl_idname = "mfb.pose_export"
    bl_label = "Export DNA"
    geometry: BoolProperty(name="Include Geometry", default=False,
                           description="Write the shapes of the meshes back into the DNA")
    selected_geometry: BoolProperty(name="Selected Meshes Only", default=False)

    def execute(self, context):
        st, arm, dna = self.ctx(context)
        path = bpy.path.abspath(st.export_path)
        if not path:
            self.report({"ERROR"}, "Set the Export DNA path first")
            return {"CANCELLED"}
        out = dna
        if st.selected_only:
            out = _selected_solver_on_original(arm, st, dna)
        if self.geometry:
            n = export_geometry(context, arm, out, selected_only=self.selected_geometry)
            log(context, "INFO", f"Wrote {n} mesh shapes into the DNA")
        write_dna(out, path)
        log(context, "INFO", f"Exported DNA to {path}")
        self.report({"INFO"}, f"Exported {Path(path).name}")
        return {"FINISHED"}


def _selected_solver_on_original(arm, st, dna):
    original = read_dna(arm.get(ORIGINAL_PROP.format(kind=st.kind), arm[KIND_PROP[st.kind]]))
    E.ensure_rbf_layers(original)
    name = dna.rbf.solvers[st.solver_index].name
    if name in [s.name for s in original.rbf.solvers]:
        E.remove_solver(original, E.solver_index(original, name))
    s = dna.rbf.solvers[st.solver_index]
    si = E.add_solver(original, name, E.solver_drivers(dna, st.solver_index), solver_type=s.solver_type,
                      distance_method=s.distance_method, function_type=s.function_type, radius=s.radius,
                      automatic_radius=s.automatic_radius, weight_threshold=s.weight_threshold,
                      normalize_method=s.normalize_method, twist_axis=s.twist_axis)
    E.remove_pose(original, int(original.rbf.solvers[si].pose_indices[0]))     # drop the generated default
    si = E.solver_index(original, name)
    for k, p in enumerate(s.pose_indices):
        original_si = E.solver_index(original, name)
        E.add_pose(original, original_si, dna.rbf.poses[int(p)].name, E.solver_targets(dna, st.solver_index)[k],
                   E.joint_column(dna, E.pose_control(dna, int(p))), scale=dna.rbf.poses[int(p)].scale)
    return original


def export_geometry(context, arm, dna, selected_only=False) -> int:
    """Write each mesh's rest shape (from Blender, in DNA space) into the DNA's geometry."""
    space = Space.for_dna(dna)
    count = 0
    full = read_dna(dna.path) if any(m is None for m in dna.meshes) else None
    for obj in arm.children:
        if obj.type != "MESH" or "mfb_mesh_index" not in obj or (selected_only and not obj.select_get()):
            continue
        mi = int(obj["mfb_mesh_index"])
        if mi >= len(dna.mesh_raw) or obj.data.name.split(".")[0] != dna.definition.mesh_names[mi]:
            continue
        if dna.meshes[mi] is None and full is not None:
            dna.meshes[mi] = full.meshes[mi]
        mesh = dna.meshes[mi]
        basis = obj.data.shape_keys.key_blocks[0].data if obj.data.shape_keys else obj.data.vertices
        co = np.zeros(len(basis) * 3, np.float32)
        basis.foreach_get("co", co)
        if len(co) // 3 != len(mesh.positions):
            log(context, "WARNING", f"{obj.name}: vertex count changed, skipped")
            continue
        mesh.positions = ((co.reshape(-1, 3) / space.s) @ space.C).astype(np.float32)   # inverse of Space.points
        count += 1
    return count


class MFB_OT_pose_import(bpy.types.Operator):
    """Build the skeleton and geometry from a DNA file (Character Assembler, without the face board)"""
    bl_idname = "mfb.pose_import"
    bl_label = "Import from DNA"
    bl_options = {"REGISTER", "UNDO"}
    filepath: StringProperty(subtype="FILE_PATH")
    geometry: BoolProperty(name="Import Geometry", default=True)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from .assemble import AssembleOptions, assemble, dna_role
        role = dna_role(read_dna(self.filepath, geometry=False))
        opts = AssembleOptions(head_dna=self.filepath if role == "head" else "",
                               body_dna=self.filepath if role == "body" else "", face_board=False,
                               materials=self.geometry, mesh_filter=None if self.geometry else set())
        res = assemble(opts)
        st = _state(context)
        st.armature = res["armatures"][role]
        st.kind = role
        bpy.ops.mfb.pose_unpack()
        return {"FINISHED"}


class MFB_OT_dna_upgrade(bpy.types.Operator):
    """Rewrite an older DNA file (2.2 to 2.4) as version 2.5, adding the RBF and twist/swing layers"""
    bl_idname = "mfb.dna_upgrade"
    bl_label = "Upgrade DNA File"
    filepath: StringProperty(subtype="FILE_PATH")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        src = Path(self.filepath)
        dna = read_dna(src)
        out = src.with_name(f"{src.stem}_v25.dna")
        write_dna(dna, out, upgrade=True)
        self.report({"INFO"}, f"Wrote {out.name} (was {dna.version[0]}.{dna.version[1]})")
        return {"FINISHED"}


class MFB_OT_log(bpy.types.Operator):
    """Copy the selected message, or clear the log"""
    bl_idname = "mfb.pose_log"
    bl_label = "Log"
    action: EnumProperty(items=(("COPY", "Copy Selected Message", ""), ("CLEAR", "Clear", "")))

    def execute(self, context):
        st = _state(context)
        if self.action == "CLEAR":
            st.log.clear()
        elif st.log:
            m = st.log[st.log_index]
            context.window_manager.clipboard = f"{m.level}: {m.text}"
        return {"FINISHED"}


# --------------------------------------------------------------------------- UI


class MFB_UL_pose_solvers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon="MUTE_IPO_ON" if item.muted else "DRIVER")


class MFB_UL_pose_poses(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()
        row.label(text=item.name, icon="POSE_HLT")
        row.label(text=f"x{item.scale:.2f}")


class MFB_UL_pose_names(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon="BONE_DATA")


class MFB_UL_pose_log(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        icons = {"ERROR": "ERROR", "WARNING": "ERROR", "INFO": "INFO"}
        layout.label(text=f"{item.time} {item.text}", icon=icons.get(item.level, "INFO"))

    def filter_items(self, context, data, propname):
        st = _state(context)
        items = getattr(data, propname)
        show = {"ERROR": st.show_errors, "WARNING": st.show_warnings, "INFO": st.show_info}
        flags = [self.bitflag_filter_item if show.get(i.level, True) else 0 for i in items]
        return flags, []


class MFB_PT_pose(bpy.types.Panel):
    bl_label = "Pose Editor"
    bl_idname = "MFB_PT_pose"
    bl_ui_units_x = 18          # width when opened as a floating panel from the menu
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MetaHuman"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        st = _state(context)
        layout = self.layout
        row = layout.row(align=True)
        row.prop(st, "armature", text="")
        row.prop(st, "kind", text="")
        row = layout.row(align=True)
        row.operator("mfb.pose_unpack", icon="IMPORT")
        row.operator("mfb.pose_import", icon="FILE_FOLDER", text="Import DNA")
        if not st.solvers:
            layout.label(text="Unpack a rig to edit its RBF solvers")
            return
        layout.label(text="RBF Solvers")
        layout.template_list("MFB_UL_pose_solvers", "", st, "solvers", st, "solver_index", rows=4)
        row = layout.row(align=True)
        row.operator("mfb.pose_new_solver", icon="ADD", text="New")
        row.operator("mfb.pose_delete_solver", icon="REMOVE", text="Delete")
        row.operator("mfb.pose_mute", icon="MUTE_IPO_ON", text="Mute")
        box = layout.box()
        box.label(text="RBF Driver Joints: " + ", ".join(d.name for d in st.drivers))
        row = box.row(align=True)
        row.operator("mfb.pose_driver", text="Add Driver", icon="ADD").add = True
        row.operator("mfb.pose_driver", text="Remove Driver", icon="REMOVE").add = False
        col = box.column(align=True)
        for prop in ("solver_type", "distance_method", "function_type", "normalize_method", "twist_axis", "radius",
                     "automatic_radius", "weight_threshold"):
            col.prop(st, prop)
        box.operator("mfb.pose_settings", icon="CHECKMARK")

        layout.label(text=f"RBF Poses ({len(st.poses)})")
        layout.template_list("MFB_UL_pose_poses", "", st, "poses", st, "pose_index", rows=5)
        layout.prop(st, "new_name")
        row = layout.row(align=True)
        row.operator("mfb.pose_create", icon="ADD", text="Create")
        row.operator("mfb.pose_rename", text="Rename")
        row.operator("mfb.pose_delete", icon="REMOVE", text="Delete")
        row.operator("mfb.pose_scale", text="Multiplier")
        row = layout.row(align=True)
        if st.editing:
            row.operator("mfb.pose_update", icon="FILE_REFRESH")
            row.operator("mfb.pose_finish", icon="CHECKMARK")
        else:
            row.operator("mfb.pose_edit", icon="GREASEPENCIL")
        box = layout.box()
        box.label(text=f"RBF Driven Joints ({len(st.driven)})")
        box.template_list("MFB_UL_pose_names", "driven", st, "driven", st, "driven_index", rows=3)
        row = box.row(align=True)
        row.operator("mfb.pose_driven", text="Add Driven", icon="ADD").add = True
        row.operator("mfb.pose_driven", text="Remove Driven", icon="REMOVE").add = False
        row = box.row(align=True)
        row.operator("mfb.pose_copy", icon="COPYDOWN")
        row.operator("mfb.pose_paste", icon="PASTEDOWN")
        box = layout.box()
        row = box.row(align=True)
        row.prop(st, "mirror_rotation", text="Rot")
        row.prop(st, "mirror_translation", text="Trans")
        row.operator("mfb.pose_mirror", icon="MOD_MIRROR")
        row = box.row(align=True)
        row.prop(st, "inbetweens")
        row.operator("mfb.pose_inbetweens", icon="IPO_EASE_IN_OUT")


class MFB_PT_pose_twist(bpy.types.Panel):
    bl_label = "Swing / Twist"
    bl_parent_id = "MFB_PT_pose"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MetaHuman"

    def draw(self, context):
        st = _state(context)
        layout = self.layout
        layout.template_list("MFB_UL_pose_names", "twists", st, "twists", st, "twists_index", rows=3)
        row = layout.row(align=True)
        row.operator("mfb.twist_default", text="Twist Setup", icon="ADD").swing = False
        row.operator("mfb.twist_default", text="Swing Setup", icon="ADD").swing = True
        row.operator("mfb.twist_delete", icon="REMOVE", text="")


class MFB_PT_pose_tools(bpy.types.Panel):
    bl_label = "Tools, Checks and Export"
    bl_parent_id = "MFB_PT_pose"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MetaHuman"

    def draw(self, context):
        st = _state(context)
        layout = self.layout
        col = layout.column(align=True)
        col.prop(st, "search"); col.prop(st, "replace")
        row = layout.row(align=True)
        row.prop(st, "use_regex")
        row.operator("mfb.pose_find_replace")
        row = layout.row(align=True)
        row.operator("mfb.pose_validate", text="Validate for RigLogic").target = "RIGLOGIC"
        row.operator("mfb.pose_validate", text="Validate for MetaHuman Creator").target = "MHC"
        layout.prop(st, "export_path")
        layout.prop(st, "selected_only")
        row = layout.row(align=True)
        op = row.operator("mfb.pose_export", icon="EXPORT"); op.geometry = False
        op = row.operator("mfb.pose_export", text="With Geometry"); op.geometry = True
        op = row.operator("mfb.pose_export", text="Selected Geometry"); op.geometry = True; op.selected_geometry = True
        layout.operator("mfb.dna_upgrade", icon="FILE_REFRESH")
        layout.separator()
        row = layout.row(align=True)
        row.prop(st, "show_errors", toggle=True); row.prop(st, "show_warnings", toggle=True)
        row.prop(st, "show_info", toggle=True); row.prop(st, "log_expanded", text="", icon="FULLSCREEN_ENTER")
        layout.template_list("MFB_UL_pose_log", "", st, "log", st, "log_index", rows=8 if st.log_expanded else 3)
        row = layout.row(align=True)
        row.operator("mfb.pose_log", text="Copy Selected Message", icon="COPYDOWN").action = "COPY"
        row.operator("mfb.pose_log", text="Clear", icon="TRASH").action = "CLEAR"


CLASSES = (MFB_PoseSolverItem, MFB_PoseItem, MFB_NameItem, MFB_LogItem, MFB_PoseState, MFB_OT_pose_unpack,
           MFB_OT_pose_edit, MFB_OT_pose_finish, MFB_OT_pose_update, MFB_OT_pose_driven, MFB_OT_pose_driver,
           MFB_OT_pose_create, MFB_OT_pose_rename, MFB_OT_pose_delete, MFB_OT_pose_scale, MFB_OT_pose_mute,
           MFB_OT_pose_mirror, MFB_OT_pose_inbetweens, MFB_OT_pose_copy, MFB_OT_pose_paste, MFB_OT_pose_settings,
           MFB_OT_pose_new_solver, MFB_OT_pose_delete_solver, MFB_OT_twist_default, MFB_OT_twist_delete,
           MFB_OT_pose_find_replace, MFB_OT_pose_validate, MFB_OT_pose_export, MFB_OT_pose_import,
           MFB_OT_dna_upgrade, MFB_OT_log, MFB_UL_pose_solvers, MFB_UL_pose_poses, MFB_UL_pose_names,
           MFB_UL_pose_log, MFB_PT_pose, MFB_PT_pose_twist, MFB_PT_pose_tools)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mfb_pose = PointerProperty(type=MFB_PoseState)


def unregister():
    del bpy.types.Scene.mfb_pose
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _dna_cache.clear()
