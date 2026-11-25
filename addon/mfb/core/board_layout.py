"""Face board layout: where each face control's knob sits, plus the board's drawing and labels.

Our own drawing. The arrangement follows the way DNA rigs are usually animated: a face with each
knob placed over the part of the face it moves, panels for the mouth shapes beside it, and a second,
smaller head for the fine "tweaker" controls. Everything is in board units: one unit is a knob's
travel from 0 to 1. The character's left side is on the viewer's right, as when facing the character.

Pure Python, so the layout can be checked (no overlapping knob ranges) without Blender.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

PAD = 0.4               # space between a knob's range and its frame
PITCH = 1.2             # distance between neighbouring one-axis knobs in a row

# ----------------------------------------------------------------------------- the face
# knob rest positions of the left-side (L) and centre (C) controls; right-side ones are mirrored
FACE = {
    "CTRL_L_brow_raiseIn": (2.2, 12.0), "CTRL_L_brow_raiseOut": (7.0, 12.0),
    "CTRL_L_brow_down": (3.6, 9.8), "CTRL_L_brow_lateral": (8.6, 10.0),
    "CTRL_L_nose_wrinkleUpper": (1.4, 7.4), "CTRL_C_eye_parallelLook": (0.0, 6.0),
    "CTRL_L_eye_pupil": (5.5, 7.6), "CTRL_L_eye": (5.5, 4.5),
    "CTRL_L_eye_blink": (9.6, 4.5), "CTRL_L_eye_lidPress": (11.0, 4.5),
    "CTRL_L_eye_squintInner": (3.0, 1.0), "CTRL_L_eye_cheekRaise": (7.6, 0.6),
    "CTRL_L_ear_up": (16.4, 1.9),
    "CTRL_L_nose": (2.0, -1.8),
    "CTRL_L_mouth_stretchLipsClose": (0.9, -5.4), "CTRL_L_mouth_upperLipRaise": (2.6, -5.8),
    "CTRL_L_mouth_suckBlow": (10.4, -4.6),
    "CTRL_C_mouth": (0.0, -8.6),
    "CTRL_L_mouth_sharpCornerPull": (4.2, -7.2), "CTRL_L_mouth_cornerPull": (5.6, -7.8),
    "CTRL_L_mouth_dimple": (7.0, -8.8), "CTRL_L_mouth_cornerDepress": (5.6, -10.6),
    "CTRL_L_mouth_stretch": (4.2, -11.4), "CTRL_L_mouth_lowerLipDepress": (2.4, -12.4),
    "CTRL_L_jaw_chinCompress": (6.4, -13.8),
    "CTRL_L_jaw_ChinRaiseU": (1.0, -15.0), "CTRL_L_jaw_ChinRaiseD": (1.0, -17.4),
    "CTRL_C_jaw": (0.0, -20.6), "CTRL_L_jaw_clench": (3.6, -20.4),
    "CTRL_C_jaw_fwdBack": (5.0, -20.4), "CTRL_C_jaw_openExtreme": (-5.0, -20.4),
    "CTRL_L_neck_stretch": (3.2, -25.0), "CTRL_L_neck_mastoidContract": (5.2, -25.4),
    "CTRL_neck_throatExhaleInhale": (-1.2, -25.6), "CTRL_neck_digastricUpDown": (0.0, -25.6),
    "CTRL_neck_throatUpDown": (1.2, -25.6), "CTRL_C_neck_swallow": (0.0, -29.4),
}
FACE_LABELS = [("brows", 0.0, 14.4), ("eyes", 11.6, 7.6), ("nose", -5.6, -1.0), ("mouth", -9.6, -8.4),
               ("jaw", -9.6, -19.8), ("neck", -10.6, -25.0)]

# ----------------------------------------------------------------------------- panels
# (title, rows); a row is a list of (group label, [controls]); "R/L" expands to the R then the L control
SIDE_LEFT = [
    ("sticky lips", [[("", ["CTRL_R_mouth_stickyOuterU", "CTRL_R_mouth_stickyInnerU", "CTRL_C_mouth_stickyU",
                            "CTRL_L_mouth_stickyInnerU", "CTRL_L_mouth_stickyOuterU"])],
                     [("", ["CTRL_R_mouth_stickyOuterD", "CTRL_R_mouth_stickyInnerD", "CTRL_C_mouth_stickyD",
                            "CTRL_L_mouth_stickyInnerD", "CTRL_L_mouth_stickyOuterD"])],
                     [("lip sticky", ["R/L mouth_lipSticky"])]]),
    ("lip shapes", [[("towards", ["R/L mouth_towardsU"]), ("purse", ["R/L mouth_purseU"]),
                     ("funnel", ["R/L mouth_funnelU"])],
                    [("", ["R/L mouth_towardsD"]), ("", ["R/L mouth_purseD"]), ("", ["R/L mouth_funnelD"])]]),
    ("lip tension", [[("bite", ["R/L mouth_lipBiteU"]), ("press", ["R/L mouth_lipsPressU"]),
                      ("tighten", ["R/L mouth_tightenU"])],
                     [("", ["R/L mouth_lipBiteD"]), ("", ["R/L mouth_lipsPressD"]), ("", ["R/L mouth_tightenD"])]]),
]
SIDE_RIGHT = [
    ("tongue", [[("in/out", ["CTRL_C_tongue_inOut"]), ("move", ["CTRL_C_tongue_move"]),
                 ("press", ["CTRL_C_tongue_press"]), ("wide", ["CTRL_C_tongue_wideNarrow"])],
                [("bend/twist", ["CTRL_C_tongue_bendTwist"]), ("roll", ["CTRL_C_tongue_roll"]),
                 ("tip", ["CTRL_C_tongue_tipMove"]), ("thick", ["CTRL_C_tongue_thickThin"])]]),
    ("lips together & press", [[("together", ["R/L mouth_lipsTogetherU"]), ("press", ["R/L mouth_pressU"])],
                               [("", ["R/L mouth_lipsTogetherD"]), ("", ["R/L mouth_pressD"])]]),
    ("cheeks blow", [[("lips blow", ["R/L mouth_lipsBlow"])]]),
]

# ----------------------------------------------------------------------------- the tweaker head
TWEAK = {
    "CTRL_L_eye_faceScrunch": (4.6, 9.4),
    "CTRL_L_eyelashes_tweakerIn": (3.0, 5.6), "CTRL_L_eyelashes_tweakerOut": (4.4, 5.6),
    "CTRL_L_eye_eyelidU": (7.8, 6.0), "CTRL_L_eye_eyelidD": (7.8, 2.4),
    "CTRL_L_nose_nasolabialDeepen": (2.6, -0.8),
}
TWEAK_LABELS = [("face scrunch", 0.0, 11.4), ("lashes", 3.7, 7.8), ("lids", 9.6, 4.2), ("nasolabial", 0.0, 1.0)]
TWEAK_ROWS = [
    [("thickness", ["R/L mouth_thicknessU"]), ("inward", ["R/L mouth_thicknessInwardU"]),
     ("roll", ["R/L mouth_lipsRollU"]), ("sharpness", ["R/L mouth_cornerSharpnessU"]),
     ("push/pull", ["R/L mouth_pushPullU"])],
    [("", ["R/L mouth_thicknessD"]), ("", ["R/L mouth_thicknessInwardD"]), ("", ["R/L mouth_lipsRollD"]),
     ("", ["R/L mouth_cornerSharpnessD"]), ("", ["R/L mouth_pushPullD"])],
    [("teeth upper", ["CTRL_C_teethU", "CTRL_C_teeth_fwdBackU"]), ("lip shift", ["CTRL_C_mouth_lipShiftU",
                                                                                 "CTRL_C_mouth_lipShiftD"]),
     ("teeth lower", ["CTRL_C_teethD", "CTRL_C_teeth_fwdBackD"])],
    [("towards teeth", ["R/L mouth_lipsTowardsTeethU", "R/L mouth_lipsTowardsTeethD"]),
     ("corners", ["CTRL_R_mouth_corner", "CTRL_L_mouth_corner"])],
]

FACE_CENTRE = (0.0, 0.0)
TWEAK_CENTRE = (52.0, 4.0)
LEFT_X = -36.0          # left edge of the left panels
RIGHT_X = 18.5          # left edge of the right panels
PANELS_TOP = -6.0


@dataclass
class Layout:
    knobs: dict = field(default_factory=dict)        # control -> (x, y) rest position
    lines: list = field(default_factory=list)        # polylines [(x, y), ...]
    labels: list = field(default_factory=list)       # (text, x, y, size, centred)


def _mirror(name: str) -> str | None:
    return name.replace("_L_", "_R_", 1) if "_L_" in name else None


def _expand(entry: str) -> list[str]:
    if entry.startswith("R/L "):
        rest = entry[4:]
        return [f"CTRL_R_{rest}", f"CTRL_L_{rest}"]
    return [entry]


def _extent(ranges: dict, name: str):
    """(x0, x1, y0, y1) of a knob's travel around its rest position, frame padding included."""
    axes = ranges.get(name, {})
    x0, x1 = axes.get(0, (0.0, 0.0))
    y0, y1 = axes.get(1, (0.0, 0.0))
    return x0 - PAD, x1 + PAD, y0 - PAD, y1 + PAD


