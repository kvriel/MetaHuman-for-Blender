# MetaHuman for Blender guide

Everything is in the 3D Viewport sidebar (**N**), tab **MetaHuman**, and in the
**MetaHuman** menu in the top bar (it opens each tool as a floating panel).

## Character Assembler
1. **MetaHumans Directory**: the folder with your exported MetaHumans (MetaHuman Creator's
   Export to DCC: one sub-folder per character with `head.dna`, `body.dna`, `Maps` and a
   picture). The folder is remembered between sessions. A single character's folder works too.
2. Click a character in the grid (type in **Filter** to narrow it; the refresh button looks again).
3. **Head**, **Body**, **Textures**, **Combine Skeletons** (off: two armatures, the head
   following the body).
4. **Advanced** (optional): DNA and texture paths by hand or **Load MetaHuman Folder**, name,
   LODs, single meshes, blend shapes, face board, materials, light rig, new scene, units.
5. **Assemble**. You get the armature (face joints in the hidden "Face Joints" bone
   collection), meshes, shape keys, skin weights, materials with wrinkle maps, the face board,
   eye aim controls, and live RigLogic for face and body (correctives and twist joints).

## Face Rig
- **Face Board / Raw Attributes**: animate the board knobs, or the raw control attributes on
  `FACIAL_C_FacialRoot` (the curves Unreal reads).
- The face board stands beside the head. Select it and switch to Pose Mode; the knobs are yellow.
  Each knob sits over the part of the face it moves (brows, eyes, nose, mouth, jaw, neck). The panels
  on the left hold sticky lips, lip shapes (towards, purse, funnel) and lip tension (bite, press,
  tighten). The panels on the right hold the tongue, lips together and press, and cheeks blow. The
  **TWEAKERS** head holds the fine controls: face scrunch, eyelashes, eyelids, nasolabial, lip
  thickness, roll, corner sharpness, push/pull, teeth, lip shift, lips towards teeth and the mouth
  corners. Each frame shows how far its knob can move.
- **Eyes Look at Aim**, **Board/Aim Follows Head**, **Key Face**, **Reset Face**.

## Expression Editor
- **Neutral Pose**: edit or sculpt the head's Basis, then **Apply Neutral**. Joints follow
  (least-squares matching) and lower LODs are updated.
- **Expression Poses**: pick an expression, **Sculpt Expression**, sculpt the copy, **Apply
  Sculpt**. The sculpt becomes the expression's corrective shape. Propagation can keep the
  combination shapes as they were (locked expressions are never changed).
- **Skin Weights**: weight-paint, then **Apply Skin Weights**.
- **Joints Matching**: fit joints to the current neutral; copy joint motion between expressions.
- Branch selection, isolate, colours, previews of downstream shapes, bookmarks (with eye and
  full-face presets), range-of-motion animation, settings files, **Save MetaHuman DNA**,
  **Export FBX** (Y-up or Z-up).

## Pose Editor
- **Unpack RigLogic** for the body (or the neck correctives of the head).
- Pick a solver and a pose; **Edit Selected** poses the drivers and holds the rig still; move the
  driven joints; **Update**. Create, rename, delete, mirror, in-betweens, copy/paste, multiplier,
  mute, driver and driven joints, solver settings.
- Swing/twist setups: select the twist bones, then the driver bone last, **Twist Setup**.
- Find and replace names, **Validate for RigLogic / MetaHuman Creator**, log, **Export DNA**
  (all, selected solver only, with geometry).

Edits are kept in a working copy (`<name>_edit.dna` next to the .blend) that the live rig reads.

## Groom Exporter
Lists hair Curves objects. Set group IDs, names and guides, then **Export** Alembic with
Unreal's groom attributes (cm, Y-up by default). Every strand carries its group's name
(`groom_group_name`; the group ID when it has no name), which Unreal shows as the group's name. Presets save and load the settings.
Guides must share a group ID with the hair they guide: Unreal does not import a group of guides
on their own, so the exporter refuses that.

## Export to Unreal
Writes the head and body DNA, the character FBX, the face animation curves as JSON, and
`mfb_import_face_curves.py`. In Unreal, select an Animation Sequence and run that script
with the JSON path to add the `CTRL_expressions_*` curves. With the head's DNA on its skeletal mesh and
the MetaHuman face post-process anim blueprint, Unreal's RigLogic turns them into the same pose as in
Blender. This works for the character FBX too: its morph targets are named `<mesh>__<channel>`
(e.g. `head_lod0_mesh__jaw_open`), the names Unreal's RigLogic looks for, at full length even where
that is longer than Blender allows for shape key names. Unreal leaves out morph targets that move no
vertex by more than a fraction of a millimetre. RigLogic follows the mesh's display LOD: the template head has blend shapes only at LOD 0 and
wrinkle maps at LOD 0-1.

## Not included
- Machine-learning joint matching (needs Epic's model, which cannot be redistributed).
- Upgrading an MH.4 rig to the MH.6 rig definition: it rebuilds the rig from Epic's MH.6 template
  and rig definition files, which cannot be redistributed. The file-format upgrade to DNA 2.5 is included.
