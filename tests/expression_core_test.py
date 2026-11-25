"""Tests for core/expression.py: python tests/expression_core_test.py <template.dna>"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addon"))
from mfb.core import expression as X  # noqa: E402
from mfb.core.skeleton import local_matrices, world_matrices  # noqa: E402
from mfb.dna.reader import read_dna  # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{' - ' + detail if detail else ''}")


dna = read_dna(sys.argv[1], meshes={0})
g = X.expression_graph(dna)
raw = [c for c, e in g.items() if not e.is_psd]; psd = [c for c, e in g.items() if e.is_psd]
jaw = next(c for c, e in g.items() if e.name == "jawOpen")
down = X.downstream(g, jaw)
check("expression graph", len(raw) > 200 and len(psd) > 400 and len(down) > 5,
      f"{len(raw)} expressions, {len(psd)} correctives, jawOpen feeds {len(down)} (e.g. {g[down[0]].name})")
check("upstream of a corrective", all(jaw in X.upstream(g, c) for c in down))
check("jawOpen has a blend shape channel", X.channels_for(dna, jaw) != [])

rng = np.random.default_rng(1)
P = 50
lin = np.eye(3) + rng.normal(0, 0.2, (12, 3, 3))
w = [rng.dirichlet(np.ones(3)).astype(np.float32) for _ in range(P)]
j = [rng.choice(12, 3, replace=False) for _ in range(P)]
S = X.skinning_linear(w, j, lin)
posed = rng.normal(0, 1, (P, 3))
rest = X.corrective_delta(posed, S)
check("corrective shape undoes skinning", np.allclose(np.einsum("pij,pj->pi", S, rest), posed, atol=1e-6))

ch = X.channels_for(dna, jaw)[0]
before = X.target_delta(dna, 0, ch)
X.set_target_delta(dna, 0, ch, before + 0.1)
check("set/read blend shape target", np.allclose(X.target_delta(dna, 0, ch), before + 0.1, atol=1e-6))
new_ch = X.ensure_channel(dna, raw[-1] if not X.channels_for(dna, raw[-1]) else 0, "test_channel")
check("ensure channel", new_ch >= 0)

m = dna.meshes[0]
shift = np.array([0.0, 0.5, 0.0])
moves = X.match_joints(dna, 0, np.tile(shift, (len(m.positions), 1)))
skinned = moves[np.abs(moves).sum(1) > 0]
check("joint matching: uniform shift moves joints the same", len(skinned) > 100 and np.allclose(skinned, shift, atol=1e-6),
      f"{len(skinned)} joints moved")
d = dna.definition
W0 = world_matrices(local_matrices(d.neutral_translations, d.neutral_rotations), d.joint_parents)
X.apply_joint_moves(dna, moves)
W1 = world_matrices(local_matrices(d.neutral_translations, d.neutral_rotations), d.joint_parents)
check("apply joint moves keeps world targets", np.allclose(W1[:, :3, 3] - W0[:, :3, 3], moves, atol=1e-3))
src = m.positions[:2000].astype(np.float64)
tgt = src[::3] + 0.01
out = X.lod_transfer(src, np.tile(shift, (len(src), 1)), tgt)
check("LOD transfer of a uniform shift", np.allclose(out, shift, atol=1e-6))
sys.exit(0 if ok else 1)
