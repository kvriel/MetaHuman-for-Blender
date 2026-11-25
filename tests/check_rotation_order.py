"""Find which Euler order puts DNA joints at the centre of the vertices they drive.

python tests/check_rotation_order.py <file.dna> [mesh index]
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addon"))

from mfb.core.skeleton import ORDERS, local_matrices, world_matrices  # noqa: E402
from mfb.dna.reader import read_dna  # noqa: E402


def joint_centroids(mesh, joint_count, min_weight=0.6):
    sums = np.zeros((joint_count, 3)); counts = np.zeros(joint_count)
    for v, (w, j) in enumerate(zip(mesh.skin_weights, mesh.skin_joints)):
        for wi, ji in zip(w, j):
            if wi >= min_weight:
                sums[ji] += mesh.positions[v]; counts[ji] += 1
    ok = counts >= 8
    return ok, sums[ok] / counts[ok, None]


def main(path, mesh_index=0):
    dna = read_dna(path, blend_shapes=False, meshes={mesh_index})
    d = dna.definition
    ok, centroids = joint_centroids(dna.meshes[mesh_index], len(d.joint_names))
    print(f"{Path(path).name}: {ok.sum()} joints with enough vertices to compare")
    for order in ORDERS:
        world = world_matrices(local_matrices(d.neutral_translations, d.neutral_rotations, order),
                               d.joint_parents)
        dist = np.linalg.norm(world[ok, :3, 3] - centroids, axis=1)
        print(f"  {order}: median {np.median(dist):6.2f} cm   90th pct {np.percentile(dist, 90):6.2f} cm")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0)
