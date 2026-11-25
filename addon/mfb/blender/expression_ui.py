"""Expression Editor: reshape the neutral face, sculpt expressions, edit skin weights, match joints.

Works on a head assembled by the Character Assembler. Edits go to the head's working DNA
(shared with the Pose Editor) and show live through RigLogic.
"""
from __future__ import annotations

import json
from pathlib import Path

import bpy
import numpy as np
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)
from mathutils import kdtree

from ..core import dna_edit as E
from ..core import expression as X
from ..core.space import Space
from ..dna.reader import read_dna
from ..dna.writer import write_dna
from . import pose_ui, rig
from .ctx import set_mode

COLOURS = (("NONE", "None", "", "BLANK1", 0), ("RED", "Red", "", "COLORSET_01_VEC", 1),
           ("ORANGE", "Orange", "", "COLORSET_02_VEC", 2), ("GREEN", "Green", "", "COLORSET_03_VEC", 3),
           ("BLUE", "Blue", "", "COLORSET_04_VEC", 4), ("PURPLE", "Purple", "", "COLORSET_06_VEC", 5))
COLOUR_ICON = {c[0]: c[3] for c in COLOURS}
EYE_WORDS = ("eye", "blink", "squint", "lid", "look", "pupil")


def _st(context):
    return context.scene.mfb_expr


def _arm(context):
    a = _st(context).armature
    return a if a and a.type == "ARMATURE" and a.get("mfb_head_dna") else None


def _dna(arm):
    return pose_ui.working_dna(arm, "head")


def _head_mesh(arm, lod=0):
    for o in arm.children:
        if o.type == "MESH" and o.data.name.split(".")[0] == f"head_lod{lod}_mesh":
            return o
    return None


def ex_commit(context, message: str) -> None:
    """Write the head working copy, refresh the live rig and the expression list."""
    st = _st(context)
    arm = _arm(context)
    pstate = context.scene.mfb_pose
    pstate.armature, pstate.kind = arm, "head"
    pose_ui.commit(context, message)
    _refresh(context)
    st.status = message


def _refresh(context) -> None:
    st = _st(context)
    arm = _arm(context)
    keep = {e.control: (e.locked, e.colour) for e in st.expressions}
    st.expressions.clear()
    if arm is None:
        return
    graph = X.expression_graph(_dna(arm))
    for c, e in sorted(graph.items(), key=lambda kv: (kv[1].is_psd, kv[1].name)):
        item = st.expressions.add()
        item.name, item.control, item.is_psd = e.name, c, e.is_psd
        item.locked, item.colour = keep.get(c, (False, "NONE"))


def _graph(arm):
    return X.expression_graph(_dna(arm))


def _current(context):
    st = _st(context)
    return st.expressions[st.expr_index] if st.expressions else None