def _ellipse(cx, cy, rx, ry, a0=0.0, a1=2 * math.pi, n=48):
    return [(cx + rx * math.cos(a0 + (a1 - a0) * i / n), cy + ry * math.sin(a0 + (a1 - a0) * i / n))
            for i in range(n + 1)]


def _head(cx, cy, w, top, chin, ear_y=None, neck=None):
    """Head outline: round cranium, cheeks narrowing to the chin, optional ears and neck."""
    pts = []
    n = 64
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        s, c = math.sin(a), math.cos(a)
        if s >= 0:
            pts.append((cx + w * c, cy + top * s))
        else:
            taper = 1.0 - 0.3 * (-s) ** 3
            pts.append((cx + w * c * taper, cy + chin * s))
    lines = [pts]
    if ear_y is not None:
        for side in (-1, 1):
            lines.append(_ellipse(cx + side * (w + 0.9), cy + ear_y, 1.3, 2.6,
                                  -math.pi / 2 if side > 0 else math.pi / 2,
                                  math.pi / 2 if side > 0 else 3 * math.pi / 2, 16))
    if neck is not None:
        for side in (-1, 1):
            x = cx + side * w * 0.5
            lines.append([(x, cy - chin * 0.86), (x + side * 0.6, cy + neck)])
    return lines


