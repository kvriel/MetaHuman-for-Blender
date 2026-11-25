"""Tests for core/dna_edit.py on a body DNA: python tests/dna_edit_test.py <SKM_Body.dna>"""
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addon"))
from mfb.core import dna_edit as E  # noqa: E402
from mfb.core.riglogic import RigLogic  # noqa: E402
from mfb.dna.reader import read_dna  # noqa: E402
from mfb.dna.writer import write_dna  # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{' - ' + detail if detail else ''}")


def deltas_at_pose(dna, pose_name):
    """Joint deltas with every solver's drivers at the target of `pose_name`."""
    rl = RigLogic(dna, 0)
    names = [j for j, _ in rl.quat_inputs]
    quats = np.tile(np.float32([0, 0, 0, 1]), (len(names), 1))
    for si, s in enumerate(dna.rbf.solvers):
        pnames = [dna.rbf.poses[p].name for p in s.pose_indices]
        if pose_name in pnames:
            t = E.solver_targets(dna, si)[pnames.index(pose_name)]
            for qi in range(len(t) // 4):
                joint = dna.definition.raw_control_names[s.raw_control_indices[qi * 4]].rpartition(".")[0]
                quats[names.index(joint)] = t[qi * 4:qi * 4 + 4]
    return rl.evaluate_raw(np.zeros(rl.raw_count, np.float32), quats).joint_deltas


dna = read_dna(sys.argv[1])
names = dna.definition.joint_names
check("original validates", not [m for m in E.validate_riglogic(dna) if m.level == "ERROR"])
before = deltas_at_pose(dna, "upperarm_l_out_85")

# 1. remove an unrelated pose: other poses must give identical results (index remap)
victim = [p.name for p in dna.rbf.poses].index("calf_l_back_120")
n_poses = len(dna.rbf.poses)
E.remove_pose(dna, victim)
after = deltas_at_pose(dna, "upperarm_l_out_85")
check("removing a pose keeps other poses intact", np.allclose(before, after, atol=1e-5),
      f"{n_poses} -> {len(dna.rbf.poses)} poses, max diff {np.abs(before - after).max():.2e}")

# 2. new solver on lowerarm_l with a pose that pushes a joint
si = E.add_solver(dna, "lowerarm_l_test_UERBFSolver", ["lowerarm_l"], radius=30.0)
target = np.float32([0.0, 0.0, 0.5, 0.8660254])           # 60 deg about Z
j = names.index("lowerarm_correctiveRoot_l") if "lowerarm_correctiveRoot_l" in names else names.index("lowerarm_twist_01_l")
delta = np.zeros(9, np.float32); delta[0:3] = (0.0, 1.5, 0.0)   # 1.5 cm up the Y axis
pi = E.add_pose(dna, si, "lowerarm_l_test_bend_60", target, {j: delta})
res = deltas_at_pose(dna, "lowerarm_l_test_bend_60")
check("new solver + pose drives its joint", np.allclose(res[j, 0:3], delta[0:3], atol=1e-3),
      f"{names[j]} moved {np.round(res[j, 0:3], 3)} cm")
check("edited DNA validates", not [m for m in E.validate_riglogic(dna) if m.level == "ERROR"],
      "; ".join(m.text for m in E.validate_riglogic(dna) if m.level != "INFO")[:200])

# 3. rename (find/replace), twist setup, write/read round trip
n = E.find_replace(dna, r"_test_", "_t_", regex=True)
check("find and replace renames solver and its poses", n == 3, f"{n} names changed (solver, default pose, bend pose)")
E.add_twist_setup(dna, "upperarm_l", ["upperarm_twist_01_l", "upperarm_twist_02_l"], axis=0)
with tempfile.TemporaryDirectory() as tmp:
    p = write_dna(dna, Path(tmp) / "edited.dna")
    back = read_dna(p)
    res2 = deltas_at_pose(back, "lowerarm_l_t_bend_60")
    check("edits survive write + read", np.allclose(res2[j, 0:3], delta[0:3], atol=1e-3)
          and len(back.rbf.poses) == len(dna.rbf.poses) and len(back.twist_swing.twists) == len(dna.twist_swing.twists))
    ref = read_dna(sys.argv[1])
    msgs = E.validate_against_reference(back, ref)
    check("MetaHuman Creator check passes (same joints/meshes)", not [m for m in msgs if m.level == "ERROR"],
          "; ".join(m.text for m in msgs)[:160])

# 4. remove the solver again: back to the original pose set
E.remove_solver(dna, E.solver_index(dna, "lowerarm_l_t_UERBFSolver"))
check("remove solver", "lowerarm_l_t_bend_60" not in [p.name for p in dna.rbf.poses]
      and np.allclose(deltas_at_pose(dna, "upperarm_l_out_85"), before, atol=1e-5))
sys.exit(0 if ok else 1)
