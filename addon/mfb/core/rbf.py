"""RBF pose solvers and twist/swing joints, as RigLogic evaluates them (see _internal/specs/riglogic.md, not published).

Quaternions are (x, y, z, w). No bpy.
"""
from __future__ import annotations

import numpy as np

from ..dna.reader import DNA

# DNA enum values
SOLVER_ADDITIVE, SOLVER_INTERPOLATIVE = 0, 1
FN_GAUSSIAN, FN_EXPONENTIAL, FN_LINEAR, FN_CUBIC, FN_QUINTIC = range(5)
DIST_EUCLIDEAN, DIST_QUATERNION, DIST_SWING, DIST_TWIST = range(4)
NORMALIZE_ABOVE_ONE, NORMALIZE_ALWAYS = 0, 1
AUTO_RADIUS_ON = 0
FUNCTION_NAMES = ("Gaussian", "Exponential", "Linear", "Cubic", "Quintic")
DISTANCE_NAMES = ("Euclidean", "Quaternion", "Swing Angle", "Twist Angle")
SOLVER_NAMES = ("Additive", "Interpolative")
AXIS_NAMES = ("X", "Y", "Z")


# --------------------------------------------------------------------------- quaternion helpers


def q_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ax, ay, az, aw = a; bx, by, bz, bw = b
    return np.array([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz], np.float64)


def q_inv(q: np.ndarray) -> np.ndarray:
    n = float(np.dot(q, q)) or 1.0
    return np.array([-q[0], -q[1], -q[2], q[3]]) / n


def q_slerp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    d = float(np.dot(a, b))
    if d < 0:
        b, d = -b, -d
    if d > 0.9995:
        r = a + t * (b - a)
        return r / np.linalg.norm(r)
    th = np.arccos(np.clip(d, -1, 1))
    return (np.sin((1 - t) * th) * a + np.sin(t * th) * b) / np.sin(th)


def twist_of(q: np.ndarray, axis: int) -> np.ndarray:
    t = np.zeros(4); t[axis] = q[axis]; t[3] = q[3]
    n = np.linalg.norm(t)
    return np.array([0, 0, 0, 1.0]) if n == 0 else t / n


def swing_of(q: np.ndarray, axis: int) -> np.ndarray:
    """Swing part used by the swing-angle distance (RigLogic's formula, sign included)."""
    x, y, z, w = q
    if axis == 0:
        s = -np.sqrt(x * x + w * w); return np.array([0.0, (w * y - x * z) / s, (w * z + x * y) / s, s])
    if axis == 1:
        s = -np.sqrt(y * y + w * w); return np.array([(w * x + y * z) / s, 0.0, (w * z - y * x) / s, s])
    s = -np.sqrt(z * z + w * w); return np.array([(w * x - z * y) / s, (w * y + z * x) / s, 0.0, s])


def quat_to_euler_xyz(q: np.ndarray, degrees: bool = True) -> np.ndarray:
    """Euler angles (x, y, z) with R = Rz @ Ry @ Rx, matching skeleton.euler_to_matrix."""
    x, y, z, w = q / (np.linalg.norm(q) or 1.0)
    m20 = 2 * (x * z - w * y); m21 = 2 * (y * z + w * x); m22 = 1 - 2 * (x * x + y * y)
    m10 = 2 * (x * y + w * z); m00 = 1 - 2 * (y * y + z * z)
    e = np.array([np.arctan2(m21, m22), np.arcsin(np.clip(-m20, -1, 1)), np.arctan2(m10, m00)])
    return np.degrees(e) if degrees else e


# --------------------------------------------------------------------------- RBF


def _convert(values: np.ndarray, method: int, axis: int) -> np.ndarray:
    if method not in (DIST_SWING, DIST_TWIST):
        return values
    out = values.reshape(-1, 4).copy()
    for i in range(len(out)):
        out[i] = twist_of(out[i], axis) if method == DIST_TWIST else swing_of(out[i], axis)
    return out.ravel()


