"""Pure-Python reader for MetaHuman DNA files (file versions 2.2 to 2.5).

Written from our own format notes in _internal/specs/dna-format.md (not published). Needs only numpy,
so it runs inside Blender and in a plain Python for tests. Everything the writer
needs is kept: raw enum values, every layer, and the raw bytes of anything not decoded.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

__all__ = ["DNAError", "read_dna", "DNA"]

_BE_U16 = np.dtype(">u2")
_BE_U32 = np.dtype(">u4")
_BE_F32 = np.dtype(">f4")

DIRECTIONS = ("left", "right", "up", "down", "front", "back")
ARCHETYPES = ("asian", "black", "caucasian", "hispanic", "alien", "other")
GENDERS = ("male", "female", "other")
KNOWN_LAYERS = ("desc", "defn", "bhvr", "geom", "mlbh", "rbfb", "rbfe", "jbmd", "twsw")


class DNAError(ValueError):
    """The file is not a DNA file we can read."""


class _Cursor:
    """Sequential big-endian reader over a bytes buffer."""

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes | memoryview, pos: int = 0):
        self.buf = buf
        self.pos = pos

    def take(self, n: int) -> memoryview:
        end = self.pos + n
        if end > len(self.buf):
            raise DNAError(f"Unexpected end of file at byte {self.pos} (wanted {n} more bytes)")
        view = memoryview(self.buf)[self.pos:end]
        self.pos = end
        return view

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def f32(self) -> float:
        return struct.unpack(">f", self.take(4))[0]

    def string(self) -> str:
        n = self.u32()
        return bytes(self.take(n)).decode("utf-8", errors="surrogateescape")

    def _array(self, dtype: np.dtype, native: str) -> np.ndarray:
        n = self.u32()
        raw = self.take(n * dtype.itemsize)
        return np.frombuffer(raw, dtype=dtype).astype(native)

    def u16s(self) -> np.ndarray:
        return self._array(_BE_U16, "<u2")

    def u32s(self) -> np.ndarray:
        return self._array(_BE_U32, "<u4")

    def f32s(self) -> np.ndarray:
        return self._array(_BE_F32, "<f4")

    def strings(self) -> list[str]:
        return [self.string() for _ in range(self.u32())]

    def u16_matrix(self) -> list[np.ndarray]:
        return [self.u16s() for _ in range(self.u32())]

    def vec3s(self) -> np.ndarray:
        """Three float arrays (xs, ys, zs) returned as an (N, 3) float32 array."""
        xs, ys, zs = self.f32s(), self.f32s(), self.f32s()
        if not (len(xs) == len(ys) == len(zs)):
            raise DNAError("Vector3 list has mismatched x/y/z lengths")
        return np.column_stack((xs, ys, zs)).astype(np.float32) if len(xs) else np.zeros((0, 3), np.float32)

    def uvs(self) -> np.ndarray:
        us, vs = self.f32s(), self.f32s()
        if len(us) != len(vs):
            raise DNAError("UV list has mismatched u/v lengths")
        return np.column_stack((us, vs)).astype(np.float32) if len(us) else np.zeros((0, 2), np.float32)


# --------------------------------------------------------------------------- data


@dataclass
class LODMapping:
    lods: np.ndarray                      # per LOD: which row of `indices` it uses
    indices: list[np.ndarray]

    def for_lod(self, lod: int) -> np.ndarray:
        return self.indices[int(self.lods[lod])]


@dataclass
class Descriptor:
    name: str
    archetype_id: int
    gender_id: int
    age: int
    metadata: list[tuple[str, str]]
    translation_unit_id: int
    rotation_unit_id: int
    axis_ids: tuple[int, int, int]
    lod_count: int
    max_lod: int
    complexity: str
    db_name: str

    @property
    def archetype(self) -> str:
        return _enum(ARCHETYPES, self.archetype_id)

    @property
    def gender(self) -> str:
        return _enum(GENDERS, self.gender_id)

    @property
    def translation_unit(self) -> str:
        return _enum(("cm", "m"), self.translation_unit_id)

    @property
    def rotation_unit(self) -> str:
        return _enum(("degrees", "radians"), self.rotation_unit_id)

    @property
    def coordinate_system(self) -> tuple[str, str, str]:
        return tuple(_enum(DIRECTIONS, a) for a in self.axis_ids)  # type: ignore[return-value]

    @property
    def metadata_dict(self) -> dict[str, str]:
        return dict(self.metadata)


@dataclass
class Definition:
    lod_joints: LODMapping
    lod_blend_shapes: LODMapping
    lod_animated_maps: LODMapping
    lod_meshes: LODMapping
    gui_control_names: list[str]
    raw_control_names: list[str]
    joint_names: list[str]
    blend_shape_channel_names: list[str]
    animated_map_names: list[str]
    mesh_names: list[str]
    mesh_blend_shape_from: np.ndarray      # mesh index
    mesh_blend_shape_to: np.ndarray        # blend shape channel index
    joint_parents: np.ndarray              # own index = root
    neutral_translations: np.ndarray       # (J, 3)
    neutral_rotations: np.ndarray          # (J, 3) Euler, descriptor rotation unit


@dataclass
class ConditionalTable:
    input_indices: np.ndarray
    output_indices: np.ndarray
    from_values: np.ndarray
    to_values: np.ndarray
    slope_values: np.ndarray
    cut_values: np.ndarray


@dataclass
class JointGroup:
    lods: np.ndarray                       # rows used per LOD
    input_indices: np.ndarray              # columns -> control index
    output_indices: np.ndarray             # rows -> joint attribute index (joint * 9 + attr)
    values: np.ndarray                     # (rows, columns), row-major in the file
    joint_indices: np.ndarray


@dataclass
class Behavior:
    psd_count: int
    gui_to_raw: ConditionalTable
    psd_rows: np.ndarray
    psd_columns: np.ndarray
    psd_values: np.ndarray
    joint_row_count: int
    joint_col_count: int
    joint_groups: list[JointGroup]
    blend_shape_lods: np.ndarray
    blend_shape_inputs: np.ndarray         # control index
    blend_shape_outputs: np.ndarray        # channel index
    animated_map_lods: np.ndarray
    animated_maps: ConditionalTable


@dataclass
class BlendShapeTarget:
    channel: int
    vertex_indices: np.ndarray
    deltas: np.ndarray                     # (K, 3)


@dataclass
class Mesh:
    positions: np.ndarray                  # (P, 3)
    uvs: np.ndarray                        # (T, 2)
    normals: np.ndarray                    # (N, 3)
    layout_positions: np.ndarray           # per vertex layout
    layout_uvs: np.ndarray
    layout_normals: np.ndarray
    faces: list[np.ndarray]                # layout indices per face
    max_influences: int
    skin_weights: list[np.ndarray]         # per position
    skin_joints: list[np.ndarray]          # per position
    blend_shapes: list[BlendShapeTarget]
    complete: bool = True                  # False when blend shapes were skipped on read


@dataclass
class NeuralLayer:
    biases: np.ndarray
    weights: np.ndarray
    function_id: int
    parameters: np.ndarray


@dataclass
class NeuralNetwork:
    output_indices: np.ndarray
    input_indices: np.ndarray
    layers: list[NeuralLayer]


@dataclass
class MLBehavior:
    control_names: list[str]
    lod_networks: LODMapping
    region_names: list[list[str]]                 # [mesh][region]
    region_indices: list[list[np.ndarray]]        # [mesh][region] -> network indices
    networks: list[NeuralNetwork]


@dataclass
class RBFSolver:
    name: str
    raw_control_indices: np.ndarray
    pose_indices: np.ndarray
    raw_control_values: np.ndarray        # (poses, inputs) flattened in the file
    radius: float
    weight_threshold: float
    solver_type: int
    automatic_radius: int
    distance_method: int
    normalize_method: int
    function_type: int
    twist_axis: int


@dataclass
class RBFPose:
    name: str
    scale: float


@dataclass
class RBFBehavior:
    lod_solvers: LODMapping
    solvers: list[RBFSolver]
    poses: list[RBFPose]


@dataclass
class RBFPoseExt:
    input_control_indices: np.ndarray
    output_control_indices: np.ndarray
    output_control_weights: np.ndarray


@dataclass
class RBFBehaviorExt:
    pose_control_names: list[str]
    poses: list[RBFPoseExt]


@dataclass
class TwistSwing:
    blend_weights: np.ndarray
    output_joint_indices: np.ndarray
    input_control_indices: np.ndarray
    twist_axis: int


@dataclass
class TwistSwingBehavior:
    twists: list[TwistSwing]
    swings: list[TwistSwing]


@dataclass
class DNA:
    path: Path
    version: tuple[int, int]
    layers: dict[str, tuple[int, int, int]]          # id -> (layer version, offset, size)
    layer_order: list[tuple[str, int]] = field(default_factory=list)
    raw_layers: dict[str, bytes] = field(default_factory=dict)
    descriptor: Descriptor | None = None
    definition: Definition | None = None
    behavior: Behavior | None = None
    meshes: list[Mesh | None] = field(default_factory=list)
    mesh_raw: list[bytes] = field(default_factory=list)  # each mesh's bytes after its size field
    ml: MLBehavior | None = None
    rbf: RBFBehavior | None = None
    rbf_ext: RBFBehaviorExt | None = None
    joint_representations: list[tuple[int, int, int]] | None = None
    twist_swing: TwistSwingBehavior | None = None

    @property
    def joint_count(self) -> int:
        return len(self.definition.joint_names) if self.definition else 0

    def mesh_index(self, name: str) -> int:
        return self.definition.mesh_names.index(name)


# --------------------------------------------------------------------------- parse


def _enum(names: tuple[str, ...], value: int) -> str:
    return names[value] if value < len(names) else str(value)


def _lod_mapping(c: _Cursor) -> LODMapping:
    return LODMapping(lods=c.u16s(), indices=c.u16_matrix())


def _descriptor(c: _Cursor) -> Descriptor:
    name = c.string()
    archetype, gender, age = c.u16(), c.u16(), c.u16()
    metadata = []
    for _ in range(c.u32()):
        k = c.string()
        metadata.append((k, c.string()))
    t_unit, r_unit = c.u16(), c.u16()
    axes = (c.u16(), c.u16(), c.u16())
    lod_count, max_lod = c.u16(), c.u16()
    return Descriptor(name, archetype, gender, age, metadata, t_unit, r_unit, axes, lod_count, max_lod,
                      c.string(), c.string())


def _definition(c: _Cursor) -> Definition:
    lod_j, lod_b, lod_a, lod_m = (_lod_mapping(c) for _ in range(4))
    names = [c.strings() for _ in range(6)]
    mb_from, mb_to = c.u16s(), c.u16s()
    parents = c.u16s()
    return Definition(
        lod_joints=lod_j, lod_blend_shapes=lod_b, lod_animated_maps=lod_a, lod_meshes=lod_m,
        gui_control_names=names[0], raw_control_names=names[1], joint_names=names[2],
        blend_shape_channel_names=names[3], animated_map_names=names[4], mesh_names=names[5],
        mesh_blend_shape_from=mb_from, mesh_blend_shape_to=mb_to,
        joint_parents=parents,
        neutral_translations=c.vec3s(),
        neutral_rotations=c.vec3s(),
    )


def _conditional_table(c: _Cursor) -> ConditionalTable:
    return ConditionalTable(c.u16s(), c.u16s(), c.f32s(), c.f32s(), c.f32s(), c.f32s())


def _behavior(c: _Cursor) -> Behavior:
    psd_count = c.u16()
    gui_to_raw = _conditional_table(c)
    psd_rows, psd_cols, psd_vals = c.u16s(), c.u16s(), c.f32s()
    row_count, col_count = c.u16(), c.u16()
    groups = []
    for _ in range(c.u32()):
        lods, inputs, outputs, values, joints = c.u16s(), c.u16s(), c.u16s(), c.f32s(), c.u16s()
        if len(values) != len(inputs) * len(outputs):
            raise DNAError("Joint group value count does not match its rows x columns")
        groups.append(JointGroup(lods, inputs, outputs, values.reshape(len(outputs), len(inputs)), joints))
    bs_lods, bs_in, bs_out = c.u16s(), c.u16s(), c.u16s()
    am_lods = c.u16s()
    am = _conditional_table(c)
    return Behavior(psd_count, gui_to_raw, psd_rows, psd_cols, psd_vals, row_count, col_count,
                    groups, bs_lods, bs_in, bs_out, am_lods, am)


def _mesh_body(c: _Cursor, end: int, with_blend_shapes: bool) -> Mesh:
    positions, uvs, normals = c.vec3s(), c.uvs(), c.vec3s()
    lay_p, lay_t, lay_n = c.u32s(), c.u32s(), c.u32s()
    faces = [c.u32s() for _ in range(c.u32())]
    max_inf = c.u16()
    weights, joints = [], []
    for _ in range(c.u32()):
        weights.append(c.f32s())
        joints.append(c.u16s())
    targets: list[BlendShapeTarget] = []
    complete = with_blend_shapes
    if with_blend_shapes:
        for _ in range(c.u32()):
            deltas = c.vec3s()
            idx = c.u32s()
            targets.append(BlendShapeTarget(channel=c.u16(), vertex_indices=idx, deltas=deltas))
    c.pos = end
    return Mesh(positions, uvs, normals, lay_p, lay_t, lay_n, faces, max_inf, weights, joints, targets, complete)


def _ml(c: _Cursor) -> MLBehavior:
    names = c.strings()
    lod = _lod_mapping(c)
    region_names = [c.strings() for _ in range(c.u32())]
    region_indices = [c.u16_matrix() for _ in range(c.u32())]
    networks = []
    for _ in range(c.u32()):
        size = c.u32(); end = c.pos + size
        out_i, in_i = c.u16s(), c.u16s()
        layers = []
        for _ in range(c.u32()):
            biases, weights = c.f32s(), c.f32s()
            fid = c.u16()
            layers.append(NeuralLayer(biases, weights, fid, c.f32s()))
        c.pos = end
        networks.append(NeuralNetwork(out_i, in_i, layers))
    return MLBehavior(names, lod, region_names, region_indices, networks)


def _rbf(c: _Cursor) -> RBFBehavior:
    lod = _lod_mapping(c)
    solvers = []
    for _ in range(c.u32()):
        size = c.u32(); end = c.pos + size
        name = c.string()
        raw_i, pose_i, raw_v = c.u16s(), c.u16s(), c.f32s()
        radius, threshold = c.f32(), c.f32()
        ints = [c.u16() for _ in range(6)]
        c.pos = end
        solvers.append(RBFSolver(name, raw_i, pose_i, raw_v, radius, threshold, *ints))
    poses = []
    for _ in range(c.u32()):
        n = c.string()
        poses.append(RBFPose(n, c.f32()))
    return RBFBehavior(lod, solvers, poses)


def _rbf_ext(c: _Cursor) -> RBFBehaviorExt:
    names = c.strings()
    poses = [RBFPoseExt(c.u16s(), c.u16s(), c.f32s()) for _ in range(c.u32())]
    return RBFBehaviorExt(names, poses)


def _twist_swing(c: _Cursor) -> TwistSwingBehavior:
    def one() -> TwistSwing:
        w, out_j, in_c = c.f32s(), c.u16s(), c.u16s()
        return TwistSwing(w, out_j, in_c, c.u16())
    twists = [one() for _ in range(c.u32())]
    swings = [one() for _ in range(c.u32())]
    return TwistSwingBehavior(twists, swings)


def read_dna(path: str | Path, *, geometry: bool = True, blend_shapes: bool = True,
             meshes: set[int] | None = None) -> DNA:
    """Read a DNA file.

    geometry: decode the geometry layer at all (its raw bytes are always kept for writing).
    blend_shapes: decode blend shape targets (the largest part of a face DNA).
    meshes: only decode these mesh indices; the rest are left as None.
    """
    path = Path(path)
    buf = path.read_bytes()
    c = _Cursor(buf)
    if bytes(c.take(3)) != b"DNA":
        raise DNAError(f"{path.name} is not a DNA file (missing 'DNA' signature)")
    generation, version = c.u16(), c.u16()
    if generation != 2 or version < 2:
        raise DNAError(f"{path.name} uses DNA file version {generation}.{version}; "
                       "only 2.2 to 2.5 (MetaHuman 5.x) are supported")

    dna = DNA(path=path, version=(generation, version), layers={})
    for _ in range(c.u32()):
        layer_id = struct.pack(">I", c.u32()).decode("latin-1")
        ver, off, size = c.u32(), c.u32(), c.u32()
        dna.layers[layer_id] = (ver, off, size)
        dna.layer_order.append((layer_id, ver))
        dna.raw_layers[layer_id] = bytes(buf[off:off + size])

    def at(layer_id: str) -> _Cursor | None:
        entry = dna.layers.get(layer_id)
        return _Cursor(buf, entry[1]) if entry else None

    if (lc := at("desc")) is not None:
        dna.descriptor = _descriptor(lc)
    if (lc := at("defn")) is not None:
        dna.definition = _definition(lc)
    if (lc := at("bhvr")) is not None:
        dna.behavior = _behavior(lc)
    if (lc := at("geom")) is not None:
        for i in range(lc.u32()):
            size = lc.u32()
            start, end = lc.pos, lc.pos + size
            dna.mesh_raw.append(bytes(buf[start:end]))
            if geometry and (meshes is None or i in meshes):
                dna.meshes.append(_mesh_body(lc, end, blend_shapes))
            else:
                lc.pos = end
                dna.meshes.append(None)
    if (lc := at("mlbh")) is not None:
        dna.ml = _ml(lc)
    if (lc := at("rbfb")) is not None:
        dna.rbf = _rbf(lc)
    if (lc := at("rbfe")) is not None:
        dna.rbf_ext = _rbf_ext(lc)
    if (lc := at("jbmd")) is not None:
        dna.joint_representations = [(lc.u16(), lc.u16(), lc.u16()) for _ in range(lc.u32())]
    if (lc := at("twsw")) is not None:
        dna.twist_swing = _twist_swing(lc)
    return dna
