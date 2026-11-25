# Expression Poses

*Part of the [MetaHuman for Blender guide](HOW_TO_USE.md), Expression Editor. Previous: [Neutral Pose](neutral-pose.md). Next: [Skin Weights](skin-weights.md).*

The **Expression Poses** tab of the Expression Editor changes how an expression moves the face:
how far the brows lift, how the lips purse, how the cheeks fold in a smile.

![The Expression Editor on its Expression Poses tab beside the character's head: the expression list with browDownL selected, Correctives, Isolate and Highlight, Sculpt Expression and Apply Sculpt, and the propagation settings](images/howto/expression.jpg)

The Expression Editor has four tabs; the others have their own pages:
[Neutral Pose](neutral-pose.md), [Skin Weights](skin-weights.md) and
[Joints Matching](joints-matching.md).

## Change an expression

1. Open the **Expression Editor**, pick the character's rig at the top and switch to the
   **Expression Poses** tab.
2. Pick an expression from the list. **Refresh Expressions** reloads the list.
3. Click **Sculpt Expression**. You get a copy of the head in that expression.
4. Sculpt the copy, then click **Apply Sculpt**. The change becomes the expression's corrective
   shape. Tick **Keep Sculpt Mesh** to keep the copy afterwards.
5. **Propagation** decides what happens to the combination shapes that build on this expression:
   - **Keep Combinations** takes the change out of them again, so combined poses look the same as
     before;
   - **Carry Into Combinations** leaves them alone, so the change shows in every combination.

   **Propagation Strength** (advanced) sets how much of the change Keep Combinations takes out.
   Expressions you lock with **Toggle Lock** are never changed.

## Working with the expression graph

Expressions build on each other: a combination shape such as a smile with the jaw open depends on
the smile and on the open jaw.

- **Correctives** shows the combination shapes in the list too.
- **Upstream**, **Downstream** and **Both** mark the expressions the selected one depends on, the
  ones that depend on it, or both. **Isolate** then shows only that branch in the list, and
  **Highlight** picks out the expression you selected last. The colour menu next to **Toggle Lock**
  gives an expression a colour of its own.
- **Preview Selected** shows the selected expression on the face, with every other control at
  zero; **Neutral** returns the face to rest.
- **Preview Downstream** makes preview meshes of the combination shapes that depend on the
  selected expression (**Unlocked Only**: just the unlocked ones); the bin button deletes them.

## Bookmarks, Animation and Files

- Save groups of expressions as **Bookmarks**. Eye and full-face presets are included.
- Make a **Range-of-Motion Animation** to check every expression.
- Save and load a **Settings File**.

## Saving your work

- Your changes go into the working copy of the DNA next to your .blend file (`<name>_edit.dna`),
  which the live rig reads.
- **Save MetaHuman DNA** writes them to a DNA file of your own.
- **Export FBX** writes the head as Y-up or Z-up.

To use the new DNA in Unreal, see [Export to Unreal](export-to-unreal.md).