def _distances(targets: np.ndarray, x: np.ndarray, method: int) -> np.ndarray:
    if method == DIST_EUCLIDEAN:
        return np.sqrt(((targets - x) ** 2).sum(axis=1))
    t = targets.reshape(len(targets), -1, 4)
    q = x.reshape(-1, 4)
    dot2 = np.einsum("tqi,qi->tq", t, q) ** 2
    arc = np.where(dot2 > 1.0, 0.0, np.arccos(np.clip(2 * dot2 - 1, -1, 1)))
    return np.sqrt((arc ** 2).sum(axis=1))


def _kernel(d: np.ndarray, radius: float, fn: int) -> np.ndarray:
    r = radius if radius != 0 else 1e-9
    if fn == FN_GAUSSIAN:
        return np.exp(-d / (r * r))
    if fn == FN_EXPONENTIAL:
        return np.exp(-2.0 * d / r)
    power = {FN_LINEAR: 1, FN_CUBIC: 3, FN_QUINTIC: 5}[fn]
    return np.maximum(0.0, 1.0 - (d / r) ** power)


class RBFSolverEval:
    def __init__(self, solver, pose_scales: np.ndarray, degrees: bool):
        self.name = solver.name
        self.inputs = solver.raw_control_indices.astype(np.intp)
        self.poses = solver.pose_indices.astype(np.intp)
        k = max(len(self.inputs), 1)
        targets = np.asarray(solver.raw_control_values, np.float64).reshape(-1, k)
        self.method, self.fn, self.axis = solver.distance_method, solver.function_type, solver.twist_axis
        self.targets = np.array([_convert(t, self.method, self.axis) for t in targets]) if len(targets) else targets
        self.scale = pose_scales[self.poses] if len(self.poses) else np.zeros(0)
        self.threshold = solver.weight_threshold
        self.normalize = solver.normalize_method
        self.solver_type = solver.solver_type
        dist_method = DIST_EUCLIDEAN if self.method == DIST_EUCLIDEAN else DIST_QUATERNION
        self.dist_method = dist_method
        self.radius = np.radians(solver.radius) if degrees else solver.radius
        n = len(self.targets)
        if solver.automatic_radius == AUTO_RADIUS_ON and n > 1:
            total = sum(_distances(self.targets[i + 1:], self.targets[i], dist_method).sum() for i in range(n - 1))
            self.radius = total / (n * (n - 1) / 2)
        self.coeff = None
        if self.solver_type == SOLVER_INTERPOLATIVE and n:
            m = np.array([_kernel(_distances(self.targets, t, dist_method), self.radius, self.fn) for t in self.targets])
            try:
                self.coeff = np.linalg.inv(m)
            except np.linalg.LinAlgError:
                self.coeff = np.linalg.pinv(m)

    def solve(self, x: np.ndarray) -> np.ndarray:
        if not len(self.targets):
            return np.zeros(0)
        x = _convert(np.asarray(x, np.float64), self.method, self.axis)
        phi = _kernel(_distances(self.targets, x, self.dist_method), self.radius, self.fn)
        w = np.clip(self.coeff @ phi, 0.0, 1.0) if self.coeff is not None else phi
        total = w.sum()
        ratio = 1.0 / total if (total > 1.0 or (self.normalize == NORMALIZE_ALWAYS and total > 0)) else 1.0
        w = w * ratio * self.scale
        return np.where(w > self.threshold, w, 0.0)