def _evaluated_coords(context, obj) -> np.ndarray:
    dg = context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    co = np.zeros(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return co.reshape(-1, 3)


def _basis_coords(obj) -> np.ndarray:
    src = obj.data.shape_keys.key_blocks[0].data if obj.data.shape_keys else obj.data.vertices
    co = np.zeros(len(src) * 3, np.float32)
    src.foreach_get("co", co)
    return co.reshape(-1, 3)


def _space(dna):
    return Space.for_dna(dna)


def _to_dna(space, d_blender):
    return (np.asarray(d_blender, np.float64) / space.s) @ space.C


def _to_blender(space, d_dna):
    return (np.asarray(d_dna, np.float64) @ space.C.T) * space.s


def _set_raw(arm, values: dict[str, float]) -> None:
    """Drive the face from raw attributes (Raw mode), all others at zero."""
    rt = rig.runtime(arm, "head")
    root = arm.pose.bones.get(rig.FACE_ROOT)
    arm[rig.PROP_DRIVE] = "RAW"
    for attr in rt.raw_attrs:
        if "_q" not in attr:
            root[attr] = float(values.get(attr, 0.0))
    rig.update_rig(arm, force=True)
    bpy.context.view_layer.update()


def _expression_values(arm, graph, control) -> dict[str, float]:
    d = _dna(arm).definition
    e = graph[control]
    controls = e.inputs if e.is_psd else [control]
    return {rig.raw_attr_name(d.raw_control_names[c]): 1.0 for c in controls if c < len(d.raw_control_names)}


def _update_shape_key(obj, name: str, basis_b: np.ndarray, delta_b: np.ndarray, channel: int = -1) -> None:
    """Write a channel's shape into the mesh's shape key for it, creating the key if needed."""
    if obj.data.shape_keys is None:
        obj.shape_key_add(name="Basis", from_mix=False)
    recorded = dict(obj.get("mfb_shape_channels", {}))
    key_name = next((k for k, c in recorded.items() if c == channel), None) if channel >= 0 else None
    key = obj.data.shape_keys.key_blocks.get(key_name or name)
    if key is None:
        key = obj.shape_key_add(name=name, from_mix=False)          # Blender may shorten the name
        if channel >= 0:
            recorded[key.name] = channel
            obj["mfb_shape_channels"] = recorded
    key.data.foreach_set("co", (basis_b + delta_b).astype(np.float32).ravel())
    obj.data.update()


# --------------------------------------------------------------------------- property groups


class MFB_ExprItem(bpy.types.PropertyGroup):
    name: StringProperty()
    control: IntProperty()
    is_psd: BoolProperty()
    locked: BoolProperty(name="Locked", description="Propagation never changes a locked expression")
    colour: EnumProperty(name="Colour", items=COLOURS)
    marked: BoolProperty()                      # part of the selected branch


class MFB_Bookmark(bpy.types.PropertyGroup):
    name: StringProperty()
    values: StringProperty()                    # JSON {raw attribute: value}


class MFB_ExprState(bpy.types.PropertyGroup):
    armature: PointerProperty(type=bpy.types.Object, name="Head Rig",
                              poll=lambda s, o: o.type == "ARMATURE" and bool(o.get("mfb_head_dna")))
    mode: EnumProperty(name="Editing Mode", items=(
        ("NEUTRAL", "Neutral Pose", "Reshape the face at rest"),
        ("EXPRESSION", "Expression Poses", "Sculpt what an expression looks like"),
        ("SKIN", "Skin Weights", "Paint how the face follows its joints"),
        ("JOINTS", "Joints Matching", "Fit the joints to a reshaped face")))
    expressions: CollectionProperty(type=MFB_ExprItem)
    expr_index: IntProperty()
    filter_psd: BoolProperty(name="Correctives", default=True)
    isolate: BoolProperty(name="Isolate Selected Branch", description="Show only the selected expression's "
                          "upstream and downstream expressions")
    highlight: BoolProperty(name="Highlight Last Selected", default=True)
    propagation: EnumProperty(name="Propagation", items=(
        ("KEEP", "Keep Combinations", "Subtract the edit from downstream correctives so combined poses look the same"),
        ("CARRY", "Carry Into Combinations", "Leave correctives alone so the edit shows in every combination")))
    strength: FloatProperty(name="Propagation Strength", default=1.0, min=0.0, max=1.0,
                            description="Advanced: how much of the edit propagation removes from correctives")
    match_joints: BoolProperty(name="Match Joints", default=True, description="Move joints with the reshaped face")
    transfer_lods: BoolProperty(name="Update Lower LODs", default=True)
    keep_sculpt: BoolProperty(name="Keep Sculpt Mesh", default=False)
    copy_target: StringProperty(name="Copy Joints To", description="Expression name that receives the joint motion")
    bookmarks: CollectionProperty(type=MFB_Bookmark)
    bookmark_index: IntProperty()
    bookmark_name: StringProperty(name="Bookmark Name")
    rom_step: IntProperty(name="Frames per Expression", default=4, min=2, max=48)
    file_path: StringProperty(name="File", subtype="FILE_PATH")
    export_up: EnumProperty(name="Export Up Axis", items=(("Y", "Y-up", ""), ("Z", "Z-up", "")))
    status: StringProperty()


# --------------------------------------------------------------------------- operators


class _ExprOp(bpy.types.Operator):
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _arm(context) is not None


class MFB_OT_expr_load(bpy.types.Operator):
    """Load a head DNA (MetaHuman 5.6 or later), with an optional body DNA, for editing"""
    bl_idname = "mfb.expr_load"
    bl_label = "Load MetaHuman DNA"
    bl_options = {"REGISTER", "UNDO"}
    head: StringProperty(name="Head DNA", subtype="FILE_PATH")
    body: StringProperty(name="Body DNA (optional)", subtype="FILE_PATH")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=480)

    def execute(self, context):
        from .assemble import AssembleOptions, assemble
        dna = read_dna(bpy.path.abspath(self.head), geometry=False)
        if not dna.definition.gui_control_names:
            self.report({"ERROR"}, "That file has no face rig; choose a head DNA")
            return {"CANCELLED"}
        if dna.version < (2, 4) or "MH.6" not in dna.descriptor.db_name:
            self.report({"WARNING"}, f"{Path(self.head).name} is {dna.descriptor.db_name or 'unknown'} "
                                     f"(DNA {dna.version[0]}.{dna.version[1]}); the editor expects MetaHuman 5.6+ (MH.6)")
        res = assemble(AssembleOptions(head_dna=bpy.path.abspath(self.head),
                                       body_dna=bpy.path.abspath(self.body) if self.body else ""))
        _st(context).armature = res["armatures"]["head"]
        _refresh(context)
        return {"FINISHED"}


