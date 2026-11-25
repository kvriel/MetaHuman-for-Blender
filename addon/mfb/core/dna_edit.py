"""Editing operations on a read DNA (numpy only, no bpy): our counterpart of DNACalib commands.

Control index space (one vector shared by every stage):
    [raw controls | PSD controls | ML controls | RBF pose controls]
Inserting or removing controls shifts later indices, so every reference is remapped.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass

import numpy as np

from ..dna.reader import (DNA, JointGroup, LODMapping, RBFBehavior, RBFBehaviorExt, RBFPose, RBFPoseExt,
                          RBFSolver, TwistSwing, TwistSwingBehavior)

ATTRS = 9                     # tx ty tz rx ry rz sx sy sz per joint


# --------------------------------------------------------------------------- control index space


def raw_count(dna: DNA) -> int:
    return len(dna.definition.raw_control_names)


def psd_base(dna: DNA) -> int:
    return raw_count(dna)


def ml_base(dna: DNA) -> int:
    return raw_count(dna) + dna.behavior.psd_count


def rbf_base(dna: DNA) -> int:
    return ml_base(dna) + (len(dna.ml.control_names) if dna.ml else 0)


def ensure_rbf_layers(dna: DNA) -> None:
    lods = dna.descriptor.lod_count
    if dna.rbf is None:
        dna.rbf = RBFBehavior(LODMapping(np.zeros(lods, np.uint16), [np.zeros(0, np.uint16)]), [], [])
    if dna.rbf_ext is None or (not dna.rbf_ext.poses and dna.rbf.poses):
        base = rbf_base(dna)
        dna.rbf_ext = RBFBehaviorExt([p.name for p in dna.rbf.poses],
                                     [RBFPoseExt(np.zeros(0, np.uint16), np.array([base + i], np.uint16),
                                                 np.ones(1, np.float32)) for i in range(len(dna.rbf.poses))])
    if dna.twist_swing is None:
        dna.twist_swing = TwistSwingBehavior([], [])
    for lid, ver in (("rbfb", 0x10000), ("rbfe", 0x10000), ("twsw", 0x10000), ("jbmd", 0x10000)):
        if lid not in {l for l, _ in dna.layer_order}:
            dna.layer_order.append((lid, ver))
    if dna.version < (2, 5):
        dna.version = (2, 5)


def remap_controls(dna: DNA, mapping) -> None:
    """Apply an old->new control index function (returns -1 to drop) to every reference."""
    b = dna.behavior
    m = np.vectorize(mapping, otypes=[np.int64]) if not isinstance(mapping, np.ndarray) else None

    def f(a):
        a = np.asarray(a, np.int64)
        return (mapping[a] if m is None else m(a)) if len(a) else a

    # PSD matrix: rows are outputs, columns inputs; drop entries whose row or column vanished
    rows, cols = f(b.psd_rows), f(b.psd_columns)
    keep = (rows >= 0) & (cols >= 0)
    b.psd_rows, b.psd_columns, b.psd_values = rows[keep].astype(np.uint16), cols[keep].astype(np.uint16), b.psd_values[keep]
    for g in b.joint_groups:
        inp = f(g.input_indices)
        keep = inp >= 0
        g.input_indices = inp[keep].astype(np.uint16)
        g.values = g.values[:, keep]
    bi = f(b.blend_shape_inputs)
    keep = bi >= 0
    # blend shape lods count entries in order; drop removed entries and shrink LOD counts
    removed_before = np.cumsum(~keep)
    b.blend_shape_lods = np.array([n - (removed_before[n - 1] if n else 0) for n in b.blend_shape_lods], np.uint16)
    b.blend_shape_inputs, b.blend_shape_outputs = bi[keep].astype(np.uint16), b.blend_shape_outputs[keep]
    am = b.animated_maps
    ai = f(am.input_indices)
    keep = ai >= 0
    removed_before = np.cumsum(~keep)
    b.animated_map_lods = np.array([n - (removed_before[n - 1] if n else 0) for n in b.animated_map_lods], np.uint16)
    for name in ("output_indices", "from_values", "to_values", "slope_values", "cut_values"):
        setattr(am, name, getattr(am, name)[keep])
    am.input_indices = ai[keep].astype(np.uint16)
    if dna.rbf:
        for s in dna.rbf.solvers:
            s.raw_control_indices = f(s.raw_control_indices).astype(np.uint16)
    if dna.rbf_ext:
        for p in dna.rbf_ext.poses:
            p.input_control_indices = f(p.input_control_indices).astype(np.uint16)
            p.output_control_indices = f(p.output_control_indices).astype(np.uint16)
    if dna.twist_swing:
        for t in (*dna.twist_swing.twists, *dna.twist_swing.swings):
            t.input_control_indices = f(t.input_control_indices).astype(np.uint16)
    if dna.ml:
        for n in dna.ml.networks:
            n.input_indices = f(n.input_indices).astype(np.uint16)
            n.output_indices = f(n.output_indices).astype(np.uint16)
    update_joint_col_count(dna)


def control_total(dna: DNA) -> int:
    """Size of the control vector: raw + PSD + ML + RBF pose controls."""
    poses = len(dna.rbf_ext.poses) if dna.rbf_ext and dna.rbf_ext.poses else (len(dna.rbf.poses) if dna.rbf else 0)
    return rbf_base(dna) + poses


def update_joint_col_count(dna: DNA) -> None:
    """Epic's files do not keep joint_col_count as an upper bound (RBF pose columns lie past it),
    so the stored value is left as it is; RigLogic sizes the control vector from the counts."""
    return


def insert_controls(dna: DNA, at: int, count: int) -> None:
    """Open `count` control slots at index `at`; references >= at move up."""
    remap_controls(dna, lambda i: i + count if i >= at else i)


def remove_control(dna: DNA, index: int) -> None:
    remap_controls(dna, lambda i: -1 if i == index else (i - 1 if i > index else i))


def ensure_quat_raw_controls(dna: DNA, joint: str) -> list[int]:
    """Raw controls 'joint.qx..qw' (driver rotation inputs); created if missing."""
    names = dna.definition.raw_control_names
    want = [f"{joint}.q{c}" for c in "xyzw"]
    if all(w in names for w in want):
        return [names.index(w) for w in want]
    at = raw_count(dna)
    insert_controls(dna, at, 4)
    # GUI->raw table outputs are raw indices below `at`: unchanged. PSD outputs moved by remap.
    names.extend(want)
    return list(range(at, at + 4))


# --------------------------------------------------------------------------- joint matrix columns


def joint_column(dna: DNA, control: int) -> dict[int, np.ndarray]:
    """Joint deltas a control produces at value 1: {joint: 9 values}."""
    out: dict[int, np.ndarray] = {}
    for g in dna.behavior.joint_groups:
        hit = np.nonzero(g.input_indices == control)[0]
        if not len(hit):
            continue
        col = g.values[:, hit[0]]
        for row, attr in enumerate(g.output_indices):
            j, k = divmod(int(attr), ATTRS)
            out.setdefault(j, np.zeros(ATTRS, np.float32))[k] = col[row]
    return out


def _group_for_joint(dna: DNA, joint: int) -> JointGroup:
    for g in dna.behavior.joint_groups:
        if joint in set(int(j) for j in g.joint_indices):
            return g
    lods = dna.descriptor.lod_count
    g = JointGroup(np.zeros(lods, np.uint16), np.zeros(0, np.uint16), np.zeros(0, np.uint16),
                   np.zeros((0, 0), np.float32), np.array([joint], np.uint16))
    dna.behavior.joint_groups.append(g)
    return g


def set_joint_column(dna: DNA, control: int, deltas: dict[int, np.ndarray], tolerance: float = 1e-6) -> None:
    """Make `control` produce exactly `deltas` (joint -> 9 values); other joints get 0 from it."""
    for g in dna.behavior.joint_groups:            # clear the control everywhere first
        hit = np.nonzero(g.input_indices == control)[0]
        if len(hit):
            g.values[:, hit[0]] = 0.0
    for joint, vals in deltas.items():
        vals = np.asarray(vals, np.float32)
        if np.all(np.abs(vals) < tolerance):
            continue
        g = _group_for_joint(dna, int(joint))
        hit = np.nonzero(g.input_indices == control)[0]
        if not len(hit):
            g.input_indices = np.append(g.input_indices, control).astype(np.uint16)
            g.values = np.concatenate([g.values, np.zeros((len(g.output_indices), 1), np.float32)], axis=1)
            col = len(g.input_indices) - 1
        else:
            col = hit[0]
        for k in range(ATTRS):
            if abs(vals[k]) < tolerance:
                continue
            attr = int(joint) * ATTRS + k
            rows = np.nonzero(g.output_indices == attr)[0]
            if not len(rows):
                g.output_indices = np.append(g.output_indices, attr).astype(np.uint16)
                g.values = np.concatenate([g.values, np.zeros((1, g.values.shape[1]), np.float32)], axis=0)
                g.lods = np.minimum(g.lods.astype(np.int64) + 1, len(g.output_indices)).astype(np.uint16) \
                    if len(g.lods) else g.lods
                row = len(g.output_indices) - 1
            else:
                row = rows[0]
            g.values[row, col] = vals[k]
        if int(joint) not in set(int(j) for j in g.joint_indices):
            g.joint_indices = np.append(g.joint_indices, joint).astype(np.uint16)
    _drop_empty_columns(dna, control)
    dna.behavior.joint_row_count = max(dna.behavior.joint_row_count, len(dna.definition.joint_names) * ATTRS)
    update_joint_col_count(dna)


def _drop_empty_columns(dna: DNA, control: int) -> None:
    for g in dna.behavior.joint_groups:
        hit = np.nonzero(g.input_indices == control)[0]
        if len(hit) and not np.any(g.values[:, hit[0]]):
            keep = np.ones(len(g.input_indices), bool); keep[hit[0]] = False
            g.input_indices, g.values = g.input_indices[keep], g.values[:, keep]


# --------------------------------------------------------------------------- RBF poses and solvers


def pose_control(dna: DNA, pose: int, create: bool = True) -> int:
    """The control a pose drives. Some files have poses with no output; one is made when `create`."""
    ensure_rbf_layers(dna)
    ext = dna.rbf_ext.poses[pose]
    if len(ext.output_control_indices):
        return int(ext.output_control_indices[0])
    if not create:
        return -1
    used = [int(o) for p in dna.rbf_ext.poses for o in p.output_control_indices]
    control = max(used + [control_total(dna) - 1, rbf_base(dna) - 1]) + 1
    ext.output_control_indices = np.array([control], np.uint16)
    ext.output_control_weights = np.ones(1, np.float32)
    return control


def solver_index(dna: DNA, name: str) -> int:
    return [s.name for s in dna.rbf.solvers].index(name)


def solver_targets(dna: DNA, si: int) -> np.ndarray:
    s = dna.rbf.solvers[si]
    k = max(len(s.raw_control_indices), 1)
    return np.asarray(s.raw_control_values, np.float32).reshape(-1, k)


def add_pose(dna: DNA, si: int, name: str, target: np.ndarray, deltas: dict[int, np.ndarray] | None = None,
             scale: float = 1.0) -> int:
    """New RBF pose on solver si with a driver target and driven joint deltas. Returns pose index."""
    ensure_rbf_layers(dna)
    r, ext = dna.rbf, dna.rbf_ext
    control = rbf_base(dna) + len(ext.poses)
    r.poses.append(RBFPose(name, float(scale)))
    ext.poses.append(RBFPoseExt(np.zeros(0, np.uint16), np.array([control], np.uint16), np.ones(1, np.float32)))
    ext.pose_control_names.append(name)
    pi = len(r.poses) - 1
    s = r.solvers[si]
    s.pose_indices = np.append(s.pose_indices, pi).astype(np.uint16)
    s.raw_control_values = np.concatenate([np.asarray(s.raw_control_values, np.float32),
                                           np.asarray(target, np.float32).ravel()])
    update_joint_col_count(dna)
    if deltas:
        set_joint_column(dna, control, deltas)
    return pi


def remove_pose(dna: DNA, pi: int) -> None:
    ensure_rbf_layers(dna)
    control = pose_control(dna, pi, create=False)
    for s in dna.rbf.solvers:
        pos = np.nonzero(s.pose_indices == pi)[0]
        if len(pos):
            k = max(len(s.raw_control_indices), 1)
            t = solver_targets(dna, dna.rbf.solvers.index(s))
            t = np.delete(t, pos[0], axis=0)
            s.raw_control_values = t.ravel()
            s.pose_indices = np.delete(s.pose_indices, pos[0])
        s.pose_indices = np.array([p - 1 if p > pi else p for p in s.pose_indices], np.uint16)
    del dna.rbf.poses[pi]
    del dna.rbf_ext.poses[pi]
    del dna.rbf_ext.pose_control_names[pi]
    if control >= 0 and not any(control in p.output_control_indices for p in dna.rbf_ext.poses):
        remove_control(dna, control)


def rename_pose(dna: DNA, pi: int, name: str) -> None:
    ensure_rbf_layers(dna)
    dna.rbf.poses[pi].name = name
    dna.rbf_ext.pose_control_names[pi] = name


def add_solver(dna: DNA, name: str, driver_joints: list[str], *, solver_type=1, distance_method=1, function_type=0,
               radius=45.0, automatic_radius=0, weight_threshold=0.001, normalize_method=1, twist_axis=0) -> int:
    """New RBF solver driven by joint rotations, with a 'default' (rest) pose."""
    ensure_rbf_layers(dna)
    raw = []
    for j in driver_joints:
        raw += ensure_quat_raw_controls(dna, j)
    s = RBFSolver(name, np.array(raw, np.uint16), np.zeros(0, np.uint16), np.zeros(0, np.float32), radius,
                  weight_threshold, solver_type, automatic_radius, distance_method, normalize_method, function_type,
                  twist_axis)
    dna.rbf.solvers.append(s)
    si = len(dna.rbf.solvers) - 1
    lods = dna.rbf.lod_solvers
    if not lods.indices:
        lods.indices = [np.zeros(0, np.uint16)]
        lods.lods = np.zeros(dna.descriptor.lod_count, np.uint16)
    for row in set(int(x) for x in lods.lods):            # active on every LOD row
        lods.indices[row] = np.append(lods.indices[row], si).astype(np.uint16)
    add_pose(dna, si, f"{name}_default", np.tile(np.float32([0, 0, 0, 1]), len(driver_joints)))
    return si


def remove_solver(dna: DNA, si: int) -> None:
    for pi in sorted((int(p) for p in dna.rbf.solvers[si].pose_indices), reverse=True):
        remove_pose(dna, pi)
    del dna.rbf.solvers[si]
    lods = dna.rbf.lod_solvers
    lods.indices = [np.array([i - 1 if i > si else i for i in row if i != si], np.uint16) for row in lods.indices]


# --------------------------------------------------------------------------- twist / swing


def add_twist_setup(dna: DNA, driver: str, outputs: list[str], weights: list[float] | None = None,
                    axis: int = 0, swing: bool = False) -> TwistSwing:
    """Twist (or swing) joints following a fraction of the driver joint's rotation."""
    ensure_rbf_layers(dna)
    names = dna.definition.joint_names
    raw = ensure_quat_raw_controls(dna, driver)
    if weights is None:                       # spread evenly: nearest the driver follows most
        n = len(outputs)
        weights = [(i + 1) / (n + 1) for i in range(n)][::-1]
    setup = TwistSwing(np.array(weights, np.float32), np.array([names.index(o) for o in outputs], np.uint16),
                       np.array(raw, np.uint16), int(axis))
    (dna.twist_swing.swings if swing else dna.twist_swing.twists).append(setup)
    return setup


