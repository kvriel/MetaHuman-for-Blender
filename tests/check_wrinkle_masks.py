"""Check addon/mfb/data/wrinkle_masks.json against the mask textures themselves.

python tests/check_wrinkle_masks.py <head.dna> <masks folder> [<Unreal per-region masks folder>]

wrinkle_masks.json is derived from Epic's shader code. This check
does not use that code: it samples each mask texture channel on the head mesh and finds where on
the face the channel paints (weighted centre of the vertices it covers). Then it checks that every
wrinkle map's channel sits where its name says: _L on the character's left, _R mirrored on the right,
brows above the eyes, the lips below them, the neck lowest, and so on. It also checks that the
table covers every wrinkle map in the DNA and that no mask channel is used twice.

With a third folder, Unreal's per-region masks (a UE 5.7 DCC export's SourceAssets/masks: one greyscale
mask per region, named after it), it also checks every table entry directly: the table's channel must
be that region's mask, pixel for pixel, and no other channel may match the region better.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
from mfb.dna.reader import read_dna  # noqa: E402

dna_path, masks = Path(sys.argv[1]), Path(sys.argv[2])
region_masks = Path(sys.argv[3]) if len(sys.argv) > 3 else None
table = json.loads((ROOT / "addon/mfb/data/wrinkle_masks.json").read_text(encoding="utf-8"))
dna = read_dna(dna_path, blend_shapes=False)
d = dna.definition
head = dna.meshes[d.mesh_names.index("head_lod0_mesh")]
ok = True


def check(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{' - ' + detail if detail else ''}")


# one UV per position (the first layout vertex that uses it)
uv_of = np.full((len(head.positions), 2), np.nan, np.float32)
uv_of[head.layout_positions[::-1]] = head.uvs[head.layout_uvs[::-1]]
known = ~np.isnan(uv_of[:, 0])
pos, uv = head.positions[known], uv_of[known]
size = np.ptp(pos, axis=0)

centres, used = {}, {}
images = {}
for name, (tex, ch) in table.items():
    if tex not in images:
        images[tex] = np.asarray(Image.open(masks / f"{tex}.tga").convert("RGBA"), np.float32) / 255.0
    img = images[tex]
    h, w = img.shape[:2]
    px = np.clip((uv[:, 0] * (w - 1)).round().astype(int), 0, w - 1)
    py = np.clip(((1 - uv[:, 1]) * (h - 1)).round().astype(int), 0, h - 1)
    wgt = img[py, px, "rgba".index(ch)]
    if wgt.sum() < 1:
        centres[name] = None
        continue
    centres[name] = (pos * wgt[:, None]).sum(0) / wgt.sum()
    used.setdefault((tex, ch), []).append(name)

maps = {a.split(".", 1)[-1] for a in d.animated_map_names}
check("table covers every wrinkle map in the DNA", maps == set(table),
      f"{len(table)} entries, {len(maps)} maps in the DNA, missing {sorted(maps - set(table))[:3]}")
check("every channel paints part of the face", all(c is not None for c in centres.values()),
      f"empty: {[n for n, c in centres.items() if c is None]}")
check("no mask channel is used by two maps", all(len(v) == 1 for v in used.values()),
      f"{[v for v in used.values() if len(v) > 1]}")

c = {n: v for n, v in centres.items() if v is not None}
# which axis is left-right, up-down: the DNA is y-up; left-right is x
x, y = 0, 1
pairs = [(n, n[:-1] + "R") for n in c if n.endswith("L") and n[:-1] + "R" in c]
side_l = [n for n, _ in pairs if c[n][x] > 0]
check("every _L map paints the character's left (+x), every _R the right", len(side_l) == len(pairs)
      and all(c[r][x] < 0 for _, r in pairs), f"{len(side_l)}/{len(pairs)} pairs")
mirror = max(max(abs(c[l][x] + c[r][x]), abs(c[l][y] - c[r][y])) for l, r in pairs) / size[x]
check("left and right channels mirror each other", mirror < 0.05, f"worst {mirror:.3f} of the head width")


def height(word):
    v = [c[n][y] for n in c if word in n]
    return float(np.mean(v)) if v else float("nan")


order = ["brows", "blink", "lips", "chinRaise", "neckStretch"]
hs = [height(w) for w in order]
check("brows > eyes > lips > chin > neck, top to bottom", all(a > b for a, b in zip(hs, hs[1:])),
      ", ".join(f"{w} {h:.1f}" for w, h in zip(order, hs)))
mid = {w: height(w) for w in ("noseWrinkler", "cheekRaiseInner", "cheekRaiseOuter", "mouthStretch", "smile")}
eyes, lips = height("blink"), height("lips")
check("nose, cheek, smile and mouth-stretch maps lie between the eyes and the chin", all(
    height("chinRaise") < h < eyes for h in mid.values()), ", ".join(f"{w} {h:.1f}" for w, h in mid.items()))
centre_x = max(abs(c[n][x]) for n in c if n.endswith(("jawOpen",))) / size[x]
check("the jaw-open map is centred", centre_x < 0.05, f"{centre_x:.3f} of the head width off centre")
inner_outer = [(f"head_wm1_browsRaiseInner_{s}", f"head_wm1_browsRaiseOuter_{s}") for s in "LR"] + [
    (f"head_wm3_cheekRaiseInner_{s}", f"head_wm3_cheekRaiseOuter_{s}") for s in "LR"]
check("inner brow and cheek maps lie nearer the middle than the outer ones",
      all(abs(c[i][x]) < abs(c[o][x]) for i, o in inner_outer),
      ", ".join(f"{i.split('_', 2)[2]} {abs(c[i][x]):.1f} < {abs(c[o][x]):.1f}" for i, o in inner_outer))
up_down = [(f"head_wm{w}_{part}_U{s}", f"head_wm{w}_{part}_D{s}") for w, part in ((1, "purse"), (13, "lips"))
           for s in "LR"]
check("upper-lip maps lie above the lower-lip ones", all(c[u][y] > c[dn][y] for u, dn in up_down),
      ", ".join(f"{u.split('_', 2)[2]} {c[u][y]:.1f} > {c[dn][y]:.1f}" for u, dn in up_down))

# Direct check against Unreal's per-region masks (UE 5.7 DCC export, SourceAssets/masks): one greyscale
# mask per wrinkle region, named after it. Each table entry's packed channel must be that mask.
if region_masks is not None:
    files = {p.stem.removesuffix("_msk"): p for p in region_masks.glob("*_msk.*")}
    check("Unreal's per-region masks are exactly the table's regions", set(files) == set(table),
          f"{len(files)} masks, only in Unreal {sorted(set(files) - set(table))[:3]}, "
          f"only in the table {sorted(set(table) - set(files))[:3]}")
    channels = {(tex, ch): images[tex][..., "rgba".index(ch)] for tex, ch in {tuple(v) for v in table.values()}}
    worst, swapped = 0.0, []
    for name in sorted(set(files) & set(table)):
        own = np.asarray(Image.open(files[name]).convert("L"), np.float32) / 255.0
        packed = channels[tuple(table[name])]
        if own.shape != packed.shape:
            own = np.asarray(Image.open(files[name]).convert("L").resize(packed.shape[::-1]), np.float32) / 255.0
        worst = max(worst, float(np.abs(own - packed).max()))
        best = min(channels, key=lambda k: float(np.abs(own - channels[k]).mean()))
        if best != tuple(table[name]):
            swapped.append(f"{name}: table {table[name]}, best {list(best)}")
    check("every table channel is the region's own Unreal mask, pixel for pixel", worst <= 2 / 255,
          f"largest difference {worst * 255:.0f}/255")
    check("no other channel matches a region better than the table's", not swapped, "; ".join(swapped[:3]))

print("WRINKLE MASKS OK" if ok else "WRINKLE MASKS: PROBLEMS")
sys.exit(0 if ok else 1)