class MFB_OT_expr_refresh(_ExprOp):
    """Read the expressions of the head rig"""
    bl_idname = "mfb.expr_refresh"
    bl_label = "Refresh Expressions"

    def execute(self, context):
        _refresh(context)
        return {"FINISHED"}


class MFB_OT_expr_preview(_ExprOp):
    """Show the selected expression on the face (Raw mode, everything else at zero)"""
    bl_idname = "mfb.expr_preview"
    bl_label = "Preview Selected"

    def execute(self, context):
        arm = _arm(context)
        e = _current(context)
        _set_raw(arm, _expression_values(arm, _graph(arm), e.control) if e else {})
        return {"FINISHED"}


class MFB_OT_expr_neutral(_ExprOp):
    """Back to the neutral face"""
    bl_idname = "mfb.expr_neutral"
    bl_label = "Neutral"

    def execute(self, context):
        _set_raw(_arm(context), {})
        return {"FINISHED"}


class MFB_OT_expr_sculpt(_ExprOp):
    """Make a sculpt mesh of the head in the selected expression"""
    bl_idname = "mfb.expr_sculpt"
    bl_label = "Sculpt Expression"

    def execute(self, context):
        arm = _arm(context)
        e = _current(context)
        head = _head_mesh(arm)
        _set_raw(arm, _expression_values(arm, _graph(arm), e.control))
        co = _evaluated_coords(context, head)
        me = head.data.copy()
        if me.shape_keys:
            sculpt = bpy.data.objects.new(f"SCULPT_{e.name}", me)
            sculpt.shape_key_clear()
        else:
            sculpt = bpy.data.objects.new(f"SCULPT_{e.name}", me)
        sculpt.data.vertices.foreach_set("co", co.ravel())
        sculpt.data.update()
        sculpt["mfb_sculpt_for"] = e.control
        context.scene.collection.objects.link(sculpt)
        head.hide_set(True)
        return {"FINISHED"}


def _skin_linear(arm, dna, mesh_index, space):
    d = dna.definition
    m = dna.meshes[mesh_index]
    lin = np.tile(np.eye(3), (len(d.joint_names), 1, 1))
    for j, name in enumerate(d.joint_names):
        pb = arm.pose.bones.get(name)
        if pb:
            lin[j] = np.array((pb.matrix @ pb.bone.matrix_local.inverted()).to_3x3())
    return X.skinning_linear(m.skin_weights, m.skin_joints, lin)


class MFB_OT_expr_apply_sculpt(_ExprOp):
    """Turn the sculpt mesh into the expression's shape (corrective blend shape), with propagation"""
    bl_idname = "mfb.expr_apply_sculpt"
    bl_label = "Apply Sculpt"

    def execute(self, context):
        st = _st(context)
        arm = _arm(context)
        sculpt = next((o for o in context.scene.objects if "mfb_sculpt_for" in o), None)
        if sculpt is None:
            self.report({"ERROR"}, "Make a sculpt mesh first")
            return {"CANCELLED"}
        control = int(sculpt["mfb_sculpt_for"])
        dna = _dna(arm)
        graph = X.expression_graph(dna)
        head = _head_mesh(arm)
        mi = int(head["mfb_mesh_index"])
        space = _space(dna)
        _set_raw(arm, _expression_values(arm, graph, control))
        posed = _evaluated_coords(context, head)
        sc = np.zeros(len(sculpt.data.vertices) * 3, np.float32)
        sculpt.data.vertices.foreach_get("co", sc)
        posed_delta_b = (sc.reshape(-1, 3) @ np.array(sculpt.matrix_world.to_3x3()).T
                         + np.array(sculpt.matrix_world.translation)) - posed
        rest_b = X.corrective_delta(posed_delta_b, _skin_linear(arm, dna, mi, space))
        rest_dna = _to_dna(space, rest_b)
        ch = X.ensure_channel(dna, control, graph[control].name)
        X.set_target_delta(dna, mi, ch, X.target_delta(dna, mi, ch) + rest_dna)
        locked = {e.control for e in st.expressions if e.locked}
        changed = X.propagate(dna, graph, control, mi, rest_dna, mode=st.propagation, strength=st.strength,
                              locked=locked)
        basis = _basis_coords(head)
        names = dna.definition.blend_shape_channel_names
        for c in [control, *changed]:
            for cch in X.channels_for(dna, c):
                _update_shape_key(head, names[cch], basis, _to_blender(space, X.target_delta(dna, mi, cch)), cch)
        if not st.keep_sculpt:
            bpy.data.objects.remove(sculpt)
        head.hide_set(False)
        ex_commit(context, f"Applied sculpt to {graph[control].name}; propagated to {len(changed)} correctives")
        return {"FINISHED"}