def _place_rows(lay: Layout, ranges: dict, rows, x_left: float | None, top: float, centre_x: float | None = None):
    """Place rows of grouped knobs. Returns (x0, x1, y_bottom) of what was placed."""
    y = top
    xs = []
    for row in rows:
        groups = [(label, [c for e in entries for c in _expand(e)]) for label, entries in row]
        if not any(c in ranges for _, names in groups for c in names):
            continue
        groups = [(label, names) for label, names in groups if any(c in ranges for c in names) or not label]
        # widths first, to centre the row if asked
        widths = []
        for _, names in groups:
            w = 0.0
            for k, nm in enumerate(names):
                x0, x1, _, _ = _extent(ranges, nm)
                w += (x1 - x0) + (PITCH - 0.8 if k else 0.0)
            widths.append(w)
        total = sum(widths) + 1.2 * (len(groups) - 1)
        x = x_left if centre_x is None else centre_x - total / 2
        label_space = 1.2 if any(label for label, _ in groups) else 0.0
        row_top = max(_extent(ranges, nm)[3] for _, names in groups for nm in names if nm in ranges)
        row_bot = min(_extent(ranges, nm)[2] for _, names in groups for nm in names if nm in ranges)
        rest_y = y - label_space - row_top
        for (label, names), w in zip(groups, widths):
            gx = x
            for k, nm in enumerate(names):
                x0, x1, _, _ = _extent(ranges, nm)
                if k:
                    x += PITCH - 0.8
                if nm in ranges:                     # a control the rig lacks keeps its slot empty
                    lay.knobs[nm] = (x - x0, rest_y)
                x += x1 - x0
            if label:
                lay.labels.append((label, (gx + x) / 2, y - 0.3, 0.65, True))
            xs += [gx, x]
            x += 1.2
        y = rest_y + row_bot - 0.5
    return (min(xs), max(xs), y) if xs else (x_left or 0.0, x_left or 0.0, top)


