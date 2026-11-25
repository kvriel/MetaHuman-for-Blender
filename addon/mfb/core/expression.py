"""Expression Editor maths (numpy only, no bpy).

- Expression graph: raw expressions and the PSD correctives built from them.
- Corrective shapes: a sculpt made on the posed face is turned into a rest-space blend shape
  delta by undoing the skinning, per vertex: d_rest = (sum_i w_i A_i)^-1 d_posed, with A_i the
  linear part of joint i's pose-from-rest matrix.
- Blend shape targets: read, add to, create.
- Joint matching (least squares): after the neutral face changes, each joint moves by the
  skin-weighted mean of its vertices' displacement (minimises sum w^2 |dv - t|^2).
- LOD transfer: carry a LOD0 displacement to another LOD with inverse-distance weights.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..dna.reader import DNA, BlendShapeTarget
from . import dna_edit as E


# --------------------------------------------------------------------------- graph


@dataclass
class Expression:
    control: int
    name: str
    inputs: list[int] = field(default_factory=list)       # PSD: the controls it multiplies
    is_psd: bool = False


def expression_graph(dna: DNA) -> dict[int, Expression]:
    """Every raw CTRL_expressions control and every PSD corrective, keyed by control index."""
    d, b = dna.definition, dna.behavior
    graph: dict[int, Expression] = {}
    for i, n in enumerate(d.raw_control_names):
        obj, _, attr = n.partition(".")
        if obj == "CTRL_expressions":
            graph[i] = Expression(i, attr)
    rows, cols = b.psd_rows.astype(int), b.psd_columns.astype(int)
    for r in np.unique(rows):
        ins = [int(c) for c in cols[rows == r]]
        names = [graph[c].name if c in graph else (d.raw_control_names[c] if c < len(d.raw_control_names) else f"pose{c}")
                 for c in ins]
        graph[int(r)] = Expression(int(r), "+".join(names), ins, True)
    return graph


def upstream(graph: dict[int, Expression], control: int) -> list[int]:
    e = graph.get(control)
    return [c for c in (e.inputs if e else []) if c in graph]


def downstream(graph: dict[int, Expression], control: int) -> list[int]:
    return [c for c, e in graph.items() if control in e.inputs]


def channels_for(dna: DNA, control: int) -> list[int]:
    b = dna.behavior
    return [int(o) for i, o in zip(b.blend_shape_inputs, b.blend_shape_outputs) if int(i) == control]


# --------------------------------------------------------------------------- blend shapes


def target_delta(dna: DNA, mesh_index: int, channel: int) -> np.ndarray:
    """Dense (P, 3) delta of a channel on a mesh (zeros if the mesh has no target for it)."""
    m = dna.meshes[mesh_index]
    out = np.zeros((len(m.positions), 3), np.float32)
    for t in m.blend_shapes:
        if t.channel == channel:
            out[t.vertex_indices] = t.deltas
    return out


def set_target_delta(dna: DNA, mesh_index: int, channel: int, dense: np.ndarray, eps: float = 1e-5) -> None:
    """Store a dense delta as a sparse target (creating or removing the target as needed)."""
    m = dna.meshes[mesh_index]
    idx = np.nonzero(np.abs(dense).max(axis=1) > eps)[0].astype(np.uint32)
    for k, t in enumerate(m.blend_shapes):
        if t.channel == channel:
            if len(idx):
                t.vertex_indices, t.deltas = idx, dense[idx].astype(np.float32)
            else:
                del m.blend_shapes[k]
            return
    if len(idx):
        m.blend_shapes.append(BlendShapeTarget(channel, idx, dense[idx].astype(np.float32)))
        d = dna.definition
        pairs = set(zip(d.mesh_blend_shape_from.tolist(), d.mesh_blend_shape_to.tolist()))
        if (mesh_index, channel) not in pairs:
            d.mesh_blend_shape_from = np.append(d.mesh_blend_shape_from, mesh_index).astype(np.uint16)
            d.mesh_blend_shape_to = np.append(d.mesh_blend_shape_to, channel).astype(np.uint16)


def ensure_channel(dna: DNA, control: int, name: str) -> int:
    """Blend shape channel driven by `control`; created (on every LOD) if there is none."""
    have = channels_for(dna, control)
    if have:
        return have[0]
    d, b = dna.definition, dna.behavior
    d.blend_shape_channel_names.append(name)
    ch = len(d.blend_shape_channel_names) - 1
    b.blend_shape_inputs = np.append(b.blend_shape_inputs, control).astype(np.uint16)
    b.blend_shape_outputs = np.append(b.blend_shape_outputs, ch).astype(np.uint16)
    b.blend_shape_lods = (b.blend_shape_lods.astype(np.int64) + 1).astype(np.uint16) if len(b.blend_shape_lods) \
        else np.full(dna.descriptor.lod_count, len(b.blend_shape_inputs), np.uint16)
    lm = d.lod_blend_shapes
    if lm.indices:
        lm.indices = [np.append(row, ch).astype(np.uint16) for row in lm.indices]
    else:
        lm.lods, lm.indices = np.zeros(dna.descriptor.lod_count, np.uint16), [np.array([ch], np.uint16)]
    return ch


# --------------------------------------------------------------------------- corrective shapes


def skinning_linear(weights: list[np.ndarray], joints: list[np.ndarray], joint_linear: np.ndarray) -> np.ndarray:
    """(P, 3, 3): per-vertex blend of joint 3x3 pose-from-rest matrices (joint_linear[j])."""
    P = len(weights)
    k = max((len(w) for w in weights), default=0)
    W = np.zeros((P, k), np.float64); J = np.zeros((P, k), np.intp)
    for v, (w, j) in enumerate(zip(weights, joints)):
        W[v, :len(w)] = w; J[v, :len(j)] = j
    return np.einsum("pk,pkij->pij", W, joint_linear[J])


def corrective_delta(posed_delta: np.ndarray, skin_linear: np.ndarray) -> np.ndarray:
    """Rest-space delta that produces `posed_delta` once the skinning is applied."""
    inv = np.linalg.inv(skin_linear + np.eye(3) * 1e-9)
    return np.einsum("pij,pj->pi", inv, posed_delta)


def propagate(dna: DNA, graph, control: int, mesh_index: int, rest_delta: np.ndarray, *, mode: str = "KEEP",
              strength: float = 1.0, locked: set[int] | None = None) -> list[int]:
    """Push an expression edit into its downstream correctives.

    KEEP: subtract the edit from each corrective so the combined poses look as before.
    CARRY: leave correctives alone, so the edit shows in every combination.
    """
    if mode != "KEEP" or strength == 0:
        return []
    changed = []
    for c in downstream(graph, control):
        if locked and c in locked:
            continue
        ch = ensure_channel(dna, c, graph[c].name)
        cur = target_delta(dna, mesh_index, ch)
        set_target_delta(dna, mesh_index, ch, cur - strength * rest_delta)
        changed.append(c)
    return changed


# --------------------------------------------------------------------------- neutral, joints, LODs


def match_joints(dna: DNA, mesh_index: int, displacement: np.ndarray, min_weight: float = 0.05) -> np.ndarray:
    """Least-squares joint moves (J, 3) for a neutral-mesh displacement (P, 3), in DNA units."""
    m = dna.meshes[mesh_index]
    J = len(dna.definition.joint_names)
    num = np.zeros((J, 3)); den = np.zeros(J)
    for v, (w, j) in enumerate(zip(m.skin_weights, m.skin_joints)):
        w2 = np.where(w >= min_weight, w * w, 0.0)
        np.add.at(num, j, w2[:, None] * displacement[v])
        np.add.at(den, j, w2)
    out = np.zeros((J, 3))
    ok = den > 0
    out[ok] = num[ok] / den[ok, None]
    # joints with no skinned vertices: mean of their skinned descendants, else their parent's move
    from .skeleton import topo_order
    parents = dna.definition.joint_parents
    order = topo_order(parents)
    acc = np.where(ok[:, None], out, 0.0); cnt = ok.astype(float)
    for j in reversed(order):
        p = int(parents[j])
        if p != j:
            acc[p] += acc[j]; cnt[p] += cnt[j]
    for j in order:
        if ok[j]:
            continue
        p = int(parents[j])
        if cnt[j] > 0:
            out[j] = acc[j] / cnt[j]
        elif p != j:
            out[j] = out[p]
    return out


def apply_joint_moves(dna: DNA, world_moves: np.ndarray) -> None:
    """Move joints by world-space (DNA units) offsets, rewriting their local neutral translations."""
    from .skeleton import local_matrices, world_matrices
    d = dna.definition
    deg = dna.descriptor.rotation_unit == "degrees"
    world = world_matrices(local_matrices(d.neutral_translations, d.neutral_rotations, degrees=deg), d.joint_parents)
    new_pos = world[:, :3, 3] + world_moves
    t = np.array(d.neutral_translations, np.float64)
    for j, p in enumerate(d.joint_parents):
        p = int(p)
        if p == j:
            t[j] = new_pos[j]
        else:
            t[j] = world[p, :3, :3].T @ (new_pos[j] - new_pos[p])
    d.neutral_translations = t.astype(np.float32)


def lod_transfer(source_rest: np.ndarray, source_delta: np.ndarray, target_rest: np.ndarray, k: int = 4,
                 nearest=None) -> np.ndarray:
    """Carry a displacement from one LOD to another by inverse-distance weighting of the k nearest
    source vertices. `nearest(points, k)` -> (indices, distances) may be supplied (e.g. a KD-tree)."""
    if nearest is None:
        def nearest(points, kk):
            idx = np.zeros((len(points), kk), np.intp); dist = np.zeros((len(points), kk))
            for s in range(0, len(points), 512):
                block = points[s:s + 512]
                d2 = ((block[:, None, :] - source_rest[None, :, :]) ** 2).sum(-1)
                part = np.argpartition(d2, kk, axis=1)[:, :kk]
                idx[s:s + 512] = part
                dist[s:s + 512] = np.sqrt(np.take_along_axis(d2, part, 1))
            return idx, dist
    idx, dist = nearest(target_rest, k)
    w = 1.0 / np.maximum(dist, 1e-6)
    w /= w.sum(axis=1, keepdims=True)
    return np.einsum("pk,pki->pi", w, source_delta[idx])


def copy_expression_joints(dna: DNA, source: int, target: int) -> int:
    col = E.joint_column(dna, source)
    E.set_joint_column(dna, target, col)
    return len(col)


def set_skin_weights(dna: DNA, mesh_index: int, weights: list[np.ndarray], joints: list[np.ndarray]) -> None:
    m = dna.meshes[mesh_index]
    max_inf = m.max_influences or 8
    new_w, new_j = [], []
    for w, j in zip(weights, joints):
        order = np.argsort(-w)[:max_inf]
        w, j = np.asarray(w)[order], np.asarray(j)[order]
        keep = w > 1e-6
        w, j = w[keep], j[keep]
        s = w.sum()
        new_w.append((w / s if s > 0 else w).astype(np.float32)); new_j.append(j.astype(np.uint16))
    m.skin_weights, m.skin_joints = new_w, new_j