class MFB_OT_expr_previews(_ExprOp):
    """Create preview meshes of the selected expression's downstream correctives"""
    bl_idname = "mfb.expr_previews"
    bl_label = "Preview Downstream"
    unlocked_only: BoolProperty(default=False)
    selected_only: BoolProperty(default=True)

    def execute(self, context):
        st = _st(context)
        arm = _arm(context)
        graph = _graph(arm)
        head = _head_mesh(arm)
        e = _current(context)
        targets = X.downstream(graph, e.control)
        if self.unlocked_only:
            locked = {x.control for x in st.expressions if x.locked}
            targets = [t for t in targets if t not in locked]
        coll = bpy.data.collections.get("MFB_Previews") or bpy.data.collections.new("MFB_Previews")
        if coll.name not in context.scene.collection.children:
            context.scene.collection.children.link(coll)
        for k, c in enumerate(targets[:24]):
            _set_raw(arm, _expression_values(arm, graph, c))
            co = _evaluated_coords(context, head)
            me = bpy.data.meshes.new(f"PREVIEW_{graph[c].name}")
            me.from_pydata(co.tolist(), [], [tuple(p.vertices) for p in head.data.polygons])
            ob = bpy.data.objects.new(me.name, me)
            ob.location.x = 0.35 * (k + 1)
            coll.objects.link(ob)
        _set_raw(arm, {})
        st.status = f"Made {min(len(targets), 24)} preview meshes"
        return {"FINISHED"}


class MFB_OT_expr_delete_previews(bpy.types.Operator):
    """Delete the preview meshes"""
    bl_idname = "mfb.expr_delete_previews"
    bl_label = "Delete Previews"

    def execute(self, context):
        coll = bpy.data.collections.get("MFB_Previews")
        if coll:
            for o in list(coll.objects):
                bpy.data.objects.remove(o)
            bpy.data.collections.remove(coll)
        return {"FINISHED"}


class MFB_OT_expr_branch(_ExprOp):
    """Mark the selected expression's upstream and/or downstream expressions"""
    bl_idname = "mfb.expr_branch"
    bl_label = "Select Branch"
    direction: EnumProperty(items=(("UP", "Upstream", ""), ("DOWN", "Downstream", ""), ("BOTH", "Both", "")))

    def execute(self, context):
        st = _st(context)
        arm = _arm(context)
        graph = _graph(arm)
        e = _current(context)
        marked = {e.control}
        if self.direction in ("UP", "BOTH"):
            marked |= set(X.upstream(graph, e.control))
        if self.direction in ("DOWN", "BOTH"):
            marked |= set(X.downstream(graph, e.control))
        for item in st.expressions:
            item.marked = item.control in marked
        st.status = f"{len(marked) - 1} expressions in the branch"
        return {"FINISHED"}


class MFB_OT_expr_lock(_ExprOp):
    """Lock or unlock the selected expression (propagation skips locked ones)"""
    bl_idname = "mfb.expr_lock"
    bl_label = "Toggle Lock"

    def execute(self, context):
        e = _current(context)
        e.locked = not e.locked
        return {"FINISHED"}


class MFB_OT_expr_apply_neutral(_ExprOp):
    """Store the head's reshaped neutral (Basis) in the DNA, fit the joints and carry it to lower LODs"""
    bl_idname = "mfb.expr_apply_neutral"
    bl_label = "Apply Neutral"

    def execute(self, context):
        st = _st(context)
        arm = _arm(context)
        dna = _dna(arm)
        head = _head_mesh(arm)
        mi = int(head["mfb_mesh_index"])
        space = _space(dna)
        new = _to_dna(space, _basis_coords(head))
        old = dna.meshes[mi].positions.astype(np.float64)
        disp = new - old
        if np.abs(disp).max() < 1e-6:
            self.report({"INFO"}, "The neutral face has not changed")
            return {"CANCELLED"}
        dna.meshes[mi].positions = new.astype(np.float32)
        msg = [f"neutral moved up to {np.abs(disp).max():.2f} cm"]
        if st.transfer_lods:
            n = _transfer_to_lods(context, arm, dna, mi, old, disp)
            msg.append(f"{n} lower LODs updated")
        if st.match_joints:
            moves = X.match_joints(dna, mi, disp)
            X.apply_joint_moves(dna, moves)
            _move_bones(arm, dna, space, moves)
            msg.append(f"{int((np.abs(moves).sum(1) > 1e-6).sum())} joints matched")
        ex_commit(context, "Applied neutral: " + ", ".join(msg))
        return {"FINISHED"}


