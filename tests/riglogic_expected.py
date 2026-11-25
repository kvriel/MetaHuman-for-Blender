"""What our RigLogic gives for exported face curves, to compare with Unreal's RigLogic.

python tests/riglogic_expected.py <head.dna> <face_curves.json> <out.json> [frame indices...] [--inputs ue_actual.json]

With --inputs, each frame uses the input curve values Unreal evaluated (after its curve
compression) instead of the exported ones, so only the rig evaluation is compared.

Joint results are coordinate-free (translation offset length in cm, rotation angle in degrees,
both relative to the neutral pose in the parent's space), so they can be compared with Unreal's
pose whatever axis conversion Unreal uses.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addon"))
from mfb.core.riglogic import RigLogic  # noqa: E402
from mfb.core.skeleton import euler_to_matrix  # noqa: E402
from mfb.dna.reader import read_dna  # noqa: E402

argv = sys.argv[1:]
inputs = None
if "--inputs" in argv:
    k = argv.index("--inputs")
    inputs = json.loads(Path(argv[k + 1]).read_text(encoding="utf-8"))["frames"]
    del argv[k:k + 2]
dna_path, curves_path, out_path = argv[:3]
frames = [int(f) for f in argv[3:]] or None
dna = read_dna(dna_path, geometry=False, blend_shapes=False)
rl = RigLogic(dna, 0)
d = dna.definition
data = json.loads(Path(curves_path).read_text(encoding="utf-8"))
curves = data["curves"]
n = len(data["times"])
frames = frames or [0, n // 2, n - 1]
raw_names = d.raw_control_names
# curve CTRL_expressions_jawOpen <-> raw control CTRL_expressions.jawOpen
col = {name.replace(".", "_"): i for i, name in enumerate(raw_names)}
missing = [c for c in curves if c not in col]


def rot_angle(rx, ry, rz):
    m = euler_to_matrix(np.array([[rx, ry, rz]]))[0]
    return math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(m) - 1) / 2))))


out = {"frames": {}, "missing_curves": missing, "all_animated_maps": list(d.animated_map_names),
       "inputs_from": "unreal" if inputs else "export"}
for f in frames:
    raw = np.zeros(len(raw_names), np.float32)
    for name, values in curves.items():
        if name in col:
            raw[col[name]] = inputs[str(f)]["curves"]["inputs"].get(name, 0.0) if inputs else values[f]
    res = rl.evaluate_raw(raw)
    joints = {}
    for j, delta in enumerate(res.joint_deltas):
        t = float(np.linalg.norm(delta[:3]))
        r = rot_angle(*delta[3:6])
        if t > 1e-4 or r > 1e-3:
            joints[d.joint_names[j]] = [round(t, 5), round(r, 5)]
    shapes = {nm: round(float(w), 5) for nm, w in zip(d.blend_shape_channel_names, res.blend_shapes) if w > 1e-4}
    maps = {nm: round(float(w), 5) for nm, w in zip(d.animated_map_names, res.animated_maps) if w > 1e-4}
    out["frames"][str(f)] = {"time": data["times"][f], "joints": joints, "blend_shapes": shapes, "animated_maps": maps}
    print(f"frame {f}: {len(joints)} joints move, {len(shapes)} blend shapes, {len(maps)} maps")
Path(out_path).write_text(json.dumps(out, indent=1), encoding="utf-8")
print("missing curves:", missing[:5], len(missing))