# --------------------------------------------------------------------------- names


def find_replace(dna: DNA, pattern: str, replacement: str, *, regex: bool = False, solvers: bool = True,
                 poses: bool = True) -> int:
    """Rename RBF solvers and poses; returns how many names changed."""
    changed = 0
    sub = (lambda s: re.sub(pattern, replacement, s)) if regex else (lambda s: s.replace(pattern, replacement))
    if dna.rbf and solvers:
        for s in dna.rbf.solvers:
            new = sub(s.name)
            changed += new != s.name
            s.name = new
    if dna.rbf and poses:
        ensure_rbf_layers(dna)
        for i, p in enumerate(dna.rbf.poses):
            new = sub(p.name)
            if new != p.name:
                rename_pose(dna, i, new)
                changed += 1
    return changed


# --------------------------------------------------------------------------- validation


@dataclass
class Message:
    level: str        # "ERROR", "WARNING", "INFO"
    text: str


def validate_riglogic(dna: DNA) -> list[Message]:
    """Structural checks RigLogic relies on."""
    msgs: list[Message] = []
    d, b = dna.definition, dna.behavior
    jcount, total = len(d.joint_names), control_total(dna)
    seen_outputs: dict[int, int] = {}
    for gi, g in enumerate(b.joint_groups):
        if g.values.shape != (len(g.output_indices), len(g.input_indices)):
            msgs.append(Message("ERROR", f"Joint group {gi}: value matrix does not match its rows and columns"))
        for o in g.output_indices:
            if int(o) in seen_outputs:
                msgs.append(Message("ERROR", f"Joint attribute {d.joint_names[int(o) // ATTRS]}.{int(o) % ATTRS} "
                                             f"is driven by joint groups {seen_outputs[int(o)]} and {gi}"))
            seen_outputs[int(o)] = gi
            if int(o) // ATTRS >= jcount:
                msgs.append(Message("ERROR", f"Joint group {gi} writes to joint {int(o) // ATTRS}, which does not exist"))
        if len(g.input_indices) and int(g.input_indices.max()) >= max(total, 1):
            msgs.append(Message("ERROR", f"Joint group {gi} reads control {int(g.input_indices.max())}, "
                                         f"beyond the {total} controls"))
        if len(g.lods) and int(g.lods.max()) > len(g.output_indices):
            msgs.append(Message("ERROR", f"Joint group {gi}: LOD row count exceeds its rows"))
    parents = d.joint_parents
    for j, p in enumerate(parents):
        if int(p) >= jcount:
            msgs.append(Message("ERROR", f"Joint {d.joint_names[j]} has a parent index out of range"))
    if len(set(d.joint_names)) != len(d.joint_names):
        msgs.append(Message("ERROR", "Joint names are not unique"))
    if dna.rbf:
        ext = dna.rbf_ext
        if ext and ext.poses and len(ext.poses) != len(dna.rbf.poses):
            msgs.append(Message("ERROR", "RBF pose extension count differs from the RBF pose count"))
        for si, s in enumerate(dna.rbf.solvers):
            k = len(s.raw_control_indices)
            if k == 0:
                msgs.append(Message("ERROR", f"Solver {s.name} has no driver controls"))
                continue
            if len(s.raw_control_values) != k * len(s.pose_indices):
                msgs.append(Message("ERROR", f"Solver {s.name}: {len(s.pose_indices)} poses but "
                                             f"{len(s.raw_control_values) // k} targets"))
            if s.distance_method != 0 and k % 4:
                msgs.append(Message("ERROR", f"Solver {s.name}: quaternion distance needs inputs in groups of 4"))
            if int(s.raw_control_indices.max()) >= len(d.raw_control_names):
                msgs.append(Message("ERROR", f"Solver {s.name} reads a raw control that does not exist"))
            if s.distance_method != 0 and len(s.raw_control_values) == k * len(s.pose_indices):
                q = solver_targets(dna, si).reshape(-1, 4)
                bad = np.abs(np.linalg.norm(q, axis=1) - 1) > 1e-3
                if bad.any():
                    msgs.append(Message("WARNING", f"Solver {s.name}: {int(bad.sum())} target quaternions "
                                                   "are not unit length"))
            for p in s.pose_indices:
                if int(p) >= len(dna.rbf.poses):
                    msgs.append(Message("ERROR", f"Solver {s.name} uses pose {int(p)}, which does not exist"))
            if s.radius <= 0 and s.automatic_radius != 0:
                msgs.append(Message("WARNING", f"Solver {s.name}: radius is zero and automatic radius is off"))
        names = [p.name for p in dna.rbf.poses]
        if len(set(names)) != len(names):
            msgs.append(Message("WARNING", "Some RBF pose names are used more than once"))
    if dna.twist_swing:
        for t in (*dna.twist_swing.twists, *dna.twist_swing.swings):
            if len(t.input_control_indices) != 4:
                msgs.append(Message("ERROR", "A twist/swing setup does not read exactly one quaternion"))
            if len(t.blend_weights) != len(t.output_joint_indices):
                msgs.append(Message("ERROR", "A twist/swing setup has a weight count different from its joints"))
    if not any(m.level == "ERROR" for m in msgs):
        msgs.append(Message("INFO", f"RigLogic checks passed: {len(b.joint_groups)} joint groups, "
                                    f"{len(dna.rbf.solvers) if dna.rbf else 0} RBF solvers"))
    return msgs


