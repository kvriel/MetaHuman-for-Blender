"""Build Blender data (armature, meshes, skin weights, shape keys) from a read DNA."""
from __future__ import annotations

from dataclasses import dataclass, field

import bpy
import numpy as np
from mathutils import Matrix

from .ctx import set_mode
from ..core.skeleton import local_matrices, world_matrices
from ..core.space import Space
from ..dna.reader import DNA

# Custom properties that tie Blender objects back to the DNA they came from.
PROP_DNA_PATH = "mfb_dna_path"
PROP_ROLE = "mfb_role"            # "head", "body", "combined"
PROP_MESH_INDEX = "mfb_mesh_index"


@dataclass
class BuildOptions:
    lods: set[int] = field(default_factory=lambda: {0})
    mesh_filter: set[str] | None = None   # mesh names to import; None = all in chosen LODs
    blend_shapes: bool = True
    skin: bool = True
    normals: bool = True
    metres: bool = True


# --------------------------------------------------------------------------- collections


def ensure_collection(name: str, parent: bpy.types.Collection) -> bpy.types.Collection:
    coll = parent.children.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        parent.children.link(coll)
    return coll


# --------------------------------------------------------------------------- armature


def joint_rest_matrices(dna: DNA, space: Space) -> np.ndarray:
    d = dna.definition
    local = local_matrices(d.neutral_translations, d.neutral_rotations,
                           degrees=dna.descriptor.rotation_unit == "degrees")
    return space.world_matrices(world_matrices(local, d.joint_parents))


def _bone_lengths(world: np.ndarray, parents: np.ndarray, scale: float) -> np.ndarray:
    heads = world[:, :3, 3]
    lengths = np.full(len(world), 1.0 * scale)
    first_child: dict[int, int] = {}
    for j, p in enumerate(parents):
        if p != j and int(p) not in first_child:
            first_child[int(p)] = j
    for p, c in first_child.items():
        lengths[p] = np.linalg.norm(heads[c] - heads[p])
    return np.clip(lengths, 0.2 * scale, 5.0 * scale)


def build_armature(dna: DNA, space: Space, name: str, collection: bpy.types.Collection,
                   joint_subset: np.ndarray | None = None) -> bpy.types.Object:
    """Armature whose bone rest frames equal the DNA joint frames exactly."""
    d = dna.definition
    world = joint_rest_matrices(dna, space)
    # Bone lengths are clamped in centimetres, expressed in Blender units.
    cm = space.s * (100.0 if dna.descriptor.translation_unit == "m" else 1.0)
    lengths = _bone_lengths(world, d.joint_parents, cm)

    arm_data = bpy.data.armatures.new(name)
    arm_data.display_type = "OCTAHEDRAL"
    arm_obj = bpy.data.objects.new(name, arm_data)
    collection.objects.link(arm_obj)
    arm_obj[PROP_DNA_PATH] = str(dna.path)

    view_layer = bpy.context.view_layer
    prev_active = view_layer.objects.active
    view_layer.objects.active = arm_obj
    set_mode(arm_obj, "EDIT")
    try:
        bones = arm_data.edit_bones
        keep = set(range(len(d.joint_names))) if joint_subset is None else set(int(j) for j in joint_subset)
        made: dict[int, bpy.types.EditBone] = {}
        for j, jname in enumerate(d.joint_names):
            if j not in keep:
                continue
            eb = bones.new(jname)
            eb.length = float(lengths[j])
            eb.matrix = Matrix(world[j].tolist())
            made[j] = eb
        for j, eb in made.items():
            p = int(d.joint_parents[j])
            if p != j and p in made:
                eb.parent = made[p]
    finally:
        set_mode(arm_obj, "OBJECT")
        view_layer.objects.active = prev_active
    organize_bones(arm_obj)
    return arm_obj


def build_combined_armature(parts: list[tuple[DNA, Space]], name: str,
                            collection: bpy.types.Collection) -> bpy.types.Object:
    """One armature holding the joints of several DNAs, joined by joint name.

    The first DNA wins for joints that appear in more than one (pass the body first).
    Each joint keeps its own DNA's frame; parents are looked up by name.
    """
    joints: dict[str, tuple[np.ndarray, str | None, float]] = {}
    for dna, space in parts:
        d = dna.definition
        world = joint_rest_matrices(dna, space)
        cm = space.s * (100.0 if dna.descriptor.translation_unit == "m" else 1.0)
        lengths = _bone_lengths(world, d.joint_parents, cm)
        for j, jname in enumerate(d.joint_names):
            if jname in joints:
                continue
            p = int(d.joint_parents[j])
            joints[jname] = (world[j], None if p == j else d.joint_names[p], float(lengths[j]))

    arm_data = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm_data)
    collection.objects.link(arm_obj)
    vl = bpy.context.view_layer
    prev = vl.objects.active
    vl.objects.active = arm_obj
    set_mode(arm_obj, "EDIT")
    try:
        made = {}
        for jname, (m, _, length) in joints.items():
            eb = arm_data.edit_bones.new(jname)
            eb.length = length
            eb.matrix = Matrix(m.tolist())
            made[jname] = eb
        for jname, (_, parent, _) in joints.items():
            if parent and parent in made:
                made[jname].parent = made[parent]
    finally:
        set_mode(arm_obj, "OBJECT")
        vl.objects.active = prev
    organize_bones(arm_obj)
    return arm_obj


