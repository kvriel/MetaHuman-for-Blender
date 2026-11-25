# Pose Editor

*Part of the [MetaHuman for Blender guide](HOW_TO_USE.md). Previous: [Joints Matching](joints-matching.md). Next: [Groom Exporter](groom-exporter.md).*

The Pose Editor edits the RBF corrective poses that fix shoulders, elbows, knees, the neck and so on
when they bend.

![The Pose Editor panel beside the full body: RBF Solvers with upperarm_l selected, the solver settings, RBF Poses, driven joints, Swing / Twist setups, and Tools, Checks and Export](images/howto/pose.jpg)

## Edit a corrective pose

1. Click **Unpack RigLogic** for the body (or for the head, to get the neck correctives).
2. Pick a solver, then a pose.
3. Click **Edit Selected**. The driver joints go into that pose and the rest of the rig holds still.
4. Move the driven joints until the shape looks right, then click **Update**. **Finish Editing**
   ends the edit.

## Manage poses and solvers

- **Poses:** **Create Pose**, **Rename Pose**, **Delete Pose**, **Mirror** a pose to the other
  side, **Generate In-betweens**, **Copy Driven** and **Paste Driven**, **Set Multiplier** and
  **Toggle Mute**.
- **Solvers:** **New Solver** and **Delete Solver**. Set their driver and driven joints, and click
  **Apply Settings** after changing a solver's settings.

## Twist and swing joints

In **Swing / Twist**, select the twist bones, then the driver bone last, and click **Twist Setup**
or **Swing Setup**.

- **Generate Default Swing Twist Setup** builds the standard ones.
- **Delete Setup** removes one.

## Tools, Checks and Export

- **Replace Names** finds and replaces names.
- **Validate** checks the setup against RigLogic and MetaHuman Creator.
- **Log** shows what happened.
- **Export DNA** writes all solvers, the selected solver only, or the solvers with geometry.
- **Upgrade DNA File** converts an older DNA file to version 2.5.

Your edits go into a working copy next to the .blend file (`<name>_edit.dna`). The live rig reads
that copy, so you see the effect straight away.