class RBFEval:
    """All RBF solvers of a DNA: raw (quaternion) controls -> RBF pose controls."""

    def __init__(self, dna: DNA, lod: int, degrees: bool):
        self.solvers: list[RBFSolverEval] = []
        self.active: list[int] = []
        r = dna.rbf
        if r is None or not r.solvers:
            return
        scales = np.array([p.scale for p in r.poses], np.float64)
        self.solvers = [RBFSolverEval(s, scales, degrees) for s in r.solvers]
        self.active = [int(i) for i in r.lod_solvers.for_lod(lod)] if len(r.lod_solvers.lods) > lod \
            else list(range(len(self.solvers)))
        ext = dna.rbf_ext
        if ext is not None and ext.poses:
            self.pose_in = [p.input_control_indices.astype(np.intp) for p in ext.poses]
            self.pose_out = [p.output_control_indices.astype(np.intp) for p in ext.poses]
            self.pose_w = [p.output_control_weights.astype(np.float64) for p in ext.poses]
        else:  # older files: pose i drives control (raw + psd + ml + i) with weight 1
            base = (len(dna.definition.raw_control_names) + dna.behavior.psd_count
                    + (len(dna.ml.control_names) if dna.ml else 0))
            self.pose_in = [np.zeros(0, np.intp) for _ in r.poses]
            self.pose_out = [np.array([base + i], np.intp) for i in range(len(r.poses))]
            self.pose_w = [np.ones(1) for _ in r.poses]
        self.last_weights: dict[int, np.ndarray] = {}

    def highest_output(self) -> int:
        return max((int(o.max()) for o in getattr(self, "pose_out", []) if len(o)), default=-1)

    def evaluate(self, controls: np.ndarray, muted: set[int] | None = None) -> None:
        """Write RBF pose control values into `controls` in place."""
        for si in self.active:
            s = self.solvers[si]
            outs = [self.pose_out[p] for p in s.poses]
            for o in outs:
                controls[o] = 0.0
            if muted and si in muted:
                continue
            w = s.solve(controls[s.inputs])
            self.last_weights[si] = w
            for i, p in enumerate(s.poses):
                gate = np.prod(controls[self.pose_in[p]]) if len(self.pose_in[p]) else 1.0
                controls[self.pose_out[p]] += self.pose_w[p] * w[i] * gate


# --------------------------------------------------------------------------- twist / swing


class TwistSwingEval:
    """Twist and swing joints: rotations derived from a driver joint's quaternion."""

    def __init__(self, dna: DNA):
        self.setups: dict[tuple, dict] = {}
        ts = dna.twist_swing
        if ts is None:
            return
        for s in ts.swings:
            key = tuple(int(i) for i in s.input_control_indices)
            self.setups.setdefault(key, {})["swing"] = s
        for t in ts.twists:
            key = tuple(int(i) for i in t.input_control_indices)
            self.setups.setdefault(key, {})["twist"] = t

    def output_joints(self) -> set[int]:
        out = set()
        for st in self.setups.values():
            for part in st.values():
                out.update(int(j) for j in part.output_joint_indices)
        return out

    def evaluate(self, controls: np.ndarray, rot_deltas: np.ndarray, degrees: bool) -> None:
        """Overwrite rotation deltas (J, 3) of twist/swing output joints."""
        identity = np.array([0.0, 0.0, 0.0, 1.0])
        for key, st in self.setups.items():
            q = controls[list(key)].astype(np.float64)
            if np.dot(q, q) == 0:
                q = identity
            swing = st.get("swing")
            if swing is not None:
                twist = twist_of(q, swing.twist_axis)
                inv_twist = q_inv(twist)
                inv_swing = q_inv(q_mul(q, inv_twist))
                for w, j in zip(swing.blend_weights, swing.output_joint_indices):
                    out = q_mul(inv_twist, q_slerp(inv_swing, identity, float(w)))
                    rot_deltas[int(j)] = quat_to_euler_xyz(out, degrees)
            else:
                inv_twist = twist_of(q, st["twist"].twist_axis)   # RigLogic uses the twist itself here
            twist = st.get("twist")
            if twist is not None:
                for w, j in zip(twist.blend_weights, twist.output_joint_indices):
                    rot_deltas[int(j)] = quat_to_euler_xyz(q_slerp(inv_twist, identity, float(w)), degrees)