def _panels(lay: Layout, ranges: dict, panels, x_left: float, top: float):
    y = top
    for title, rows in panels:
        x0, x1, bottom = _place_rows(lay, ranges, rows, x_left + 0.8, y - 2.0)
        if x1 <= x0:
            continue
        x1 = max(x1, x0 + 0.55 * len(title))
        lay.lines.append([(x_left, y), (x1 + 0.8, y), (x1 + 0.8, bottom - 0.3), (x_left, bottom - 0.3), (x_left, y)])
        lay.labels.append((title, x_left + 0.6, y - 0.5, 1.0, False))
        y = bottom - 1.6


def layout(ranges: dict) -> Layout:
    """ranges: {control: {axis: (min, max)}} with axis 0 = x (tx), 1 = y (ty), in control units."""
    lay = Layout()

    def put(table, centre):
        for name, (x, y) in table.items():
            for nm, sx in ((name, 1.0), (_mirror(name), -1.0)):
                if nm and nm in ranges:
                    lay.knobs[nm] = (centre[0] + sx * x, centre[1] + y)

    put(FACE, FACE_CENTRE)
    cx, cy = FACE_CENTRE
    lay.lines += _head(cx, cy, 15.4, 16.5, 23.5, ear_y=2.4, neck=-31.0)
    for side in (-1, 1):                                       # eyes and mouth, as drawing guides
        lay.lines.append(_ellipse(cx + side * 5.5, cy + 4.5, 3.4, 2.0))
    lay.lines.append(_ellipse(cx, cy - 8.6, 3.0, 1.9))
    lay.labels += [(t, cx + x, cy + y, 1.0, True) for t, x, y in FACE_LABELS]

    _panels(lay, ranges, SIDE_LEFT, LEFT_X, PANELS_TOP)
    _panels(lay, ranges, SIDE_RIGHT, RIGHT_X, PANELS_TOP)

    put(TWEAK, TWEAK_CENTRE)
    tx, ty = TWEAK_CENTRE
    _, _, bottom = _place_rows(lay, ranges, TWEAK_ROWS, None, ty - 3.4, centre_x=tx)
    lay.lines += _head(tx, ty, 14.0, 14.0, ty - bottom + 2.4, ear_y=3.6)
    lay.labels += [(t, tx + x, ty + y, 0.8, True) for t, x, y in TWEAK_LABELS]
    lay.labels.append(("TWEAKERS", tx, ty + 16.6, 1.8, True))
    lay.labels.append(("Follow head and eye aim: sidebar, MetaHuman > Face Rig", tx, ty - (ty - bottom + 2.4) - 2.0,
                       0.7, True))

    # anything this layout does not know (other rig definitions) goes in a last panel
    rest = [n for n in ranges if n not in lay.knobs]
    if rest:
        rows = [[("", rest[i:i + 12])] for i in range(0, len(rest), 12)]
        _panels(lay, ranges, [("other", rows)], RIGHT_X, _lowest(lay, ranges) - 2.0)
    return lay


def _lowest(lay: Layout, ranges: dict) -> float:
    return min(y + _extent(ranges, n)[2] for n, (_, y) in lay.knobs.items())


def frames(lay: Layout, ranges: dict) -> dict:
    """{control: (x0, x1, y0, y1)} frame rectangle of each placed knob."""
    out = {}
    for n, (x, y) in lay.knobs.items():
        x0, x1, y0, y1 = _extent(ranges, n)
        out[n] = (x + x0, x + x1, y + y0, y + y1)
    return out


def overlaps(lay: Layout, ranges: dict) -> list[tuple[str, str]]:
    f = frames(lay, ranges)
    items = list(f.items())
    bad = []
    for i, (a, ra) in enumerate(items):
        for b, rb in items[i + 1:]:
            if ra[0] < rb[1] and rb[0] < ra[1] and ra[2] < rb[3] and rb[2] < ra[3]:
                bad.append((a, b))
    return bad


def bounds(lay: Layout, ranges: dict) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for r in frames(lay, ranges).values():
        xs += r[:2]; ys += r[2:]
    for line in lay.lines:
        xs += [p[0] for p in line]; ys += [p[1] for p in line]
    return min(xs), max(xs), min(ys), max(ys)
