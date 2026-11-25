# Groom Exporter

*Part of the [MetaHuman for Blender guide](HOW_TO_USE.md). Previous: [Pose Editor](pose-editor.md). Next: [Export to Unreal](export-to-unreal.md).*

The Groom Exporter writes Blender hair to Alembic files that Unreal imports as grooms.

![The Groom Exporter panel beside the character's head: Open and Save presets, the list of hair objects (two layers of long hair and the eyebrows), Set Group ID and Toggle Guides, the export options and Export Groom](images/howto/groom.jpg)

## Export hair

1. Open the **Groom Exporter** panel. It lists the hair Curves objects in the scene.
2. Give each groom a group with **Set Group ID**, and a name. Use **Toggle Guides** to mark guide
   curves.
3. Click **Export Groom** to write an Alembic file with the attributes Unreal's groom importer
   expects. It uses centimetres and Y-up by default.

**Save Preset** and **Open Preset** store and reload the export settings.

## Guides

Guides must share a group ID with the hair they guide. Unreal does not import a group that holds
only guides, so the exporter refuses to write one.
