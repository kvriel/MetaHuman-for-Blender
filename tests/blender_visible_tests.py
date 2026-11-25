"""Run every test suite inside a visible Blender window, one after another.

    blender --factory-startup --python tests/blender_visible_tests.py

After each suite the viewport frames what the suite built and a screenshot is saved to
tests/out/visible/. When all suites have run, the results open in a Text Editor and Blender
stays open so you can look around. Results: tests/out/visible/results.txt
"""
import contextlib
import io
import runpy
import sys
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon"
OUT = ROOT / "tests" / "out"
VIS = OUT / "visible"
VIS.mkdir(parents=True, exist_ok=True)
ARCH = r"C:\Program Files\Epic Games\UE_5.7\Engine\Plugins\MetaHuman\MetaHumanCoreTechLib\Content\ArchetypeDNA"
TMPL = str(next((ROOT / "_reference").glob("**/mh_expression_editor/res/MH.6/template.dna"), ""))
sys.path.insert(0, str(ADDON))

SUITES = [
    ("DNA round trip", "dna_roundtrip.py", [f"{ARCH}\\SKM_Face.dna", f"{ARCH}\\SKM_Body.dna", TMPL]),
    ("DNA editing", "dna_edit_test.py", [f"{ARCH}\\SKM_Body.dna"]),
    ("Expression maths", "expression_core_test.py", [TMPL]),
    ("Face rig features", "blender_face_features_test.py", ["--", TMPL]),
    ("Body rig (RBF, twist)", "blender_body_rig_test.py", ["--", ARCH, str(OUT / "body_rig")]),
    ("Pose Editor", "blender_pose_test.py", ["--", ARCH, str(OUT / "pose")]),
    ("Expression Editor", "blender_expression_test.py", ["--", TMPL, str(OUT / "expr")]),
    ("Groom Exporter", "blender_groom_test.py", ["--", str(OUT / "groom")]),
    ("Export to Unreal", "blender_export_test.py", ["--", ARCH, str(OUT / "ue_export")]),
]
LOG = VIS / "results.txt"
LOG.write_text("", encoding="utf-8")
summary: list[str] = []
state = {"i": 0, "phase": "run"}


def log(line: str) -> None:
    print(line)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def unregister_all() -> None:
    mfb = sys.modules.get("mfb")
    if mfb is None:
        return
    for mod in reversed(mfb._blender_modules()):
        try:
            mod.unregister()
        except Exception:
            pass


def view3d():
    win = bpy.context.window_manager.windows[0]
    for area in win.screen.areas:
        if area.type == "VIEW_3D":
            region = next(r for r in area.regions if r.type == "WINDOW")
            return win, area, region
    return win, None, None


def frame_scene() -> None:
    win, area, region = view3d()
    if area is None:
        return
    area.spaces.active.shading.type = "SOLID"
    for o in bpy.context.view_layer.objects:
        if o.type == "MESH" and not o.hide_get():
            o.select_set(True)
    with bpy.context.temp_override(window=win, area=area, region=region):
        try:
            bpy.ops.view3d.view_selected()
        except Exception:
            bpy.ops.view3d.view_all()
    area.tag_redraw()


def shot(name: str) -> None:
    win, area, _ = view3d()
    with bpy.context.temp_override(window=win, area=area or win.screen.areas[0]):
        bpy.ops.screen.screenshot(filepath=str(VIS / f"{name}.png"), check_existing=False)


def run_suite() -> float:
    name, script, args = SUITES[state["i"]]
    log(f"=== {name}")
    sys.argv = [bpy.app.binary_path, *args]
    buf = io.StringIO()
    t = time.perf_counter()
    try:
        with contextlib.redirect_stdout(buf):
            runpy.run_path(str(ROOT / "tests" / script), run_name="__main__")
    except SystemExit:
        pass
    except Exception:
        buf.write("[FAIL] crashed\n" + traceback.format_exc())
    text = buf.getvalue()
    lines = [l for l in text.splitlines() if l.startswith(("[PASS]", "[FAIL]")) or "Traceback" in l or "Error:" in l]
    for l in lines:
        log("  " + l)
    passed = sum(l.startswith("[PASS]") for l in lines)
    failed = sum(l.startswith("[FAIL]") for l in lines)
    summary.append(f"{name:<24} {passed:3d} passed {failed:3d} failed   ({time.perf_counter() - t:.0f} s)")
    frame_scene()
    return 1.0


def finish() -> None:
    log("")
    log("SUMMARY")
    for s in summary:
        log("  " + s)
    total_p = sum(int(s.split()[-6]) for s in summary)
    total_f = sum(int(s.split()[-4]) for s in summary)
    log(f"  TOTAL {total_p} passed, {total_f} failed")
    log("ALL DONE")
    txt = bpy.data.texts.new("MFB Test Results")
    txt.write(LOG.read_text(encoding="utf-8"))
    win, area, _ = view3d()
    for a in win.screen.areas:
        if a.type == "PROPERTIES":
            a.type = "TEXT_EDITOR"
            a.spaces.active.text = txt
            a.spaces.active.show_word_wrap = True
            break


def runner():
    try:
        if state["phase"] == "run":
            if state["i"] >= len(SUITES):
                finish()
                state["phase"] = "done"
                return None
            unregister_all()
            delay = run_suite()
            state["phase"] = "shot"
            return delay
        if state["phase"] == "shot":
            name = SUITES[state["i"]][0]
            shot(f"{state['i'] + 1:02d}_{name.split(' (')[0].replace(' ', '_').lower()}")
            state["i"] += 1
            state["phase"] = "run"
            return 0.5
    except Exception:
        log("[FAIL] runner: " + traceback.format_exc())
        state["i"] += 1
        state["phase"] = "run"
        return 0.5
    return None


bpy.app.timers.register(runner, first_interval=3.0, persistent=True)
