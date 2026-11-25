"""Compare our RigLogic (riglogic_expected.py) with Unreal's (ue_riglogic_test.py).

python tests/riglogic_compare.py <expected.json> <ue_actual.json> [--t-tol 0.01] [--r-tol 0.1] [--w-tol 0.01]
Exit code 0 when everything matches within tolerance.
"""
import json
import sys
from pathlib import Path


def arg(name, default):
    return float(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


exp = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["frames"]
act = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["frames"]
T_TOL, R_TOL, W_TOL = arg("--t-tol", 0.01), arg("--r-tol", 0.1), arg("--w-tol", 0.01)
ok = True


def norm(name):
    return name.replace(".", "_").lower()


for f in sorted(exp, key=int):
    if f not in act:
        print(f"frame {f}: missing in Unreal results")
        ok = False
        continue
    e, a = exp[f], act[f]
    # joints
    names = set(e["joints"]) | set(a["joints"])
    bad = []
    for n in names:
        et, er = e["joints"].get(n, [0, 0])
        at, ar = a["joints"].get(n, [0, 0])
        if abs(et - at) > T_TOL or abs(er - ar) > R_TOL:
            bad.append((max(abs(et - at) / T_TOL, abs(er - ar) / R_TOL), n, (et, er), (at, ar)))
    bad.sort(reverse=True)
    print(f"frame {f}: joints moved ours {len(e['joints'])} / Unreal {len(a['joints'])}, "
          f"{len(names) - len(bad)}/{len(names)} match (t {T_TOL} cm, r {R_TOL} deg)")
    for _, n, ev, av in bad[:8]:
        print(f"    {n:40} ours t {ev[0]:.4f} r {ev[1]:.3f}   Unreal t {av[0]:.4f} r {av[1]:.3f}")
    ok &= not bad
    # blend shapes -> head morph targets
    morph = a["curves"]["morph"]
    head = {k.split("__", 1)[1]: v for k, v in morph.items() if k.startswith("head_lod0_mesh__")}
    shapes = set(e["blend_shapes"]) | set(head)
    sbad = [(n, e["blend_shapes"].get(n, 0), head.get(n, 0)) for n in shapes
            if abs(e["blend_shapes"].get(n, 0) - head.get(n, 0)) > W_TOL]
    print(f"    blend shapes: ours {len(e['blend_shapes'])} / Unreal head morphs {len(head)} "
          f"(all morphs {len(morph)}), {len(shapes) - len(sbad)}/{len(shapes)} match")
    for n, ev, av in sorted(sbad)[:8]:
        print(f"    {n:40} ours {ev:.3f}   Unreal {av:.3f}")
    ok &= not sbad
    # animated maps (wrinkle mask multipliers)
    maps = a["curves"].get("maps") or {}
    names = set(e["animated_maps"]) | set(maps)
    mbad = [(n, e["animated_maps"].get(n, 0), maps.get(n, 0)) for n in names
            if abs(e["animated_maps"].get(n, 0) - maps.get(n, 0)) > W_TOL]
    print(f"    wrinkle maps: ours {len(e['animated_maps'])} / Unreal {len(maps)} "
          f"({a['curves'].get('maps_missing', '?')} map curves absent in Unreal), {len(names) - len(mbad)}/{len(names)} match")
    for n, ev, av in sorted(mbad)[:6]:
        print(f"    {n:40} ours {ev:.3f}   Unreal {av:.3f}")
    ok &= not mbad
print("MATCH" if ok else "DIFFERENCES")
sys.exit(0 if ok else 1)
