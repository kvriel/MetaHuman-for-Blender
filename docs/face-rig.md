# Face Rig

*Part of the [MetaHuman for Blender guide](HOW_TO_USE.md). Previous: [Character Assembler](character-assembler.md). Next: [Neutral Pose](neutral-pose.md).*

The **Face Rig** panel controls how you drive the face of an [assembled character](character-assembler.md).

![The Face Rig panel floating beside the character's head, with the face board and the TWEAKERS head to the right of it](images/howto/face_rig.jpg)

## Face Board or Raw Attributes

- With **Face Board** you animate the knobs on the board beside the head.
- With **Raw Attributes** you animate the control attributes on `FACIAL_C_FacialRoot` directly.
  These are the same curves Unreal reads.

## Using the board

Select the board and switch to Pose Mode. The yellow knobs are the controls.

![The head smiling, posed from the face board: the main board laid out like a face, with panels for lips, tongue and cheeks, and the TWEAKERS head](images/face_board.jpg)

- **Main board:** shaped like a face. Each knob sits over the part of the face it moves: brows,
  eyes, nose, mouth, jaw and neck.
- **Left panels:** sticky lips, lip shapes (towards, purse, funnel) and lip tension (bite, press,
  tighten).
- **Right panels:** the tongue, lips together and press, and cheeks blow.
- **TWEAKERS head:** the fine controls, such as face scrunch, eyelids, nasolabial folds, lip
  thickness and roll, teeth and the mouth corners.
- **Limits:** each knob's frame shows how far it can move.

## The other controls

- **Eyes Look at Aim:** points the eyes at the aim control.
- **Board/Aim Follows Head:** keeps the board and the aim with the head as it turns.
- **Key Face:** sets a keyframe on every face control at the current frame.
- **Reset Face:** returns every face control to neutral.

Animate as you would any rig: pose the knobs, key them, scrub the timeline. The wrinkle maps switch
on by themselves as the expression changes.

To bring the animation into Unreal, see [Export to Unreal](export-to-unreal.md).
