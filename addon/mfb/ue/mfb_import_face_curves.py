"""Unreal Editor script: add face animation curves exported from Blender to an Animation Sequence.

In Unreal: select the Animation Sequence in the Content Browser, then run
    py "<path>/mfb_import_face_curves.py" "<path>/<name>_face_curves.json"
(Output Log, Python mode, or Tools > Execute Python Script). Existing curves of the same name
are replaced. Curve names follow the MetaHuman face (CTRL_expressions_<control>).
From other scripts: import this file and call add_face_curves(sequence, json_path).
"""
import json
import math
import sys

import unreal


def add_face_curves(seq, json_path: str) -> int:
    """Add (or replace) every curve from the JSON on `seq`. Returns the number of curves."""
    data = json.loads(open(json_path, encoding="utf-8").read())
    times = [float(t) for t in data["times"]]
    lib = unreal.AnimationLibrary
    _fit_length(seq, times[-1] if times else 0.0)
    float_type = unreal.RawCurveTrackTypes.RCT_FLOAT
    for name, values in data["curves"].items():
        if lib.does_curve_exist(seq, name, float_type):
            lib.remove_curve(seq, name, False)
        lib.add_curve(seq, name, float_type, False)
        lib.add_float_curve_keys(seq, name, times, [float(v) for v in values])
    unreal.EditorAssetLibrary.save_loaded_asset(seq)
    return len(data["curves"])


def _fit_length(seq, seconds: float) -> None:
    """Make the sequence at least `seconds` long, keeping its own frame rate."""
    lib = unreal.AnimationLibrary
    length = lib.get_sequence_length(seq)
    if length >= seconds - 1e-4:
        return
    frames = max(lib.get_num_frames(seq), 1)
    fps = frames / length if length > 0 else 30.0
    needed = int(math.ceil(seconds * fps - 1e-6))
    ctrl = seq.get_editor_property("controller")
    ctrl.open_bracket(unreal.Text("Fit length for face curves"), False)
    ctrl.set_number_of_frames(unreal.FrameNumber(needed), False)
    ctrl.close_bracket(False)


def main(json_path: str) -> None:
    assets = [a for a in unreal.EditorUtilityLibrary.get_selected_assets() if isinstance(a, unreal.AnimSequence)]
    if not assets:
        unreal.log_error("Select an Animation Sequence in the Content Browser first")
        return
    for seq in assets:
        with unreal.ScopedEditorTransaction("Import face curves"):
            n = add_face_curves(seq, json_path)
        unreal.log(f"Added {n} face curves to {seq.get_name()}")


if __name__ == "__main__":
    main(sys.argv[1])
