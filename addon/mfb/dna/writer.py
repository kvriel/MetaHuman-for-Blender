"""Writer for MetaHuman DNA files: the mirror of reader.py (_internal/specs/dna-format.md, not published).

Every decoded layer is serialised from its data, so edits are saved. Layers or meshes
that were not decoded are copied from their original bytes. Reading a file and writing
it back unchanged gives a byte-identical file (see tests/dna_roundtrip.py).
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from .reader import (DNA, Behavior, ConditionalTable, Definition, Descriptor, LODMapping, Mesh,
                     MLBehavior, RBFBehavior, RBFBehaviorExt, TwistSwingBehavior)

LATEST = (2, 5)
LAYER_VERSIONS = {"desc": 0x10001, "defn": 0x10001, "bhvr": 0x10001, "geom": 0x10001,
                  "mlbh": 0x10000, "rbfb": 0x10000, "rbfe": 0x10000, "jbmd": 0x10000, "twsw": 0x10000}
# layers each file version may hold (newer layers are added when upgrading)
LAYERS_FOR_VERSION = {2: ("desc", "defn", "bhvr", "geom"), 3: ("mlbh",), 4: ("rbfb", "jbmd", "twsw"), 5: ("rbfe",)}


class _Out:
    def __init__(self):
        self.b = bytearray()

    def u16(self, v: int) -> None:
        self.b += struct.pack(">H", int(v))

    def u32(self, v: int) -> None:
        self.b += struct.pack(">I", int(v))

    def f32(self, v: float) -> None:
        self.b += struct.pack(">f", float(v))

    def string(self, s: str) -> None:
        data = s.encode("utf-8", errors="surrogateescape")
        self.u32(len(data)); self.b += data

    def _array(self, a, dtype: str) -> None:
        arr = np.asarray(a).astype(dtype, copy=False).ravel()
        self.u32(len(arr)); self.b += arr.tobytes()

    def u16s(self, a) -> None:
        self._array(a, ">u2")

    def u32s(self, a) -> None:
        self._array(a, ">u4")

    def f32s(self, a) -> None:
        self._array(a, ">f4")

    def strings(self, items) -> None:
        self.u32(len(items))
        for s in items:
            self.string(s)

    def u16_matrix(self, rows) -> None:
        self.u32(len(rows))
        for r in rows:
            self.u16s(r)

    def vec3s(self, v) -> None:
        v = np.asarray(v, np.float32).reshape(-1, 3)
        for k in range(3):
            self.f32s(v[:, k])

    def uvs(self, v) -> None:
        v = np.asarray(v, np.float32).reshape(-1, 2)
        self.f32s(v[:, 0]); self.f32s(v[:, 1])

    def sized(self, body: bytes) -> None:
        self.u32(len(body)); self.b += body


def _lod(o: _Out, m: LODMapping) -> None:
    o.u16s(m.lods); o.u16_matrix(m.indices)


def _descriptor(o: _Out, d: Descriptor) -> None:
    o.string(d.name); o.u16(d.archetype_id); o.u16(d.gender_id); o.u16(d.age)
    o.u32(len(d.metadata))
    for k, v in d.metadata:
        o.string(k); o.string(v)
    o.u16(d.translation_unit_id); o.u16(d.rotation_unit_id)
    for a in d.axis_ids:
        o.u16(a)
    o.u16(d.lod_count); o.u16(d.max_lod); o.string(d.complexity); o.string(d.db_name)


def _definition(o: _Out, d: Definition) -> None:
    for m in (d.lod_joints, d.lod_blend_shapes, d.lod_animated_maps, d.lod_meshes):
        _lod(o, m)
    for names in (d.gui_control_names, d.raw_control_names, d.joint_names, d.blend_shape_channel_names,
                  d.animated_map_names, d.mesh_names):
        o.strings(names)
    o.u16s(d.mesh_blend_shape_from); o.u16s(d.mesh_blend_shape_to)
    o.u16s(d.joint_parents)
    o.vec3s(d.neutral_translations); o.vec3s(d.neutral_rotations)


def _conditional(o: _Out, t: ConditionalTable) -> None:
    o.u16s(t.input_indices); o.u16s(t.output_indices)
    o.f32s(t.from_values); o.f32s(t.to_values); o.f32s(t.slope_values); o.f32s(t.cut_values)


def _behavior(o: _Out, b: Behavior) -> None:
    o.u16(b.psd_count); _conditional(o, b.gui_to_raw)
    o.u16s(b.psd_rows); o.u16s(b.psd_columns); o.f32s(b.psd_values)
    o.u16(b.joint_row_count); o.u16(b.joint_col_count)
    o.u32(len(b.joint_groups))
    for g in b.joint_groups:
        o.u16s(g.lods); o.u16s(g.input_indices); o.u16s(g.output_indices)
        o.f32s(np.asarray(g.values, np.float32).ravel()); o.u16s(g.joint_indices)
    o.u16s(b.blend_shape_lods); o.u16s(b.blend_shape_inputs); o.u16s(b.blend_shape_outputs)
    o.u16s(b.animated_map_lods); _conditional(o, b.animated_maps)


def _mesh(m: Mesh) -> bytes:
    o = _Out()
    o.vec3s(m.positions); o.uvs(m.uvs); o.vec3s(m.normals)
    o.u32s(m.layout_positions); o.u32s(m.layout_uvs); o.u32s(m.layout_normals)
    o.u32(len(m.faces))
    for f in m.faces:
        o.u32s(f)
    o.u16(m.max_influences)
    o.u32(len(m.skin_weights))
    for w, j in zip(m.skin_weights, m.skin_joints):
        o.f32s(w); o.u16s(j)
    o.u32(len(m.blend_shapes))
    for t in m.blend_shapes:
        o.vec3s(t.deltas); o.u32s(t.vertex_indices); o.u16(t.channel)
    return bytes(o.b)


def _geometry(o: _Out, dna: DNA) -> None:
    count = len(dna.mesh_raw) if dna.mesh_raw else len(dna.meshes)
    o.u32(count)
    for i in range(count):
        m = dna.meshes[i] if i < len(dna.meshes) else None
        if m is not None and m.complete:
            o.sized(_mesh(m))
        else:
            o.sized(dna.mesh_raw[i])


def _ml(o: _Out, ml: MLBehavior) -> None:
    o.strings(ml.control_names); _lod(o, ml.lod_networks)
    o.u32(len(ml.region_names))
    for names in ml.region_names:
        o.strings(names)
    o.u32(len(ml.region_indices))
    for rows in ml.region_indices:
        o.u16_matrix(rows)
    o.u32(len(ml.networks))
    for n in ml.networks:
        body = _Out()
        body.u16s(n.output_indices); body.u16s(n.input_indices)
        body.u32(len(n.layers))
        for layer in n.layers:
            body.f32s(layer.biases); body.f32s(layer.weights); body.u16(layer.function_id); body.f32s(layer.parameters)
        o.sized(bytes(body.b))


def _rbf(o: _Out, r: RBFBehavior) -> None:
    _lod(o, r.lod_solvers)
    o.u32(len(r.solvers))
    for s in r.solvers:
        body = _Out()
        body.string(s.name); body.u16s(s.raw_control_indices); body.u16s(s.pose_indices)
        body.f32s(s.raw_control_values); body.f32(s.radius); body.f32(s.weight_threshold)
        for v in (s.solver_type, s.automatic_radius, s.distance_method, s.normalize_method, s.function_type,
                  s.twist_axis):
            body.u16(v)
        o.sized(bytes(body.b))
    o.u32(len(r.poses))
    for p in r.poses:
        o.string(p.name); o.f32(p.scale)


def _rbf_ext(o: _Out, r: RBFBehaviorExt) -> None:
    o.strings(r.pose_control_names)
    o.u32(len(r.poses))
    for p in r.poses:
        o.u16s(p.input_control_indices); o.u16s(p.output_control_indices); o.f32s(p.output_control_weights)


def _twist_swing(o: _Out, t: TwistSwingBehavior) -> None:
    for items in (t.twists, t.swings):
        o.u32(len(items))
        for x in items:
            o.f32s(x.blend_weights); o.u16s(x.output_joint_indices); o.u16s(x.input_control_indices); o.u16(x.twist_axis)


def _empty_layer(layer_id: str) -> bytes:
    """Bytes of an empty layer, used when upgrading an older file to 2.5."""
    o = _Out()
    if layer_id == "mlbh":
        o.u32(0); _lod(o, LODMapping(np.zeros(0, np.uint16), [])); o.u32(0); o.u32(0); o.u32(0)
    elif layer_id == "rbfb":
        _lod(o, LODMapping(np.zeros(0, np.uint16), [])); o.u32(0); o.u32(0)
    elif layer_id in ("rbfe", "twsw"):
        o.u32(0); o.u32(0)
    elif layer_id == "jbmd":
        o.u32(0)
    return bytes(o.b)


def layer_bytes(dna: DNA, layer_id: str) -> bytes:
    o = _Out()
    if layer_id == "desc" and dna.descriptor:
        _descriptor(o, dna.descriptor)
    elif layer_id == "defn" and dna.definition:
        _definition(o, dna.definition)
    elif layer_id == "bhvr" and dna.behavior:
        _behavior(o, dna.behavior)
    elif layer_id == "geom" and (dna.mesh_raw or dna.meshes):
        _geometry(o, dna)
    elif layer_id == "mlbh" and dna.ml:
        _ml(o, dna.ml)
    elif layer_id == "rbfb" and dna.rbf:
        _rbf(o, dna.rbf)
    elif layer_id == "rbfe" and dna.rbf_ext:
        _rbf_ext(o, dna.rbf_ext)
    elif layer_id == "jbmd" and dna.joint_representations is not None:
        o.u32(len(dna.joint_representations))
        for t, r, s in dna.joint_representations:
            o.u16(t); o.u16(r); o.u16(s)
    elif layer_id == "twsw" and dna.twist_swing:
        _twist_swing(o, dna.twist_swing)
    elif layer_id in dna.raw_layers:
        return dna.raw_layers[layer_id]
    else:
        return _empty_layer(layer_id)
    return bytes(o.b)


def dna_bytes(dna: DNA, upgrade: bool = False) -> bytes:
    """Serialise the whole file. upgrade=True writes file version 2.5 and adds any missing layers."""
    order = list(dna.layer_order)
    version = dna.version
    if upgrade:
        version = LATEST
        present = {lid for lid, _ in order}
        for v in range(2, LATEST[1] + 1):
            for lid in LAYERS_FOR_VERSION[v]:
                if lid not in present:
                    order.append((lid, LAYER_VERSIONS[lid]))
    bodies = [layer_bytes(dna, lid) for lid, _ in order]
    header = 3 + 2 + 2 + 4 + 16 * len(order)
    out = _Out()
    out.b += b"DNA"; out.u16(version[0]); out.u16(version[1])
    out.u32(len(order))
    offset = header
    for (lid, ver), body in zip(order, bodies):
        out.u32(struct.unpack(">I", lid.encode("latin-1"))[0]); out.u32(ver); out.u32(offset); out.u32(len(body))
        offset += len(body)
    for body in bodies:
        out.b += body
    return bytes(out.b)


def write_dna(dna: DNA, path: str | Path, upgrade: bool = False) -> Path:
    path = Path(path)
    path.write_bytes(dna_bytes(dna, upgrade=upgrade))
    return path
