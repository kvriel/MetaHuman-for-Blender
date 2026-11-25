"""Joint maths shared by the importer and RigLogic (numpy only, no bpy).

DNA joints store a local translation and a local Euler rotation (relative to the
parent joint). RigLogic's default rotation order is XYZ: rotate about X first,
then Y, then Z, so the matrix is Rz @ Ry @ Rx (column vectors), the same meaning
as Blender's Euler 'XYZ'.
"""
from __future__ import annotations

import numpy as np

ORDERS = ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX")


def _axis_rot(axis: str, a: np.ndarray) -> np.ndarray:
    """Batched 3x3 rotation matrices about one axis; a is (N,) radians."""
    c, s = np.cos(a), np.sin(a)
    m = np.zeros((len(a), 3, 3))
    if axis == "X":
        m[:, 0, 0] = 1; m[:, 1, 1] = c; m[:, 1, 2] = -s; m[:, 2, 1] = s; m[:, 2, 2] = c
    elif axis == "Y":
        m[:, 1, 1] = 1; m[:, 0, 0] = c; m[:, 0, 2] = s; m[:, 2, 0] = -s; m[:, 2, 2] = c
    else:
        m[:, 2, 2] = 1; m[:, 0, 0] = c; m[:, 0, 1] = -s; m[:, 1, 0] = s; m[:, 1, 1] = c
    return m


def euler_to_matrix(rot: np.ndarray, order: str = "XYZ", degrees: bool = True) -> np.ndarray:
    """(N, 3) Euler angles (x, y, z) -> (N, 3, 3). `order` is the order the axes apply in."""
    rot = np.asarray(rot, dtype=np.float64)
    if degrees:
        rot = np.radians(rot)
    per_axis = {"X": rot[:, 0], "Y": rot[:, 1], "Z": rot[:, 2]}
    m = np.broadcast_to(np.eye(3), (len(rot), 3, 3)).copy()
    for axis in order:                      # first applied ends up rightmost
        m = _axis_rot(axis, per_axis[axis]) @ m
    return m


def local_matrices(translations: np.ndarray, rotations: np.ndarray,
                   order: str = "XYZ", degrees: bool = True) -> np.ndarray:
    """(N, 4, 4) local joint matrices from DNA translations and Euler rotations."""
    n = len(translations)
    m = np.zeros((n, 4, 4))
    m[:, :3, :3] = euler_to_matrix(rotations, order, degrees)
    m[:, :3, 3] = translations
    m[:, 3, 3] = 1
    return m


def topo_order(parents: np.ndarray) -> list[int]:
    """Joint indices ordered so every parent comes before its children."""
    parents = [int(p) for p in parents]
    children: dict[int, list[int]] = {}
    roots = []
    for i, p in enumerate(parents):
        (roots if p == i else children.setdefault(p, [])).append(i)
    out, stack = [], list(reversed(roots))
    while stack:
        j = stack.pop()
        out.append(j)
        stack.extend(reversed(children.get(j, [])))
    if len(out) != len(parents):
        raise ValueError("Joint hierarchy has a cycle or a parent index out of range")
    return out


def world_matrices(local: np.ndarray, parents: np.ndarray) -> np.ndarray:
    """Compose local matrices down the hierarchy into world (model-space) matrices."""
    world = np.empty_like(local)
    for j in topo_order(parents):
        p = int(parents[j])
        world[j] = local[j] if p == j else world[p] @ local[j]
    return world