def validate_against_reference(dna: DNA, reference: DNA) -> list[Message]:
    """Checks for re-importing into MetaHuman Creator: same joints, hierarchy, meshes and LODs as the original."""
    msgs: list[Message] = []
    d, r = dna.definition, reference.definition
    if d.joint_names != r.joint_names:
        extra = sorted(set(d.joint_names) - set(r.joint_names))
        missing = sorted(set(r.joint_names) - set(d.joint_names))
        msgs.append(Message("ERROR", f"Joint set differs from the original (added {extra[:5]}, removed {missing[:5]})"))
    elif not np.array_equal(d.joint_parents, r.joint_parents):
        msgs.append(Message("ERROR", "Joint hierarchy differs from the original"))
    if d.mesh_names != r.mesh_names:
        msgs.append(Message("ERROR", "Mesh list differs from the original"))
    if dna.descriptor.lod_count != reference.descriptor.lod_count:
        msgs.append(Message("ERROR", "LOD count differs from the original"))
    for i, (a, bm) in enumerate(zip(dna.mesh_raw, reference.mesh_raw)):
        ma, mb = dna.meshes[i] if i < len(dna.meshes) else None, reference.meshes[i] if i < len(reference.meshes) else None
        if ma is not None and mb is not None and len(ma.positions) != len(mb.positions):
            msgs.append(Message("ERROR", f"Mesh {d.mesh_names[i]} vertex count changed"))
    moved = np.abs(d.neutral_translations - r.neutral_translations).max() if len(d.neutral_translations) == len(
        r.neutral_translations) else np.inf
    if moved > 1e-3:
        msgs.append(Message("WARNING", f"Neutral joint positions moved (up to {moved:.3f} cm); MetaHuman Creator "
                                       "fits its own skeleton when it imports"))
    if not any(m.level == "ERROR" for m in msgs):
        msgs.append(Message("INFO", "MetaHuman Creator checks passed: joints, hierarchy, meshes and LODs match"))
    return msgs