def _transfer_to_lods(context, arm, dna, mi, lod0_rest, disp) -> int:
    part = dna.definition.mesh_names[mi].split("_lod")[0]
    tree = kdtree.KDTree(len(lod0_rest))
    for i, p in enumerate(lod0_rest):
        tree.insert(p, i)
    tree.balance()

    def nearest(points, k):
        idx = np.zeros((len(points), k), np.intp); dist = np.zeros((len(points), k))
        for r, p in enumerate(points):
            hits = tree.find_n(p, k)
            for c, (_, i, d) in enumerate(hits):
                idx[r, c], dist[r, c] = i, d
        return idx, dist

    space = _space(dna)
    count = 0
    for k, name in enumerate(dna.definition.mesh_names):
        if k == mi or not name.startswith(part + "_lod") or dna.meshes[k] is None:
            continue
        m = dna.meshes[k]
        moved = X.lod_transfer(lod0_rest, disp, m.positions.astype(np.float64), nearest=nearest)
        m.positions = (m.positions + moved).astype(np.float32)
        obj = next((o for o in arm.children if o.type == "MESH" and int(o.get("mfb_mesh_index", -1)) == k), None)
        if obj is not None:
            co = _to_blender(space, m.positions).astype(np.float32)
            (obj.data.shape_keys.key_blocks[0].data if obj.data.shape_keys else obj.data.vertices).foreach_set(
                "co", co.ravel())
            obj.data.update()
        count += 1
    return count


def _move_bones(arm, dna, space, moves_dna) -> None:
    """Shift edit bones by the joint moves (rest pose), keeping their orientation."""
    names = dna.definition.joint_names
    moves_b = _to_blender(space, moves_dna)
    vl = bpy.context.view_layer
    prev = vl.objects.active
    vl.objects.active = arm
    set_mode(arm, "EDIT")
    try:
        for j, name in enumerate(names):
            if np.abs(moves_b[j]).max() < 1e-9:
                continue
            eb = arm.data.edit_bones.get(name)
            if eb:
                m = eb.matrix.copy()
                m.translation = m.translation + type(m.translation)(moves_b[j])
                eb.matrix = m
    finally:
        set_mode(arm, "OBJECT")
        vl.objects.active = prev


class MFB_OT_expr_match_joints(_ExprOp):
    """Fit the joints to the current neutral face (least squares on skin weights)"""
    bl_idname = "mfb.expr_match_joints"
    bl_label = "Run Joints Matching"

    def execute(self, context):
        arm = _arm(context)
        dna = _dna(arm)
        head = _head_mesh(arm)
        mi = int(head["mfb_mesh_index"])
        space = _space(dna)
        original = read_dna(arm.get("mfb_head_dna_original", arm["mfb_head_dna"]), meshes={mi})
        disp = dna.meshes[mi].positions.astype(np.float64) - original.meshes[mi].positions
        moves = X.match_joints(dna, mi, disp)
        X.apply_joint_moves(dna, moves)
        _move_bones(arm, dna, space, moves)
        ex_commit(context, f"Joints matching moved {int((np.abs(moves).sum(1) > 1e-6).sum())} joints")
        return {"FINISHED"}


class MFB_OT_expr_copy_joints(_ExprOp):
    """Copy the selected expression's joint motion to the expression named in Copy Joints To"""
    bl_idname = "mfb.expr_copy_joints"
    bl_label = "Copy Expression Joints"

    def execute(self, context):
        st = _st(context)
        arm = _arm(context)
        dna = _dna(arm)
        target = next((e for e in st.expressions if e.name == st.copy_target), None)
        if target is None:
            self.report({"ERROR"}, f"No expression named {st.copy_target!r}")
            return {"CANCELLED"}
        n = X.copy_expression_joints(dna, _current(context).control, target.control)
        ex_commit(context, f"Copied {n} joints from {_current(context).name} to {target.name}")
        return {"FINISHED"}


class MFB_OT_expr_apply_skin(_ExprOp):
    """Store the head's vertex groups as the DNA skin weights"""
    bl_idname = "mfb.expr_apply_skin"
    bl_label = "Apply Skin Weights"

    def execute(self, context):
        arm = _arm(context)
        dna = _dna(arm)
        names = {n: j for j, n in enumerate(dna.definition.joint_names)}
        total = 0
        for obj in [o for o in arm.children if o.type == "MESH" and "mfb_mesh_index" in o and o.get("mfb_role") != "body"
                    and (o.select_get() or o == _head_mesh(arm))]:
            mi = int(obj["mfb_mesh_index"])
            gname = {g.index: g.name for g in obj.vertex_groups}
            weights, joints = [], []
            for v in obj.data.vertices:
                w = [(g.weight, names[gname[g.group]]) for g in v.groups if gname[g.group] in names and g.weight > 0]
                weights.append(np.array([x[0] for x in w], np.float32))
                joints.append(np.array([x[1] for x in w], np.uint16))
            X.set_skin_weights(dna, mi, weights, joints)
            total += 1
        ex_commit(context, f"Stored skin weights of {total} meshes")
        return {"FINISHED"}


