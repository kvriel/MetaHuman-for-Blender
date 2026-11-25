"""Conversion from DNA space (cm, axes from the descriptor) to Blender space (m, Z-up, -Y front).

Joint frames are kept exactly as the DNA defines them; only the root's placement and
all translations change. For a DNA world matrix W = [R | t] the Blender armature-space
matrix is [C R | s C t]. Local (parent-relative) matrices only get their translation
scaled by s, except the root, which also gets C.
"""
from __future__ import annotations

import numpy as np

# Where each DNA direction points in Blender (character faces -Y, its left is +X).
_BLENDER_DIRECTION = {
    "left": (1, 0, 0), "right": (-1, 0, 0),
    "up": (0, 0, 1), "down": (0, 0, -1),
    "front": (0, -1, 0), "back": (0, 1, 0),
}

UNIT_TO_METRES = {"cm": 0.01, "m": 1.0}


class Space:
    def __init__(self, axes: tuple[str, str, str], translation_unit: str, metres: bool = True):
        self.C = np.array([_BLENDER_DIRECTION[a] for a in axes], dtype=np.float64).T
        if abs(np.linalg.det(self.C) - 1) > 1e-6:
            raise ValueError(f"DNA axes {axes} do not form a right-handed system")
        self.s = UNIT_TO_METRES.get(translation_unit, 0.01) if metres else 1.0

    @classmethod
    def for_dna(cls, dna, metres: bool = True) -> "Space":
        d = dna.descriptor
        return cls(d.coordinate_system, d.translation_unit, metres)

    def points(self, p: np.ndarray) -> np.ndarray:
        return (np.asarray(p, np.float64) @ self.C.T) * self.s

    def directions(self, n: np.ndarray) -> np.ndarray:
        return np.asarray(n, np.float64) @ self.C.T

    def world_matrices(self, world_dna: np.ndarray) -> np.ndarray:
        out = world_dna.copy()
        out[:, :3, :3] = self.C @ world_dna[:, :3, :3]
        out[:, :3, 3] = (world_dna[:, :3, 3] @ self.C.T) * self.s
        return out

    def local_matrices(self, local_dna: np.ndarray, is_root: np.ndarray) -> np.ndarray:
        out = local_dna.copy()
        out[:, :3, 3] *= self.s
        if np.any(is_root):
            out[is_root, :3, :3] = self.C @ local_dna[is_root, :3, :3]
            out[is_root, :3, 3] = (local_dna[is_root, :3, 3] @ self.C.T) * self.s
        return out