def clone(dna: DNA) -> DNA:
    return copy.deepcopy(dna)


# --------------------------------------------------------------------------- drivers and mirroring


def add_driver(dna: DNA, si: int, joint: str) -> None:
    """Add a driver joint to a solver; every existing pose gets the rest rotation for it."""
    raw = ensure_quat_raw_controls(dna, joint)
    s = dna.rbf.solvers[si]
    t = solver_targets(dna, si) if len(s.raw_control_indices) else np.zeros((len(s.pose_indices), 0), np.float32)
    ident = np.tile(np.float32([0, 0, 0, 1]), (len(t), 1))
    s.raw_control_values = np.concatenate([t, ident], axis=1).ravel()
    s.raw_control_indices = np.append(s.raw_control_indices, raw).astype(np.uint16)


def remove_driver(dna: DNA, si: int, joint: str) -> None:
    s = dna.rbf.solvers[si]
    names = dna.definition.raw_control_names
    idx = [k for k, c in enumerate(s.raw_control_indices) if names[int(c)].rpartition(".")[0] == joint]
    if not idx:
        return
    t = np.delete(solver_targets(dna, si), idx, axis=1)
    s.raw_control_values = t.ravel()
    s.raw_control_indices = np.delete(s.raw_control_indices, idx)


def solver_drivers(dna: DNA, si: int) -> list[str]:
    names = dna.definition.raw_control_names
    out = []
    for c in dna.rbf.solvers[si].raw_control_indices:
        j = names[int(c)].rpartition(".")[0]
        if j not in out:
            out.append(j)
    return out