class MFB_OT_expr_bookmark(_ExprOp):
    """Bookmarks: save the current face, apply one, remove one, or add the built-in presets"""
    bl_idname = "mfb.expr_bookmark"
    bl_label = "Bookmark"
    action: EnumProperty(items=(("ADD", "Add", ""), ("APPLY", "Apply", ""), ("REMOVE", "Remove", ""),
                                ("PRESET_EYES", "Eyes Preset", ""), ("PRESET_FULL", "Full Face Preset", "")))

    def execute(self, context):
        st = _st(context)
        arm = _arm(context)
        root = arm.pose.bones.get(rig.FACE_ROOT)
        rt = rig.runtime(arm, "head")
        if self.action == "ADD":
            b = st.bookmarks.add()
            b.name = st.bookmark_name or f"Bookmark {len(st.bookmarks)}"
            b.values = json.dumps({a: float(root[a]) for a in rt.raw_attrs if "_q" not in a and root.get(a, 0)})
        elif self.action == "APPLY" and st.bookmarks:
            _set_raw(arm, json.loads(st.bookmarks[st.bookmark_index].values))
        elif self.action == "REMOVE" and st.bookmarks:
            st.bookmarks.remove(st.bookmark_index)
            st.bookmark_index = max(0, st.bookmark_index - 1)
        elif self.action.startswith("PRESET"):
            graph = _graph(arm)
            for c, e in graph.items():
                if e.is_psd or (self.action == "PRESET_EYES" and not any(w in e.name.lower() for w in EYE_WORDS)):
                    continue
                b = st.bookmarks.add()
                b.name = e.name
                b.values = json.dumps(_expression_values(arm, graph, c))
        return {"FINISHED"}


class MFB_OT_expr_bookmark_file(_ExprOp):
    """Export or import the bookmarks as a JSON file"""
    bl_idname = "mfb.expr_bookmark_file"
    bl_label = "Bookmarks File"
    export: BoolProperty(default=True)
    filepath: StringProperty(subtype="FILE_PATH")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        st = _st(context)
        if self.export:
            Path(self.filepath).write_text(json.dumps([{"name": b.name, "values": json.loads(b.values)}
                                                       for b in st.bookmarks], indent=1), encoding="utf-8")
        else:
            for item in json.loads(Path(self.filepath).read_text(encoding="utf-8")):
                b = st.bookmarks.add(); b.name = item["name"]; b.values = json.dumps(item["values"])
        return {"FINISHED"}


