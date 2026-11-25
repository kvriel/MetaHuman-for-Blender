"""RigLogic face evaluation in numpy (see _internal/specs/riglogic.md, not published). No bpy."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..dna.reader import DNA, ConditionalTable
from .rbf import RBFEval, TwistSwingEval


class _Conditional:
    """Vectorised conditional table: first matching row per (input, output) run, clamp [0, 1]."""

    def __init__(self, table: ConditionalTable, output_count: int):
        frm = table.from_values.astype(np.float32)
        to = table.to_values.astype(np.float32)
        swap = frm > to
        self.frm = np.where(swap, to, frm)
        self.to = np.where(swap, frm, to)
        self.inp = table.input_indices.astype(np.intp)
        self.out = table.output_indices.astype(np.intp)
        self.slope = table.slope_values.astype(np.float32)
        self.cut = table.cut_values.astype(np.float32)
        self.output_count = output_count
        # run id: increases whenever (input, output) changes from the previous row
        n = len(self.inp)
        change = np.ones(n, dtype=bool)
        if n > 1:
            change[1:] = (self.inp[1:] != self.inp[:-1]) | (self.out[1:] != self.out[:-1])
        self.run = np.cumsum(change) - 1

    def __call__(self, inputs: np.ndarray, rows: int | None = None) -> np.ndarray:
        n = len(self.inp) if rows is None else rows
        out = np.zeros(self.output_count, np.float32)
        if n == 0:
            return out
        x = inputs[self.inp[:n]]
        hit = np.nonzero((self.frm[:n] <= x) & (x <= self.to[:n]))[0]
        if len(hit):
            _, first = np.unique(self.run[hit], return_index=True)
            rows_used = hit[first]
            np.add.at(out, self.out[rows_used], self.slope[rows_used] * x[rows_used] + self.cut[rows_used])
        return np.clip(out, 0.0, 1.0, out=out)


@dataclass
class RigResult:
    controls: np.ndarray          # raw + psd (+ ml/rbf) control vector
    joint_deltas: np.ndarray      # (J, 9): tx ty tz rx ry rz sx sy sz
    blend_shapes: np.ndarray      # (channels,)
    animated_maps: np.ndarray     # (maps,)


class RigLogic:
    def __init__(self, dna: DNA, lod: int = 0):
        d, b = dna.definition, dna.behavior
        self.lod = lod
        self.gui_count = len(d.gui_control_names)
        self.raw_count = len(d.raw_control_names)
        self.psd_count = b.psd_count
        self.joint_count = len(d.joint_names)
        # raw + PSD, then ML and RBF pose controls; size from every index any stage reads
        referenced = [b.psd_columns, b.psd_rows, b.blend_shape_inputs, b.animated_maps.input_indices,
                      *(g.input_indices for g in b.joint_groups)]
        highest = max((int(a.max()) for a in referenced if len(a)), default=-1)
        self.degrees = dna.descriptor.rotation_unit == "degrees"
        self.rbf = RBFEval(dna, lod, self.degrees)
        self.twist_swing = TwistSwingEval(dna)
        self.muted_solvers: set[int] = set()
        highest = max(highest, self.rbf.highest_output())
        self.control_count = max(self.raw_count + self.psd_count, int(b.joint_col_count), highest + 1)

        # joint rotations RigLogic reads as raw quaternion controls ("neck_01.qx" .. ".qw")
        quat: dict[str, list[int]] = {}
        for i, name in enumerate(d.raw_control_names):
            joint, _, attr = name.rpartition(".")
            if joint and attr in ("qx", "qy", "qz", "qw"):
                quat.setdefault(joint, [-1, -1, -1, -1])["xyzw".index(attr[1])] = i
        self.quat_inputs = [(j, idx) for j, idx in quat.items() if min(idx) >= 0]
        self._quat_idx = np.array([idx for _, idx in self.quat_inputs], np.intp).reshape(-1, 4)

        self._gui_to_raw = _Conditional(b.gui_to_raw, self.raw_count)

        # PSDs: group matrix entries by output row into padded index lists
        rows, cols, vals = b.psd_rows.astype(np.intp), b.psd_columns.astype(np.intp), b.psd_values
        psd_out = np.unique(rows)
        self._psd_out = psd_out
        groups = [np.nonzero(rows == r)[0] for r in psd_out]
        width = max((len(g) for g in groups), default=0)
        one_slot = self.control_count  # index of an extra slot that always holds 1.0
        self._psd_inputs = np.full((len(psd_out), width), one_slot, np.intp)
        self._psd_weight = np.ones(len(psd_out), np.float32)
        for i, g in enumerate(groups):
            self._psd_inputs[i, :len(g)] = cols[g]
            self._psd_weight[i] = np.prod(vals[g])

        # joints: keep each group's LOD-limited matrix
        self._joint_groups = []
        for g in b.joint_groups:
            n = int(g.lods[lod]) if lod < len(g.lods) else 0
            if n:
                self._joint_groups.append((g.values[:n].astype(np.float32),
                                           g.input_indices.astype(np.intp),
                                           g.output_indices[:n].astype(np.intp)))

        n_bs = int(b.blend_shape_lods[lod]) if lod < len(b.blend_shape_lods) else 0
        self._bs_in = b.blend_shape_inputs[:n_bs].astype(np.intp)
        self._bs_out = b.blend_shape_outputs[:n_bs].astype(np.intp)
        self.blend_shape_count = len(d.blend_shape_channel_names)

        self.animated_map_count = len(d.animated_map_names)
        self._am = _Conditional(b.animated_maps, self.animated_map_count)
        self._am_rows = int(b.animated_map_lods[lod]) if lod < len(b.animated_map_lods) else 0

        # GUI control ranges (for building the face board and clamping input)
        gt = self._gui_to_raw
        self.gui_min = np.zeros(self.gui_count, np.float32)
        self.gui_max = np.zeros(self.gui_count, np.float32)
        np.minimum.at(self.gui_min, gt.inp, gt.frm)
        np.maximum.at(self.gui_max, gt.inp, gt.to)

    # ------------------------------------------------------------------ stages

    def raw_from_gui(self, gui: np.ndarray) -> np.ndarray:
        return self._gui_to_raw(np.asarray(gui, np.float32))

    def evaluate_raw(self, raw: np.ndarray, quats: np.ndarray | None = None) -> RigResult:
        """raw: raw control values. quats: (len(quat_inputs), 4) joint rotations (x, y, z, w)
        relative to the neutral pose; identity when not given."""
        controls = np.zeros(self.control_count + 1, np.float32)
        controls[:self.raw_count] = raw
        controls[-1] = 1.0
        if len(self._quat_idx):
            q = np.tile(np.float32([0, 0, 0, 1]), (len(self._quat_idx), 1)) if quats is None else quats
            controls[self._quat_idx] = q
        # RBF pose controls come first; correctives (PSDs) may read them
        self.rbf.evaluate(controls, self.muted_solvers)
        # PSDs read clamped raw controls
        clamped = np.clip(controls, 0.0, 1.0)
        clamped[-1] = 1.0
        if len(self._psd_out):
            psd = self._psd_weight * np.prod(clamped[self._psd_inputs], axis=1)
            controls[self._psd_out] = np.minimum(1.0, psd)
        controls = controls[:-1]

        deltas = np.zeros(self.joint_count * 9, np.float32)
        for values, inputs, outputs in self._joint_groups:
            deltas[outputs] = values @ controls[inputs]
        deltas = deltas.reshape(self.joint_count, 9)
        if self.twist_swing.setups:
            rot = deltas[:, 3:6].astype(np.float64)
            self.twist_swing.evaluate(controls, rot, self.degrees)
            deltas[:, 3:6] = rot

        weights = np.zeros(self.blend_shape_count, np.float32)
        weights[self._bs_out] = controls[self._bs_in]

        maps = self._am(controls, self._am_rows)
        return RigResult(controls, deltas, weights, maps)

    def evaluate_gui(self, gui: np.ndarray, quats: np.ndarray | None = None) -> RigResult:
        return self.evaluate_raw(self.raw_from_gui(gui), quats)

    def driven_joints(self) -> np.ndarray:
        """Joints whose transforms RigLogic writes (joint groups and twist/swing)."""
        driven = np.zeros(self.joint_count, bool)
        for _, _, outputs in self._joint_groups:
            driven[np.unique(outputs // 9)] = True
        for j in self.twist_swing.output_joints():
            driven[j] = True
        return driven
