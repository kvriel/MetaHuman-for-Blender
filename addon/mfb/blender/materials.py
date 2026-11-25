"""Materials, wrinkle maps and a light rig for assembled characters.

Textures are found by name in the textures folder (any image type, any sub-folder).
Wrinkle maps follow the additive rule: for each wrinkle texture layer T,
    weight_T(uv) = clamp( sum over its maps of  map_value * mask_channel(uv) )
    colour = base + sum_T weight_T * (T - base)        (same for tangent normals)
Map values come from RigLogic, written by rig.py as `wm_<map>` properties on the head mesh.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import bpy

IMAGE_EXTS = (".png", ".tga", ".tif", ".tiff", ".exr", ".jpg", ".jpeg", ".dds", ".bmp")
DATA = Path(__file__).resolve().parents[1] / "data"
SKIN_TONE = (0.62, 0.43, 0.35, 1.0)


@lru_cache(maxsize=1)
def wrinkle_masks() -> dict[str, tuple[str, str]]:
    """Wrinkle map region -> (mask texture name, channel letter)."""
    path = DATA / "wrinkle_masks.json"
    return {k: tuple(v) for k, v in json.loads(path.read_text()).items()} if path.exists() else {}


class TextureFinder:
    def __init__(self, *folders: str):
        self.files: dict[str, Path] = {}
        for folder in folders:
            if not folder or not Path(folder).is_dir():
                continue
            for p in sorted(Path(folder).rglob("*")):
                if p.suffix.lower() in IMAGE_EXTS:
                    self.files.setdefault(p.stem.lower(), p)

    def find(self, *stems: str) -> Path | None:
        """First file whose name matches one of the stems (exact, then prefix; 8-bit preferred)."""
        for stem in stems:
            s = stem.lower()
            if s in self.files:
                return self.files[s]
        for stem in stems:
            s = stem.lower()
            hits = sorted((k for k in self.files if k.startswith(s)), key=lambda k: ("16bit" in k, len(k)))
            if hits:
                return self.files[hits[0]]
        return None

    def image(self, *stems: str, color: bool = True) -> bpy.types.Image | None:
        path = self.find(*stems)
        if path is None:
            return None
        img = bpy.data.images.load(str(path), check_existing=True)
        img.colorspace_settings.name = "sRGB" if color else "Non-Color"
        return img


# --------------------------------------------------------------------------- node helpers


class _Nodes:
    def __init__(self, mat: bpy.types.Material):
        mat.use_nodes = True
        self.tree = mat.node_tree
        self.tree.nodes.clear()
        self.n, self.l = self.tree.nodes, self.tree.links
        self.out = self.n.new("ShaderNodeOutputMaterial"); self.out.location = (900, 0)
        self.bsdf = self.n.new("ShaderNodeBsdfPrincipled"); self.bsdf.location = (550, 0)
        self.l.new(self.bsdf.outputs["BSDF"], self.out.inputs["Surface"])

    def tex(self, img, x, y, label=""):
        node = self.n.new("ShaderNodeTexImage")
        node.image = img
        node.location = (x, y)
        node.label = label or (img.name if img else "")
        return node

    def math(self, op, a, b, x, y, clamp=False):
        node = self.n.new("ShaderNodeMath"); node.operation = op; node.use_clamp = clamp
        node.location = (x, y)
        for sock, val in zip(node.inputs, (a, b)):
            if isinstance(val, (int, float)):
                sock.default_value = val
            else:
                self.l.new(val, sock)
        return node.outputs[0]

    def vmath(self, op, a, b, x, y, scale=None):
        node = self.n.new("ShaderNodeVectorMath"); node.operation = op; node.location = (x, y)
        for sock, val in zip(node.inputs, (a, b)):
            if val is None:
                continue
            if isinstance(val, (tuple, list)):
                sock.default_value = val
            else:
                self.l.new(val, sock)
        if scale is not None:
            if isinstance(scale, (int, float)):
                node.inputs["Scale"].default_value = scale
            else:
                self.l.new(scale, node.inputs["Scale"])
        return node.outputs[0]

    def attr(self, name, x, y):
        node = self.n.new("ShaderNodeAttribute"); node.attribute_type = "OBJECT"
        node.attribute_name = name; node.location = (x, y)
        return node.outputs["Fac"]


def _set(bsdf, name, value):
    if name in bsdf.inputs:
        bsdf.inputs[name].default_value = value


def _material(name: str) -> bpy.types.Material:
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    return mat


# --------------------------------------------------------------------------- skin


def _layer_stems(layer: str) -> tuple[str, ...]:
    """File names a wrinkle texture may have: head_cm1_color as in the DNA, or the names Unreal's
    DCC export uses (Head_Basecolor_Animated_CM1, Head_Normal_Animated_WM1)."""
    m = re.fullmatch(r"(\w+?)_(cm|wm)(\d+)_(color|normal)", layer)
    if not m:
        return (layer,)
    part, kind, n, _ = m.groups()
    unreal = f"{part}_basecolor_animated_cm{n}" if kind == "cm" else f"{part}_normal_animated_wm{n}"
    return (layer, unreal)


def _region_mask(finder: TextureFinder, table: dict, region: str) -> tuple[str, str] | None:
    """(mask texture, channel) for a wrinkle region: a channel of a packed mask (head_wm1_msk_01),
    or the region's own greyscale mask (head_wm1_browsRaiseInner_L_msk, as Unreal's DCC export
    writes them)."""
    packed = table.get(region)
    if packed and finder.find(packed[0], packed[0].replace("_msk", "")):
        return packed
    own = f"{region}_msk"
    if finder.find(own):
        return own, "r"
    return packed


def _wrinkle_layers(nodes: _Nodes, finder: TextureFinder, map_names: list[str]):
    """Per wrinkle texture (e.g. head_cm1_color), a socket giving its weight at each pixel."""
    table = wrinkle_masks()
    masks: dict[str, object] = {}
    layers: dict[str, list] = {}
    y = -1400
    for full in map_names:
        layer, _, region = full.partition(".")
        mask = _region_mask(finder, table, region)
        if not mask:
            continue
        mask_tex, channel = mask
        if mask_tex not in masks:
            img = finder.image(mask_tex, mask_tex.replace("_msk", ""), color=False)
            if img is None:
                masks[mask_tex] = None
            else:
                t = nodes.tex(img, -2200, y, mask_tex); y -= 280
                sep = nodes.n.new("ShaderNodeSeparateColor"); sep.location = (-1900, t.location.y)
                nodes.l.new(t.outputs["Color"], sep.inputs["Color"])
                masks[mask_tex] = {"r": sep.outputs[0], "g": sep.outputs[1], "b": sep.outputs[2],
                                   "a": t.outputs["Alpha"]}
        chans = masks[mask_tex]
        if chans is None:
            continue
        ay = -1400 - 90 * sum(len(v) for v in layers.values())
        value = nodes.attr(f"wm_{full.replace('.', '_')}", -1700, ay)
        layers.setdefault(layer, []).append(nodes.math("MULTIPLY", value, chans[channel], -1450, ay))
    weights = {}
    for i, (layer, terms) in enumerate(layers.items()):
        acc = terms[0]
        for k, term in enumerate(terms[1:]):
            acc = nodes.math("ADD", acc, term, -1200, -1400 - 120 * (i * 20 + k))
        weights[layer] = nodes.math("MINIMUM", acc, 1.0, -950, -1400 - 120 * i * 20)
    return weights


def build_skin(name: str, finder: TextureFinder, prefix: str, map_names: list[str] | None = None) -> bpy.types.Material:
    """Skin material for 'head' or 'body', with wrinkle blending when map names are given."""
    mat = _material(name)
    nd = _Nodes(mat)
    b = nd.bsdf
    _set(b, "Roughness", 0.45)
    _set(b, "Subsurface Weight", 0.2)
    _set(b, "Subsurface Radius", (1.0, 0.35, 0.18))
    _set(b, "Subsurface Scale", 0.006)
    _set(b, "Specular IOR Level", 0.5)

    color_img = finder.image(f"{prefix}_color", f"{prefix}_basecolor", f"{prefix}_diffuse")
    normal_img = finder.image(f"{prefix}_normal", color=False)
    color = nd.tex(color_img, -600, 300).outputs["Color"] if color_img else None
    normal = nd.tex(normal_img, -600, -100).outputs["Color"] if normal_img else None
    if color is None:
        _set(b, "Base Color", SKIN_TONE)

    weights = _wrinkle_layers(nd, finder, map_names) if map_names else {}
    if weights and color is not None:
        y = 700
        for layer, w in weights.items():
            if "_color" not in layer:
                continue
            img = finder.image(*_layer_stems(layer))
            if img is None:
                continue
            wc = nd.tex(img, -900, y, layer).outputs["Color"]; y += 280
            delta = nd.vmath("SUBTRACT", wc, color, -650, y)
            color = nd.vmath("ADD", color, nd.vmath("SCALE", delta, None, -450, y, scale=w), -250, y)
    if weights and normal is not None:
        y = -500
        for layer, w in weights.items():
            if "_normal" not in layer:
                continue
            img = finder.image(*_layer_stems(layer), color=False)
            if img is None:
                continue
            wn = nd.tex(img, -900, y, layer).outputs["Color"]; y -= 280
            delta = nd.vmath("SUBTRACT", wn, normal, -650, y)
            normal = nd.vmath("ADD", normal, nd.vmath("SCALE", delta, None, -450, y, scale=w), -250, y)

    if color is not None:
        cavity_img = finder.image(f"{prefix}_cavity", color=False)
        ao_img = finder.image(f"{prefix}_occlusion", f"{prefix}_ao", color=False)
        for img, y in ((cavity_img, 900), (ao_img, 1150)):
            if img is not None:
                mix = nd.n.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.blend_type = "MULTIPLY"
                mix.location = (250, y); mix.inputs["Factor"].default_value = 0.6
                nd.l.new(color, mix.inputs["A"]); nd.l.new(nd.tex(img, -600, y).outputs["Color"], mix.inputs["B"])
                color = mix.outputs["Result"]
        nd.l.new(color, b.inputs["Base Color"])
    if normal is not None:
        nmap = nd.n.new("ShaderNodeNormalMap"); nmap.location = (250, -300)
        nd.l.new(normal, nmap.inputs["Color"]); nd.l.new(nmap.outputs["Normal"], b.inputs["Normal"])
    spec_img = finder.image(f"{prefix}_specular", color=False)
    if spec_img is not None:
        nd.l.new(nd.tex(spec_img, -600, -800).outputs["Color"], b.inputs["Specular IOR Level"])
    return mat


# --------------------------------------------------------------------------- other parts


def build_simple(name: str, finder: TextureFinder, stems: tuple[str, ...], normal_stems: tuple[str, ...] = (),
                 base=(0.8, 0.8, 0.8, 1.0), roughness=0.4, alpha_from_texture=False) -> bpy.types.Material:
    mat = _material(name)
    nd = _Nodes(mat)
    _set(nd.bsdf, "Roughness", roughness)
    img = finder.image(*stems) if stems else None
    if img is not None:
        t = nd.tex(img, -400, 200)
        nd.l.new(t.outputs["Color"], nd.bsdf.inputs["Base Color"])
        if alpha_from_texture:
            nd.l.new(t.outputs["Alpha"], nd.bsdf.inputs["Alpha"])
    else:
        _set(nd.bsdf, "Base Color", base)
    nimg = finder.image(*normal_stems, color=False) if normal_stems else None
    if nimg is not None:
        nmap = nd.n.new("ShaderNodeNormalMap"); nmap.location = (250, -300)
        nd.l.new(nd.tex(nimg, -400, -300).outputs["Color"], nmap.inputs["Color"])
        nd.l.new(nmap.outputs["Normal"], nd.bsdf.inputs["Normal"])
    return mat


def build_clear(name: str, alpha: float, roughness: float = 0.0, ior: float = 1.376) -> bpy.types.Material:
    """Clear parts: eye shell (cornea), tear film, eye edge blend, cartilage."""
    mat = _material(name)
    nd = _Nodes(mat)
    _set(nd.bsdf, "Base Color", (1, 1, 1, 1))
    _set(nd.bsdf, "Roughness", roughness)
    _set(nd.bsdf, "IOR", ior)
    _set(nd.bsdf, "Transmission Weight", 1.0 if alpha > 0 else 0.0)
    _set(nd.bsdf, "Alpha", alpha)
    return mat


def _part(mesh_name: str) -> str:
    return mesh_name.split("_lod")[0]


def apply_materials(meshes: list[bpy.types.Object], textures: str, char_name: str,
                    head_map_names: list[str]) -> dict[str, bpy.types.Material]:
    """Create and assign materials for every assembled mesh. Returns part -> material."""
    # Unreal's DCC export keeps the wrinkle masks next to Maps, in SourceAssets/masks
    extra = Path(textures).parent / "SourceAssets" if textures and Path(textures).name.lower() == "maps" else None
    finder = TextureFinder(textures, str(extra) if extra and extra.is_dir() else "")
    made: dict[str, bpy.types.Material] = {}

    def get(part: str) -> bpy.types.Material:
        if part in made:
            return made[part]
        n = f"{char_name}_{part}"
        if part == "head":
            mat = build_skin(n, finder, "head", head_map_names)
        elif part in ("body", "combined"):
            mat = build_skin(n, finder, "body")
        elif part == "teeth":
            mat = build_simple(n, finder, ("teeth_color",), ("teeth_normal",), base=(0.85, 0.8, 0.72, 1), roughness=0.3)
        elif part in ("eyeLeft", "eyeRight"):
            mat = build_simple(n, finder, (f"{part}_color", "eyes_color"), (f"{part}_normal", "eyes_normal"),
                               base=(0.9, 0.9, 0.9, 1), roughness=0.1)
        elif part == "eyelashes":
            mat = build_simple(n, finder, ("eyelashes_color",), base=(0.05, 0.04, 0.03, 1), roughness=0.6,
                               alpha_from_texture=True)
        elif part == "eyeshell":
            mat = build_clear(n, alpha=0.05, roughness=0.02)
        elif part == "saliva":
            mat = build_clear(n, alpha=0.15, roughness=0.05, ior=1.33)
        else:                                   # eyeEdge, cartilage, anything unknown and see-through
            mat = build_clear(n, alpha=0.0)
        made[part] = mat
        return mat

    for obj in meshes:
        part = _part(obj.data.name)
        mat = get(part)
        obj.data.materials.clear()
        obj.data.materials.append(mat)
        if part == "head" and head_map_names:
            obj["mfb_wrinkles"] = True
            for full in head_map_names:
                obj[f"wm_{full.replace('.', '_')}"] = 0.0
    return made


def build_light_rig(collection: bpy.types.Collection, target: tuple[float, float, float],
                    name: str = "MFB_Lights") -> list[bpy.types.Object]:
    """Three area lights (key, fill, rim) aimed at the head, plus a soft world."""
    from mathutils import Vector
    rigs = []
    specs = (("Key", (0.9, -1.1, 0.45), 120.0, 0.9, (1.0, 0.96, 0.9)),
             ("Fill", (-1.0, -0.9, 0.1), 45.0, 1.2, (0.9, 0.95, 1.0)),
             ("Rim", (0.2, 1.1, 0.5), 90.0, 0.6, (1.0, 1.0, 1.0)))
    t = Vector(target)
    for label, offset, power, size, color in specs:
        data = bpy.data.lights.new(f"{name}_{label}", "AREA")
        data.energy, data.size, data.color = power, size, color
        obj = bpy.data.objects.new(f"{name}_{label}", data)
        obj.location = t + Vector(offset)
        obj.rotation_euler = (t - obj.location).to_track_quat("-Z", "Y").to_euler()
        collection.objects.link(obj)
        rigs.append(obj)
    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.05, 0.055, 0.06, 1.0)
        bg.inputs["Strength"].default_value = 1.0
    return rigs
