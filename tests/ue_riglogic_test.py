"""Runs inside Unreal Editor: drive the head built from our DNA with Unreal's own RigLogic
(ABP_Face_PostProcess) using the exported face curves, and record joints, morph targets and curves.

UnrealEditor.exe <project> -noxgeshadercompile
    -ExecutePythonScript="tests/ue_riglogic_test.py <export_dir> <out_dir> [frames...] [--fbx <file>] --quit"

<export_dir> holds MFB_head.dna, MFB_face_curves.json and mfb_import_face_curves.py (Export to
Unreal). With --fbx, the head is the character FBX from Export to Unreal instead of the mesh
Unreal's DNA importer builds, so the FBX's morph target names are tested too. Writes <out_dir>/ue_actual.json and <out_dir>/ue_riglogic_log.txt. Needs the
MetaHumanCharacter plugin (MetaHuman Creator Core Data) for ABP_Face_PostProcess. Then:

    python tests/riglogic_expected.py <head.dna> <face_curves.json> <out_dir>/expected.json <frames...>
        --inputs <out_dir>/ue_actual.json
    python tests/riglogic_compare.py <out_dir>/expected.json <out_dir>/ue_actual.json

(Run riglogic_expected.py once without --inputs before the Unreal run: the Unreal script reads the
wrinkle-map names from <out_dir>/expected.json.)
"""
import importlib.util
import json
import math
import sys
import time
import traceback
from pathlib import Path

import unreal

argv = sys.argv[1:]
FBX = None
if "--fbx" in argv:
    k = argv.index("--fbx")
    FBX = Path(argv[k + 1])
    del argv[k:k + 2]
