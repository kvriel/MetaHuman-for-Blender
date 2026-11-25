"""Drive the installed extension in a real Blender window, step by step, with screenshots.

Run with the extension installed (e.g. in a throwaway profile via BLENDER_USER_RESOURCES):
    blender --python tests/blender_gui_test.py -- <archetype_dna_folder> <out_dir>
Each step runs from a timer so the window, depsgraph and handlers behave as for a user.
Results go to <out_dir>/results.txt; screenshots to <out_dir>/NN_name.png.
"""
import math
import sys
import time
import traceback
from pathlib import Path

import addon_utils
import bpy
from mathutils import Euler

ARGS = sys.argv[sys.argv.index("--") + 1:]
FOLDER, OUT = ARGS[0], Path(ARGS[1])
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "results.txt"
LOG.write_text("")
state: dict = {}
shot_no = [0]


def log(msg: str) -> None:
    print("[gui-test]", msg)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


def check(label: str, cond: bool, detail: str = "") -> None:
    log(f"[{'PASS' if cond else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")


def mod(name: str):
    return sys.modules[f"bl_ext.user_default.mfb.blender.{name}"]


def win_area(kind="VIEW_3D"):
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == kind)
    region = next(r for r in area.regions if r.type == "WINDOW")
    return win, area, region


def shot(name: str) -> None:
    shot_no[0] += 1
    win, area, _ = win_area()
    path = OUT / f"{shot_no[0]:02d}_{name}.png"
    with bpy.context.temp_override(window=win, area=area):
        bpy.ops.screen.screenshot(filepath=str(path), check_existing=False)
    log(f"screenshot {path.name}")


def view(loc, dist, rot=(math.pi / 2, 0, 0), shading="SOLID"):
    _, area, _ = win_area()
    space = area.spaces.active
    r3d = space.region_3d
    r3d.view_perspective = "PERSP"
    r3d.view_location = loc
    r3d.view_distance = dist
    r3d.view_rotation = Euler(rot).to_quaternion()
    space.shading.type = shading
    space.show_region_ui = True
    for r in area.regions:
        if r.type == "UI":
            try:
                r.active_panel_category = "MetaHuman"
            except Exception as exc:  # read-only on some versions
                state.setdefault("tab_note", str(exc))
    area.tag_redraw()


def rig_parts():
    rig = mod("rig")
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE" and o.get(rig.PROP_RIG))
    board = bpy.data.objects[arm[rig.PROP_BOARD_NAME]]
    return rig, arm, board


# --------------------------------------------------------------------------- steps


def s_start():
    enabled = [m for m in addon_utils.modules() if m.__name__.endswith(".mfb")]
    check("extension is installed and enabled", bool(enabled) and addon_utils.check(enabled[0].__name__)[1])
    check("Character Assembler panel registered", hasattr(bpy.types, "MFB_PT_assembler"))
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    view((0, 0, 1.0), 3.5)
    return 1.0


def s_empty_panel():
    shot("empty_panel")
    if "tab_note" in state:
        log(f"note: could not switch sidebar tab ({state['tab_note']})")
    return 0.2


def s_load_folder():
    s = bpy.context.scene.mfb_assembler
    s.library = FOLDER                      # MetaHumans Directory; a single character's folder lists itself
    check("MetaHumans Directory picks the character", s.character and s.head_dna.endswith("SKM_Face.dna"),
          f"character={Path(s.character).name}")
    r = bpy.ops.mfb.load_folder("EXEC_DEFAULT", directory=FOLDER)
    s.combine_skeletons = True              # the steps below check the combined rig
    s.show_advanced = True
    check("Load MetaHuman Folder finds head and body", r == {"FINISHED"} and s.head_dna.endswith("SKM_Face.dna")
          and s.body_dna.endswith("SKM_Body.dna"), f"head={Path(s.head_dna).name} body={Path(s.body_dna).name}")
    s.lods[1] = True
    s.filter_meshes = True
    bpy.ops.mfb.refresh_meshes()
    check("mesh list for LOD0+1", len(s.meshes) == 20, f"{len(s.meshes)} meshes listed")
    for m in s.meshes:                      # untick saliva to test the filter
        if m.name.startswith("saliva"):
            m.use = False
    s.lights = True
    return 0.5


def s_filled_panel():
    shot("filled_panel")
    return 0.2