_SIDES = ((r"_l(?=_|$)", "_r"), (r"_r(?=_|$)", "_l"), (r"_L(?=_|$)", "_R"), (r"_R(?=_|$)", "_L"),
          (r"(?<![A-Za-z])left", "right"), (r"(?<![A-Za-z])right", "left"),
          (r"Left", "Right"), (r"Right", "Left"))


def mirror_name(name: str) -> str:
    """Swap side markers: upperarm_l <-> upperarm_r, tiltLeft <-> tiltRight."""
    out, i = [], 0
    pattern = re.compile("|".join(f"(?:{p})" for p, _ in _SIDES))
    for m in pattern.finditer(name):
        out.append(name[i:m.start()])
        text = m.group(0)
        for p, rep in _SIDES:
            if re.fullmatch(p, text):
                out.append(rep)
                break
        i = m.end()
    out.append(name[i:])
    return "".join(out)


def mirror_quat(q: np.ndarray, axis: int) -> np.ndarray:
    """Mirror a rotation across the plane normal to `axis` (0 X, 1 Y, 2 Z)."""
    q = np.asarray(q, np.float32).copy()
    for k in range(3):
        if k != axis:
            q[k] = -q[k]
    return q


def mirror_delta(delta: np.ndarray, rot_axis: int, trans_axis: int) -> np.ndarray:
    """Mirror a 9-value joint delta: translation across trans_axis, rotation across rot_axis."""
    from .rbf import quat_to_euler_xyz
    from .skeleton import euler_to_matrix
    d = np.asarray(delta, np.float32).copy()
    d[trans_axis] = -d[trans_axis]
    m = euler_to_matrix(d[3:6].reshape(1, 3))[0]
    w = np.sqrt(max(0.0, 1 + m[0, 0] + m[1, 1] + m[2, 2])) / 2 or 1e-9
    q = np.array([(m[2, 1] - m[1, 2]) / (4 * w), (m[0, 2] - m[2, 0]) / (4 * w), (m[1, 0] - m[0, 1]) / (4 * w), w])
    d[3:6] = quat_to_euler_xyz(mirror_quat(q, rot_axis))
    return d