args = [a for a in argv if not a.startswith("--")]
SRC, OUT = Path(args[0]), Path(args[1])
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "ue_riglogic_log.txt"
LOG.write_text("", encoding="utf-8")
CURVES = json.loads((SRC / "MFB_face_curves.json").read_text(encoding="utf-8"))
TIMES = CURVES["times"]
FRAMES = [int(a) for a in args[2:]] or [0, len(TIMES) // 4, len(TIMES) // 2, 3 * len(TIMES) // 4, len(TIMES) - 1]
SAMPLES = FRAMES + [FRAMES[0]]      # the first sample warms the pose up; the last is the neutral
# wrinkle map names, from riglogic_expected.py's output when it is in <out_dir>
_exp = OUT / "expected.json"
MAP_NAMES = json.loads(_exp.read_text(encoding="utf-8")).get("all_animated_maps", []) if _exp.exists() else []
eal = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
DEST = f"/Game/MFB/RigLogic_{int(time.time())}"   # a fresh folder per run
ABP = "/MetaHumanCharacter/Face/ABP_Face_PostProcess"
ARCHETYPE = "/MetaHumanCharacter/Face/Face_Archetype_Skeleton"


def log(msg):
    unreal.log(f"[MFBRL] {msg}")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(str(msg) + "\n")


def keep_alive(on):
    """-ExecutePythonScript closes the editor when the script returns unless it is kept alive."""
    try:
        unreal.EditorPythonScripting.set_keep_python_script_alive(on)
    except Exception as exc:
        log(f"keep alive: {exc}")


def finish():
    try:
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_request_end_play()
    except Exception as exc:
        log(f"end play: {exc}")
    log("ALL DONE")
    keep_alive(False)
    if "--quit" in sys.argv:
        unreal.SystemLibrary.quit_editor()


def import_task(path, dest, options=None, factory=None):
    task = unreal.AssetImportTask()
    for k, v in (("filename", str(path)), ("destination_path", dest), ("automated", True), ("save", False),
                 ("replace_existing", True)):
        task.set_editor_property(k, v)
    if options is not None:
        task.set_editor_property("options", options)
    if factory is not None:
        task.set_editor_property("factory", factory)
    tools.import_asset_tasks([task])
    return [eal.load_asset(p) for p in task.get_editor_property("imported_object_paths") or []]


def dna_of(skm):
    try:
        return skm.get_asset_user_data_of_class(unreal.DNAAsset)
    except Exception as exc:
        log(f"get_asset_user_data_of_class: {exc}")
        return None


# ------------------------------------------------------------------ setup
def setup():
    if FBX is not None:
        # the head from our character FBX
        t0 = time.time()
        objs = import_task(FBX, DEST)
        meshes = [o for o in objs if isinstance(o, unreal.SkeletalMesh)] or [
            a for a in (eal.load_asset(p) for p in eal.list_assets(DEST, recursive=True, include_folder=False))
            if isinstance(a, unreal.SkeletalMesh)]
        if not meshes:
            raise RuntimeError(f"FBX import made no skeletal mesh: {objs}")
        skm = max(meshes, key=lambda m: len(m.get_all_morph_target_names()))
        log(f"FBX import took {time.time() - t0:.0f} s")
    else:
        # the head mesh, built by Unreal's own DNA importer from our DNA
        objs = import_task(SRC / "MFB_head.dna", DEST)
        skm = next((o for o in objs if isinstance(o, unreal.SkeletalMesh)), None)
        if skm is None:
            raise RuntimeError(f"DNA import made no skeletal mesh: {objs}")
    morphs = [str(n) for n in skm.get_all_morph_target_names()]
    log(f"head mesh {skm.get_path_name()}: {len(morphs)} morph targets, "
        f"{sum('__' in n for n in morphs)} named <mesh>__<channel>, e.g. {morphs[:2]}")
    (OUT / "ue_morph_names.json").write_text(json.dumps(morphs), encoding="utf-8")

    # attach the DNA for RigLogic
    if dna_of(skm) is None:
        fn = unreal.DNAImporterLibrary.import_skeletal_mesh_dna
        log(f"import_skeletal_mesh_dna: {fn.__doc__}")
        for call in (lambda: fn(str(SRC / "MFB_head.dna"), skm), lambda: fn(skm, str(SRC / "MFB_head.dna"))):
            try:
                log(f"import_skeletal_mesh_dna -> {call()}")
                break
            except Exception as exc:
                log(f"import_skeletal_mesh_dna failed: {exc}")
    if dna_of(skm) is None:
        try:
            ui = unreal.DNAAssetImportUI()
            log("DNAAssetImportUI properties: " + ", ".join(n for n in dir(ui) if not n.startswith("_"))[:600])
            ui.set_editor_property("skeletal_mesh", skm)
            import_task(SRC / "MFB_head.dna", DEST + "/DNA", options=ui, factory=unreal.DNAAssetImportFactory())
        except Exception as exc:
            log(f"DNAAssetImportFactory failed: {exc}")
    dna = dna_of(skm)
    log(f"[{'PASS' if dna else 'FAIL'}] DNA attached to the head mesh: {dna}")
    if dna is None:
        raise RuntimeError("no DNA on the mesh")

    # let the face post-process anim blueprint run on this skeleton
    skel = skm.get_editor_property("skeleton")
    arch = eal.load_asset(ARCHETYPE)
    for a, b in ((skel, arch), (arch, skel)):
        try:
            a.add_compatible_skeleton(b)
        except Exception as exc:
            log(f"add_compatible_skeleton: {exc}")
    abp = eal.load_asset(ABP)
    abp_class = abp.generated_class()
    try:
        skm.set_editor_property("post_process_anim_blueprint", abp_class)
    except Exception as exc:
        log(f"post_process_anim_blueprint: {exc}")

    # the face curves on an Animation Sequence of this skeleton
    factory = unreal.AnimSequenceFactory()
    factory.set_editor_property("target_skeleton", skel)
    seq = tools.create_asset("MFB_RL_FaceAnim", DEST, unreal.AnimSequence, factory)
    spec = importlib.util.spec_from_file_location("mfb_import_face_curves", SRC / "mfb_import_face_curves.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    n = mod.add_face_curves(seq, str(SRC / "MFB_face_curves.json"))
    log(f"{n} face curves on {seq.get_name()}")

    # an actor that plays it, with the post-process blueprint, in a plain level: the default
    # World Partition map streams the actor out during Simulate (no streaming source)
    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    try:
        made = les.new_level(DEST + "/RL_Level", False)
    except TypeError:
        made = les.new_level(DEST + "/RL_Level")
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    log(f"level {made}: {world.get_path_name() if world else None}")
    actor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(
        unreal.SkeletalMeshActor, unreal.Vector(0, 0, 0))
    comp = actor.get_editor_property("skeletal_mesh_component")
    try:
        comp.set_skeletal_mesh_asset(skm)
    except Exception:
        comp.set_skeletal_mesh(skm)
    comp.set_update_animation_in_editor(True)
    comp.set_override_post_process_anim_bp(abp_class, True)
    comp.set_animation_mode(unreal.AnimationMode.ANIMATION_SINGLE_NODE)
    comp.override_animation_data(seq, False, False, 0.0, 0.0)
    comp.set_component_tick_enabled(True)
    actor.tags = [unreal.Name("MFB_RL")]
    return skm, comp, seq, abp_class


def start_simulate():
    """Animation only advances in a ticking game world: run Simulate and use the actor's copy there."""
    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_play_simulate()


def simulate_comp():
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
    if world is None:
        return None
    for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.SkeletalMeshActor):
        if unreal.Name("MFB_RL") in list(a.tags):
            comp = a.get_editor_property("skeletal_mesh_component")
            comp.set_override_post_process_anim_bp(state["abp"], True)
            comp.set_animation_mode(unreal.AnimationMode.ANIMATION_SINGLE_NODE)
            comp.override_animation_data(state["seq"], False, False, 0.0, 0.0)
            comp.set_play_rate(0.0)
            # RigLogic runs at the mesh's display LOD; the Simulate camera is far away, so force LOD 0
            comp.set_forced_lod(1)
            return comp
    return None


# ------------------------------------------------------------------ sampling across editor ticks
def pose(comp):
    space = unreal.RelativeTransformSpace.RTS_PARENT_BONE_SPACE
    bones = {}
    for i in range(comp.get_num_bones()):
        name = str(comp.get_bone_name(i))
        t = comp.get_socket_transform(name, space)
        q = t.rotation
        bones[name] = [t.translation.x, t.translation.y, t.translation.z, q.x, q.y, q.z, q.w]
    return bones


def _final(comp, name):
    """A value from the component's final curves (inputs, morph targets, material curves)."""
    r = comp.get_curve_value(name, 0.0)
    if isinstance(r, tuple):
        found, value = r[0], r[-1]
        return value if found else None
    return r


def curves(comp, skm):
    """The face inputs Unreal evaluated, RigLogic's morph target weights and wrinkle map values."""
    out = {"inputs": {}, "morph": {}, "maps": {}}
    for name in CURVES["curves"]:
        v = _final(comp, name)
        out["inputs"][name] = 0.0 if v is None else v
    for name in skm.get_all_morph_target_names():
        v = _final(comp, str(name))
        if v is not None and abs(v) > 1e-5:
            out["morph"][str(name)] = v
    missing = 0
    for name in MAP_NAMES:
        v = _final(comp, name.replace(".", "_"))
        if v is None:
            missing += 1
        elif abs(v) > 1e-5:
            out["maps"][name] = v
    out["maps_missing"] = missing
    return out


state = {"i": 0, "wait": 0, "warm": 60, "frames": {}, "neutral": None}


def tick(_dt):
    try:
        if state["warm"] > 0:            # let the editor settle
            state["warm"] -= 1
            if state["warm"] == 0:
                start_simulate()
            return
        if not state.get("sim"):
            comp = simulate_comp()
            state["sim_wait"] = state.get("sim_wait", 0) + 1
            if comp is None:
                if state["sim_wait"] > 600:
                    raise RuntimeError("Simulate did not start")
                return
            state.update(sim=True, comp=comp, ready_wait=0)
            log(f"simulating: anim instance {comp.get_anim_instance()}, post process {comp.get_post_process_instance()}")
            return
        if not state.get("ready"):
            # a new sequence is compressed in the background; until then it plays without curves
            comp = state["comp"]
            peak = max(range(len(TIMES)), key=lambda k: CURVES["curves"]["CTRL_expressions_jawOpen"][k])
            comp.set_position(TIMES[peak], False)
            main = comp.get_anim_instance()
            v = main.get_curve_value("CTRL_expressions_jawOpen") if main else 0.0
            state["ready_wait"] += 1
            if state["ready_wait"] % 120 == 1:
                jaw = comp.get_socket_transform("FACIAL_C_Jaw", unreal.RelativeTransformSpace.RTS_PARENT_BONE_SPACE)
                log(f"waiting for the sequence: tick {state['ready_wait']}, jawOpen curve {v:.3f}, jaw {jaw.rotation}")
            if v > 0.5:
                state["ready"] = True
                log(f"sequence ready after {state['ready_wait']} ticks")
            elif state["ready_wait"] > 3000:
                raise RuntimeError("the face curves never reached the anim instance")
            return
        i = state["i"]
        if i >= len(SAMPLES):
            write_results()
            unreal.unregister_slate_post_tick_callback(state["handle"])
            finish()
            return
        f = SAMPLES[i]
        comp = state["comp"]
        comp.set_position(TIMES[f], False)          # every tick: a paused single-node instance may drift
        state["wait"] += 1
        if state["wait"] in (1, 2, 4, 7):
            main, post = comp.get_anim_instance(), comp.get_post_process_instance()
            jaw = comp.get_socket_transform("FACIAL_C_Jaw", unreal.RelativeTransformSpace.RTS_PARENT_BONE_SPACE).rotation
            log(f"  f{f} tick {state['wait']}: main jawOpen {main.get_curve_value('CTRL_expressions_jawOpen') if main else None}"
                f" post jawOpen {post.get_curve_value('CTRL_expressions_jawOpen') if post else None}"
                f" jaw ({jaw.x:.4f} {jaw.y:.4f} {jaw.z:.4f} {jaw.w:.4f})"
                f" morph {comp.get_morph_target('head_lod0_mesh__jaw_open'):.3f}")
        if state["wait"] < 8:
            return
        comp, skm = state["comp"], state["skm"]
        state["frames"][str(f)] = {"time": TIMES[f], "position": comp.get_position(), "bones": pose(comp),
                                   "curves": curves(comp, skm)}
        c = state["frames"][str(f)]["curves"]
        main, post = comp.get_anim_instance(), comp.get_post_process_instance()
        probe = {"main jawOpen": main.get_curve_value("CTRL_expressions_jawOpen") if main else None,
                 "post jawOpen": post.get_curve_value("CTRL_expressions_jawOpen") if post else None,
                 "post jaw_open morph": post.get_curve_value("head_lod0_mesh__jaw_open") if post else None,
                 "morph jaw_open": comp.get_morph_target("head_lod0_mesh__jaw_open"),
                 "jaw rot": str(comp.get_socket_transform("FACIAL_C_Jaw", unreal.RelativeTransformSpace.RTS_PARENT_BONE_SPACE).rotation),
                 "post class": post.get_class().get_name() if post else None}
        log(f"    {probe}")
        log(f"frame {f} t={TIMES[f]:.3f} pos={comp.get_position():.3f}: jawOpen in {c['inputs'].get('CTRL_expressions_jawOpen', 0):.4f}, "
            f"{len(c['morph'])} morph targets on, {len(c['maps'])} wrinkle maps on ({c['maps_missing']} map curves absent)")
        state["i"] += 1
        state["wait"] = 0
    except Exception:
        log("[FAIL] tick: " + traceback.format_exc())
        unreal.unregister_slate_post_tick_callback(state["handle"])
        finish()


def quat_angle(a, b):
    d = abs(sum(x * y for x, y in zip(a, b)))
    return math.degrees(2 * math.acos(min(1.0, d)))


def write_results():
    frames = state["frames"]
    base = frames[str(FRAMES[0])]["bones"]       # sampled last, after the pose had settled
    out = {"frames": {}}
    for f, fr in frames.items():
        joints = {}
        for name, v in fr["bones"].items():
            b = base[name]
            t = math.dist(v[:3], b[:3])
            r = quat_angle(v[3:], b[3:])
            if t > 1e-4 or r > 1e-3:
                joints[name] = [round(t, 5), round(r, 5)]
        out["frames"][f] = {"time": fr["time"], "position": fr["position"], "joints": joints, "curves": fr["curves"]}
    (OUT / "ue_actual.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    mid = out["frames"].get(str(FRAMES[len(FRAMES) // 2]), {})
    jaw = mid.get("joints", {}).get("FACIAL_C_Jaw")
    log(f"[{'PASS' if jaw and jaw[1] > 5 else 'FAIL'}] Unreal's RigLogic moves the jaw: FACIAL_C_Jaw {jaw}")
    log(f"mesh LOD while sampling: {state['comp'].get_editor_property('forced_lod_model') if state.get('comp') else None}")
    log(f"wrote {OUT / 'ue_actual.json'}")


try:
    skm, comp, seq, abp_class = setup()
    state.update(skm=skm, comp=comp, seq=seq, abp=abp_class)
    keep_alive(True)
    state["handle"] = unreal.register_slate_post_tick_callback(tick)
except Exception:
    log("[FAIL] setup: " + traceback.format_exc())
    finish()