def s_assemble():
    t = time.perf_counter()
    r = bpy.ops.mfb.assemble()
    dt = time.perf_counter() - t
    check("Assemble runs", r == {"FINISHED"}, f"{dt:.1f}s")
    names = {o.name for o in bpy.data.objects}
    check("mesh filter skipped saliva", not any(n.startswith("saliva") for n in names))
    check("LOD collections made", {"MetaHuman_LOD0", "MetaHuman_LOD1"} <= {c.name for c in bpy.data.collections}
          or any(c.name.endswith("_LOD1") for c in bpy.data.collections))
    check("light rig made", sum(1 for o in bpy.data.objects if o.type == "LIGHT") == 3)
    _, arm, _ = rig_parts()
    face = arm.data.collections.get("Face Joints")
    check("face joints in a hidden bone collection", face is not None and not face.is_visible
          and len(face.bones) > 500, f"{len(face.bones) if face else 0} face joints hidden")
    view((0, 0, 1.0), 3.6, shading="MATERIAL")
    return 2.0


def s_body_shot():
    shot("assembled_body")
    view((0.12, 0, 1.55), 1.0, shading="MATERIAL")
    return 1.0


def s_face_shot():
    shot("face_neutral")
    rig, arm, board = rig_parts()
    u = board["mfb_board_unit"]
    state["jaw0"] = arm.pose.bones["FACIAL_C_Jaw"].rotation_euler.x
    board.pose.bones["CTRL_C_jaw"].location.y = u              # like a user dragging the knob
    board.pose.bones["CTRL_L_mouth_cornerPull"].location.y = u
    board.pose.bones["CTRL_R_mouth_cornerPull"].location.y = u
    return 1.0


def s_after_drag():
    rig, arm, board = rig_parts()
    jaw = arm.pose.bones["FACIAL_C_Jaw"].rotation_euler.x
    check("moving board knobs updates the face live (handler fired by itself)",
          abs(jaw - state["jaw0"]) > 0.2, f"jaw rx {math.degrees(jaw):.1f}°")
    check("raw attribute follows the board", abs(arm.pose.bones[rig.FACE_ROOT]["jawOpen"] - 1) < 1e-4)
    shot("face_jaw_smile")
    return 0.2


def s_eye_aim():
    rig, arm, board = rig_parts()
    aim = bpy.data.objects[arm[rig.PROP_AIM_NAME]]
    aim[rig.PROP_AIM_SWITCH] = 1.0
    aim.location.x += 0.15
    aim.update_tag()
    return 1.0


def s_eye_check():
    rig, arm, board = rig_parts()
    look = arm.pose.bones[rig.FACE_ROOT]
    left = look.get("eyeLookLeftL", 0) + look.get("eyeLookLeftR", 0)
    right = look.get("eyeLookRightL", 0) + look.get("eyeLookRightR", 0)
    check("eye aim to the character's left turns on look-left", left > 0.3 and right < 0.05,
          f"left={left:.2f} right={right:.2f}")
    shot("eye_aim")
    aim = bpy.data.objects[arm[rig.PROP_AIM_NAME]]
    aim[rig.PROP_AIM_SWITCH] = 0.0
    aim.location.x -= 0.15
    return 0.5


def s_keys():
    rig, arm, board = rig_parts()
    sc = bpy.context.scene
    bpy.ops.mfb.reset_face()
    sc.frame_set(1)
    bpy.ops.mfb.key_face()
    sc.frame_set(25)
    board.pose.bones["CTRL_C_jaw"].location.y = board["mfb_board_unit"]
    bpy.ops.mfb.key_face()
    sc.frame_set(13)
    return 0.8


def s_keys_check():
    rig, arm, board = rig_parts()
    knob = board.pose.bones["CTRL_C_jaw"].location.y / board["mfb_board_unit"]
    raw = arm.pose.bones[rig.FACE_ROOT]["jawOpen"]
    check("keyed board plays back (frame 13 is between the keys)", 0.3 < knob < 0.7 and abs(raw - knob) < 0.05,
          f"knob {knob:.2f}, jawOpen {raw:.2f}")
    sc = bpy.context.scene
    t = time.perf_counter()
    for f in range(1, 26):
        sc.frame_set(f)
    fps = 25 / (time.perf_counter() - t)
    check("playback speed with live RigLogic", fps > 10, f"{fps:.0f} frames/s (frame_set incl. depsgraph)")
    return 0.3


def s_raw_mode():
    rig, arm, board = rig_parts()
    bpy.context.scene.frame_set(1)
    arm.animation_data_clear() if False else None
    r = bpy.ops.mfb.set_drive(armature=arm.name, mode="RAW")
    root = arm.pose.bones[rig.FACE_ROOT]
    root["browRaiseInL"] = 1.0
    root["browRaiseInR"] = 1.0
    arm.update_tag()
    state["brow_before"] = arm.pose.bones["FACIAL_L_ForeheadIn"].location.copy() \
        if "FACIAL_L_ForeheadIn" in arm.pose.bones else None
    check("switch to Raw Attributes", r == {"FINISHED"} and arm[rig.PROP_DRIVE] == "RAW")
    return 0.8