FACE_JOINT_PREFIX = "FACIAL_"


def organize_bones(arm_obj: bpy.types.Object) -> None:
    """Body bones in a visible collection; the many face joints in a hidden one (the board drives them)."""
    arm = arm_obj.data
    body = arm.collections.new("Body")
    face = arm.collections.new("Face Joints")
    for bone in arm.bones:
        (face if bone.name.startswith(FACE_JOINT_PREFIX) else body).assign(bone)
    face.is_visible = False
    if not body.bones:
        arm.collections.remove(body)


def follow_bones(follower: bpy.types.Object, leader: bpy.types.Object) -> int:
    """Make same-named bones of `follower` copy `leader`'s pose (split skeletons)."""
    count = 0
    for pb in follower.pose.bones:
        if pb.name in leader.pose.bones:
            con = pb.constraints.new("COPY_TRANSFORMS")
            con.name = "mfb_follow_body"
            con.target = leader
            con.subtarget = pb.name
            count += 1
    return count


# --------------------------------------------------------------------------- meshes


def build_mesh(dna: DNA, space: Space, mesh_index: int, collection: bpy.types.Collection,
               armature: bpy.types.Object | None, opts: BuildOptions) -> bpy.types.Object:
    d = dna.definition
    src = dna.meshes[mesh_index]
    name = d.mesh_names[mesh_index]

    me = bpy.data.meshes.new(name)
    positions = space.points(src.positions).astype(np.float32)
    me.vertices.add(len(positions))
    me.vertices.foreach_set("co", positions.ravel())

    corner_layout = np.concatenate(src.faces) if src.faces else np.zeros(0, np.uint32)
    sizes = np.fromiter((len(f) for f in src.faces), dtype=np.int32, count=len(src.faces))
    starts = np.zeros(len(sizes), np.int32)
    if len(sizes):
        starts[1:] = np.cumsum(sizes)[:-1]

    me.loops.add(len(corner_layout))
    me.loops.foreach_set("vertex_index", src.layout_positions[corner_layout].astype(np.int32))
    me.polygons.add(len(sizes))
    me.polygons.foreach_set("loop_start", starts)
    me.update(calc_edges=True)

    if len(src.uvs):
        uv = me.uv_layers.new(name="UVMap")
        uv.data.foreach_set("uv", src.uvs[src.layout_uvs[corner_layout]].astype(np.float32).ravel())

    me.polygons.foreach_set("use_smooth", np.ones(len(sizes), dtype=bool))
    if opts.normals and len(src.normals):
        loop_normals = space.directions(src.normals[src.layout_normals[corner_layout]])
        me.normals_split_custom_set(loop_normals.astype(np.float32).tolist())
    me.validate(clean_customdata=False)

    obj = bpy.data.objects.new(name, me)
    collection.objects.link(obj)
    obj[PROP_DNA_PATH] = str(dna.path)
    obj[PROP_MESH_INDEX] = mesh_index

    if armature is not None:
        obj.parent = armature
        if opts.skin:
            _apply_skin(obj, src, d.joint_names)
            mod = obj.modifiers.new("Armature", "ARMATURE")
            mod.object = armature

    if opts.blend_shapes and src.blend_shapes:
        _apply_blend_shapes(obj, src, space, d.blend_shape_channel_names, positions)
    return obj


def _apply_skin(obj, src, joint_names) -> None:
    groups: dict[int, bpy.types.VertexGroup] = {}
    for v, (weights, joints) in enumerate(zip(src.skin_weights, src.skin_joints)):
        for w, j in zip(weights.tolist(), joints.tolist()):
            if w <= 0.0:
                continue
            vg = groups.get(j)
            if vg is None:
                vg = groups[j] = obj.vertex_groups.new(name=joint_names[j])
            vg.add((v,), w, "REPLACE")


SHAPE_CHANNELS = "mfb_shape_channels"     # mesh object: {shape key name: DNA blend shape channel}


def _apply_blend_shapes(obj, src, space, channel_names, base_positions) -> None:
    obj.shape_key_add(name="Basis", from_mix=False)
    channels: dict[str, int] = {}
    for target in src.blend_shapes:
        co = base_positions.copy()
        if len(target.vertex_indices):
            co[target.vertex_indices] += space.directions(target.deltas).astype(np.float32) * space.s
        key = obj.shape_key_add(name=channel_names[target.channel], from_mix=False)
        key.data.foreach_set("co", co.ravel())
        # Blender cuts names to 63 bytes (and renames clashes), so remember each key's channel by key name
        channels[key.name] = int(target.channel)
    obj.data.shape_keys.use_relative = True
    obj[SHAPE_CHANNELS] = channels
