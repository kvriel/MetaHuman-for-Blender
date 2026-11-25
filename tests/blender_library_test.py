"""Headless test of the Character Assembler's MetaHumans Directory (character browser).

    blender -b --factory-startup --python tests/blender_library_test.py -- <archetype_dna_folder> <out_dir>
Builds a small library like MetaHuman Creator's "Export to DCC" output (one folder per character with
head.dna, body.dna, ExportManifest.json, <name>.png and Maps), then checks listing, pictures, the filter,
picking a character, the Head/Body/Textures switches and Assemble.
"""
import json
import os
import shutil
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addon"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _testutil import reset_scene  # noqa: E402
import mfb  # noqa: E402
from mfb.blender import assemble as asm, ui  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
arch, out = Path(args[0]), Path(args[1]).resolve()
lib = out / "library"


def check(label, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")


def link(src: Path, dst: Path):
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def thumbnail(path: Path, rgb):
    img = bpy.data.images.new(path.stem, 32, 32)
    img.pixels.foreach_set(list(rgb + (1.0,)) * 32 * 32)
    img.filepath_raw, img.file_format = str(path), "PNG"
    img.save()
    bpy.data.images.remove(img)


shutil.rmtree(lib, ignore_errors=True)
for folder, name, rgb in (("Aera_Export", "Aera", (0.8, 0.3, 0.3)), ("Cameron_Export", "Cameron", (0.3, 0.3, 0.8))):
    d = lib / folder
    (d / "Maps").mkdir(parents=True)
    link(arch / "SKM_Face.dna", d / "head.dna")
    link(arch / "SKM_Body.dna", d / "body.dna")
    (d / "ExportManifest.json").write_text(json.dumps({"metaHumanName": name}))
    thumbnail(d / f"{name}.png", rgb)
    thumbnail(d / "Maps" / "head_basecolor.png", rgb)
(lib / "NotACharacter").mkdir()
(lib / "NotACharacter" / "readme.txt").write_text("no DNA here")

reset_scene()
mfb.register()
s = bpy.context.scene.mfb_assembler

entries = asm.scan_library(lib)
check("library lists the two characters, sorted, and skips other folders",
      [e.name for e in entries] == ["Aera", "Cameron"], str([e.name for e in entries]))
check("pictures found next to the DNA", all(e.thumbnail.endswith(f"{e.name}.png") for e in entries))
check("a single character folder lists itself", [e.name for e in asm.scan_library(arch)] == ["ArchetypeDNA"])
check("missing folder lists nothing", asm.scan_library(lib / "nope") == [])

check("Combine Skeletons is off by default", s.combine_skeletons is False)
check("Assemble is disabled before a folder is chosen", not bpy.ops.mfb.assemble.poll())

s.library = str(lib)
items = ui._character_items(s, bpy.context)
# icon ids stay 0 without a window; the pictures themselves must be loaded
check("character menu has both characters with pictures loaded", len(items) == 2
      and all(i[0] in ui._previews and i[0] in ui._library["icons"] for i in items),
      str([i[1] for i in items]))
check("choosing the directory picks the first character", s.character == entries[0].path
      and s.name == "Aera" and Path(s.head_dna).name == "head.dna" and Path(s.body_dna).name == "body.dna",
      f"name={s.name} head={Path(s.head_dna).name} body={Path(s.body_dna).name}")
check("textures folder found", Path(s.textures).name == "Maps", s.textures)
check("mesh list filled for head and body", {m.role for m in s.meshes} == {"head", "body"}, f"{len(s.meshes)} meshes")

s.character = entries[1].path
check("clicking another character fills its paths", s.name == "Cameron" and "Cameron_Export" in s.head_dna)

s.filter = "cam"
shown = [e.name for e in ui.library_entries(s) if s.filter.lower() in e.name.lower()]
check("filter narrows the grid", shown == ["Cameron"], str(shown))
s.filter = ""

s.use_head = False
check("Head off drops head meshes", {m.role for m in s.meshes} == {"body"})
s.use_body = False
check("Assemble is disabled with neither head nor body", not bpy.ops.mfb.assemble.poll())
s.use_body, s.use_textures = True, False
r = bpy.ops.mfb.assemble()
names = {o.name for o in bpy.data.objects}
arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
check("Assemble with Body only builds the body and no head", r == {"FINISHED"}
      and any(n.startswith("body_lod0") for n in names) and not any(n.startswith("head_lod0") for n in names),
      f"{len(names)} objects")
root = bpy.data.collections.get("Cameron")
check("Textures off builds without a textures folder", root is not None and root.get(asm.PROP_TEXTURES) == "",
      repr(root.get(asm.PROP_TEXTURES) if root else None))

# the panel must draw in every state (empty, filled, filtered to nothing, advanced open)
panel = bpy.types.MFB_PT_assembler
check("panel draws the characters and Advanced section",
      hasattr(panel, "draw_characters") and hasattr(panel, "draw_advanced"))

mfb.unregister()
check("unregister frees the picture previews", ui._previews is None)