def s_raw_check():
    rig, arm, board = rig_parts()
    head = next(o for o in bpy.data.objects if o.name.startswith("head_lod0"))
    wm = sum(v for k, v in head.items() if k.startswith("wm_") and "browsRaiseInner" in k)
    check("raw attributes drive the face (inner brow wrinkle on)", wm > 0.5, f"wrinkle weight sum {wm:.2f}")
    shot("raw_mode_brows")
    bpy.ops.mfb.set_drive(armature=arm.name, mode="BOARD")
    return 0.5


def s_save():
    path = OUT / "gui_test.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    state["blend"] = str(path)
    bpy.ops.wm.open_mainfile(filepath=str(path))
    return 1.5


def s_after_reopen():
    rig, arm, board = rig_parts()
    bpy.context.scene.frame_set(1)
    before = arm.pose.bones["FACIAL_C_Jaw"].rotation_euler.x
    board.pose.bones["CTRL_C_jaw"].animation_data_clear() if False else None
    if board.animation_data:
        board.animation_data.action = None
    board.pose.bones["CTRL_C_jaw"].location.y = board["mfb_board_unit"]
    state["reopen_before"] = before
    return 1.0


def s_reopen_check():
    rig, arm, board = rig_parts()
    jaw = arm.pose.bones["FACIAL_C_Jaw"].rotation_euler.x
    check("live rig still works after saving and reopening the file",
          abs(jaw - state["reopen_before"]) > 0.2, f"jaw rx {math.degrees(jaw):.1f}°")
    view((0.12, 0, 1.55), 1.0, shading="MATERIAL")
    return 1.0


def s_after_reopen_shot():
    shot("after_reopen")
    return 0.3


def _popup(kind: str, name: str) -> None:
    win, area, region = win_area()
    with bpy.context.temp_override(window=win, area=area, region=region):
        if kind == "menu":
            bpy.ops.wm.call_menu(name=name)
        else:
            bpy.ops.wm.call_panel(name=name, keep_open=True)


def s_menu():
    check("top-bar MetaHuman menu registered", hasattr(bpy.types, "MFB_MT_main"))
    check("all tool panels registered", all(hasattr(bpy.types, p) for p in
          ("MFB_PT_assembler", "MFB_PT_expression", "MFB_PT_pose", "MFB_PT_groom", "MFB_PT_export")))
    _popup("menu", "MFB_MT_main")
    return 1.0


def s_menu_shot():
    shot("menu")
    rig, arm, board = rig_parts()
    sc = bpy.context.scene
    sc.mfb_pose.armature, sc.mfb_pose.kind = arm, "body"
    r = bpy.ops.mfb.pose_unpack()
    check("Pose Editor unpacks the body rig in the GUI", r == {"FINISHED"} and len(sc.mfb_pose.solvers) > 50,
          f"{len(sc.mfb_pose.solvers)} solvers")
    sc.mfb_pose.solver_index = [s.name for s in sc.mfb_pose.solvers].index("upperarm_l_UERBFSolver")
    _popup("panel", "MFB_PT_pose")
    return 1.0


def s_pose_shot():
    shot("pose_editor")
    rig, arm, board = rig_parts()
    sc = bpy.context.scene
    sc.mfb_expr.armature = arm
    bpy.ops.mfb.expr_refresh()
    sc.mfb_expr.mode = "EXPRESSION"
    check("Expression Editor lists expressions in the GUI", len(sc.mfb_expr.expressions) > 200,
          f"{len(sc.mfb_expr.expressions)} items")
    _popup("panel", "MFB_PT_expression")
    return 1.0


def s_expr_shot():
    shot("expression_editor")
    _popup("panel", "MFB_PT_groom")
    return 1.0


def s_groom_shot():
    shot("groom_exporter")
    log("done")
    return None


STEPS = [s_start, s_empty_panel, s_load_folder, s_filled_panel, s_assemble, s_body_shot, s_face_shot,
         s_after_drag, s_eye_aim, s_eye_check, s_keys, s_keys_check, s_raw_mode, s_raw_check, s_save,
         s_after_reopen, s_reopen_check, s_after_reopen_shot, s_menu, s_menu_shot, s_pose_shot, s_expr_shot,
         s_groom_shot]
idx = [0]


def runner():
    if idx[0] >= len(STEPS):
        bpy.ops.wm.quit_blender()
        return None
    step = STEPS[idx[0]]
    idx[0] += 1
    try:
        delay = step()
    except Exception:
        log(f"[ERROR] {step.__name__}:\n{traceback.format_exc()}")
        delay = 0.3
    if delay is None:
        idx[0] = len(STEPS)
        delay = 0.5
    return delay


bpy.app.timers.register(runner, first_interval=2.0, persistent=True)