class MFB_OT_expr_rom(_ExprOp):
    """Key a range-of-motion test: each expression goes 0 -> 1 -> 0 in turn (Raw mode keys)"""
    bl_idname = "mfb.expr_rom"
    bl_label = "Range-of-Motion Animation"
    eyes_only: BoolProperty(name="Eyes Only", default=False)

    def execute(self, context):
        st = _st(context)
        arm = _arm(context)
        graph = _graph(arm)
        root = arm.pose.bones.get(rig.FACE_ROOT)
        rt = rig.runtime(arm, "head")
        arm[rig.PROP_DRIVE] = "RAW"
        attrs = [a for a in rt.raw_attrs if "_q" not in a]
        exprs = [e for c, e in sorted(graph.items()) if not e.is_psd
                 and (not self.eyes_only or any(w in e.name.lower() for w in EYE_WORDS))]
        frame, step = context.scene.frame_start, st.rom_step
        for a in attrs:
            root[a] = 0.0
            root.keyframe_insert(f'["{a}"]', frame=frame, group="ROM")
        for e in exprs:
            for f, v in ((frame, 0.0), (frame + step // 2, 1.0), (frame + step, 0.0)):
                root[e.name] = v
                root.keyframe_insert(f'["{e.name}"]', frame=f, group="ROM")
            frame += step
        context.scene.frame_end = frame
        st.status = f"Keyed {len(exprs)} expressions over {frame - context.scene.frame_start} frames"
        return {"FINISHED"}


class MFB_OT_expr_settings_file(_ExprOp):
    """Save or load the editor settings (mode, propagation, locks, colours, bookmarks)"""
    bl_idname = "mfb.expr_settings_file"
    bl_label = "Settings File"
    save: BoolProperty(default=True)
    filepath: StringProperty(subtype="FILE_PATH")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        st = _st(context)
        if self.save:
            data = {"mode": st.mode, "propagation": st.propagation, "strength": st.strength,
                    "match_joints": st.match_joints, "transfer_lods": st.transfer_lods, "rom_step": st.rom_step,
                    "locked": [e.name for e in st.expressions if e.locked],
                    "colours": {e.name: e.colour for e in st.expressions if e.colour != "NONE"},
                    "bookmarks": [{"name": b.name, "values": json.loads(b.values)} for b in st.bookmarks]}
            Path(self.filepath).write_text(json.dumps(data, indent=1), encoding="utf-8")
        else:
            data = json.loads(Path(self.filepath).read_text(encoding="utf-8"))
            for k in ("mode", "propagation", "strength", "match_joints", "transfer_lods", "rom_step"):
                if k in data:
                    setattr(st, k, data[k])
            for e in st.expressions:
                e.locked = e.name in data.get("locked", [])
                e.colour = data.get("colours", {}).get(e.name, "NONE")
            st.bookmarks.clear()
            for item in data.get("bookmarks", []):
                b = st.bookmarks.add(); b.name = item["name"]; b.values = json.dumps(item["values"])
        return {"FINISHED"}


class MFB_OT_expr_save_dna(_ExprOp):
    """Write the edited head DNA (as it is in memory) to a file"""
    bl_idname = "mfb.expr_save_dna"
    bl_label = "Save MetaHuman DNA"

    def execute(self, context):
        st = _st(context)
        path = bpy.path.abspath(st.file_path)
        if not path.lower().endswith(".dna"):
            self.report({"ERROR"}, "Set File to a .dna path")
            return {"CANCELLED"}
        write_dna(_dna(_arm(context)), path)
        self.report({"INFO"}, f"Saved {Path(path).name}")
        return {"FINISHED"}


class MFB_OT_expr_export_fbx(_ExprOp):
    """Export the head (meshes, skeleton, shape keys) as FBX, Y-up or Z-up"""
    bl_idname = "mfb.expr_export_fbx"
    bl_label = "Export FBX"

    def execute(self, context):
        st = _st(context)
        arm = _arm(context)
        path = bpy.path.abspath(st.file_path)
        if not path.lower().endswith(".fbx"):
            self.report({"ERROR"}, "Set File to a .fbx path")
            return {"CANCELLED"}
        objs = [arm, *[o for o in arm.children if o.type == "MESH" and o.visible_get()]]
        from .ctx import window_override
        with context.temp_override(**window_override(), selected_objects=objs, active_object=arm):
            bpy.ops.export_scene.fbx(filepath=path, use_selection=True, object_types={"ARMATURE", "MESH"},
                                     axis_up=st.export_up, axis_forward="-Z" if st.export_up == "Y" else "Y",
                                     add_leaf_bones=False, use_armature_deform_only=False, bake_anim=False,
                                     global_scale=1.0, apply_unit_scale=True)
        self.report({"INFO"}, f"Exported {Path(path).name} ({st.export_up}-up)")
        return {"FINISHED"}


# --------------------------------------------------------------------------- UI


class MFB_UL_expressions(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        st = _st(context)
        row = layout.row(align=True)
        ic = COLOUR_ICON.get(item.colour, "BLANK1") if item.colour != "NONE" else (
            "LAYER_ACTIVE" if item.marked and st.highlight else ("NODETREE" if item.is_psd else "SHAPEKEY_DATA"))
        row.label(text=item.name, icon=ic)
        row.prop(item, "locked", text="", icon="LOCKED" if item.locked else "UNLOCKED", emboss=False)

    def filter_items(self, context, data, propname):
        st = _st(context)
        items = getattr(data, propname)
        flt = []
        for it in items:
            show = (st.filter_psd or not it.is_psd) and (not st.isolate or it.marked)
            if self.filter_name and self.filter_name.lower() not in it.name.lower():
                show = False
            flt.append(self.bitflag_filter_item if show else 0)
        return flt, []


class MFB_UL_bookmarks(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon="BOOKMARKS")


class MFB_PT_expression(bpy.types.Panel):
    bl_label = "Expression Editor"
    bl_idname = "MFB_PT_expression"
    bl_ui_units_x = 18          # width when opened as a floating panel from the menu
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MetaHuman"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        st = _st(context)
        layout = self.layout
        row = layout.row(align=True)
        row.prop(st, "armature", text="")
        row.operator("mfb.expr_load", text="", icon="FILE_FOLDER")
        row.operator("mfb.expr_refresh", text="", icon="FILE_REFRESH")
        if _arm(context) is None:
            layout.label(text="Load or pick an assembled head")
            return
        layout.prop(st, "mode", expand=True)
        if st.status:
            layout.label(text=st.status, icon="INFO")
        if st.mode == "NEUTRAL":
            layout.label(text="Edit or sculpt the head's Basis, then:")
            col = layout.column(align=True)
            col.prop(st, "match_joints"); col.prop(st, "transfer_lods")
            layout.operator("mfb.expr_apply_neutral", icon="CHECKMARK")
        elif st.mode == "SKIN":
            layout.label(text="Weight-paint the head (and selected meshes), then:")
            layout.operator("mfb.expr_apply_skin", icon="MOD_VERTEX_WEIGHT")
        elif st.mode == "JOINTS":
            layout.operator("mfb.expr_match_joints", icon="BONE_DATA")
            row = layout.row(align=True)
            row.prop(st, "copy_target")
            row.operator("mfb.expr_copy_joints", text="Copy", icon="COPYDOWN")
        layout.template_list("MFB_UL_expressions", "", st, "expressions", st, "expr_index", rows=8)
        row = layout.row(align=True)
        row.prop(st, "filter_psd", toggle=True)
        row.prop(st, "isolate", toggle=True, text="Isolate")
        row.prop(st, "highlight", toggle=True, text="Highlight")
        row = layout.row(align=True)
        for d, label in (("UP", "Upstream"), ("DOWN", "Downstream"), ("BOTH", "Both")):
            row.operator("mfb.expr_branch", text=label).direction = d
        if st.expressions:
            e = st.expressions[st.expr_index]
            row = layout.row(align=True)
            row.prop(e, "colour", text="")
            row.operator("mfb.expr_lock", icon="LOCKED")
        row = layout.row(align=True)
        row.operator("mfb.expr_preview", icon="HIDE_OFF")
        row.operator("mfb.expr_neutral", icon="LOOP_BACK")
        if st.mode == "EXPRESSION":
            box = layout.box()
            row = box.row(align=True)
            row.operator("mfb.expr_sculpt", icon="SCULPTMODE_HLT")
            row.operator("mfb.expr_apply_sculpt", icon="CHECKMARK")
            box.prop(st, "keep_sculpt")
            box.prop(st, "propagation")
            box.prop(st, "strength", slider=True)
            row = box.row(align=True)
            row.operator("mfb.expr_previews", text="Preview Downstream").selected_only = True
            op = row.operator("mfb.expr_previews", text="Unlocked Only"); op.unlocked_only = True
            row.operator("mfb.expr_delete_previews", text="", icon="TRASH")


class MFB_PT_expression_helpers(bpy.types.Panel):
    bl_label = "Bookmarks, Animation and Files"
    bl_parent_id = "MFB_PT_expression"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MetaHuman"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        st = _st(context)
        layout = self.layout
        layout.template_list("MFB_UL_bookmarks", "", st, "bookmarks", st, "bookmark_index", rows=4)
        layout.prop(st, "bookmark_name")
        row = layout.row(align=True)
        for a, label in (("ADD", "Add"), ("APPLY", "Apply"), ("REMOVE", "Remove")):
            row.operator("mfb.expr_bookmark", text=label).action = a
        row = layout.row(align=True)
        row.operator("mfb.expr_bookmark", text="Eyes Preset").action = "PRESET_EYES"
        row.operator("mfb.expr_bookmark", text="Full Preset").action = "PRESET_FULL"
        row = layout.row(align=True)
        row.operator("mfb.expr_bookmark_file", text="Export Bookmarks").export = True
        row.operator("mfb.expr_bookmark_file", text="Import Bookmarks").export = False
        row = layout.row(align=True)
        row.prop(st, "rom_step")
        row.operator("mfb.expr_rom", icon="ANIM")
        row = layout.row(align=True)
        row.operator("mfb.expr_settings_file", text="Save Settings").save = True
        row.operator("mfb.expr_settings_file", text="Load Settings").save = False
        layout.prop(st, "file_path")
        row = layout.row(align=True)
        row.operator("mfb.expr_save_dna", icon="FILE_TICK")
        row.prop(st, "export_up", text="")
        row.operator("mfb.expr_export_fbx", icon="EXPORT")
        layout.operator("mfb.dna_upgrade", text="Upgrade MetaHuman DNA File", icon="FILE_REFRESH")


CLASSES = (MFB_ExprItem, MFB_Bookmark, MFB_ExprState, MFB_OT_expr_load, MFB_OT_expr_refresh, MFB_OT_expr_preview,
           MFB_OT_expr_neutral, MFB_OT_expr_sculpt, MFB_OT_expr_apply_sculpt, MFB_OT_expr_previews,
           MFB_OT_expr_delete_previews, MFB_OT_expr_branch, MFB_OT_expr_lock, MFB_OT_expr_apply_neutral,
           MFB_OT_expr_match_joints, MFB_OT_expr_copy_joints, MFB_OT_expr_apply_skin, MFB_OT_expr_bookmark,
           MFB_OT_expr_bookmark_file, MFB_OT_expr_rom, MFB_OT_expr_settings_file, MFB_OT_expr_save_dna,
           MFB_OT_expr_export_fbx, MFB_UL_expressions, MFB_UL_bookmarks, MFB_PT_expression, MFB_PT_expression_helpers)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mfb_expr = PointerProperty(type=MFB_ExprState)


def unregister():
    del bpy.types.Scene.mfb_expr
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
