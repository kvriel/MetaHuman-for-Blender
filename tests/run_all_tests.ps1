# Runs every test suite and prints one line per suite: passed / failed.
# Usage: powershell -File tests\run_all_tests.ps1
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$blender = "C:\Program Files\Blender Foundation\Blender 5.3\blender.exe"
$py = "C:\Users\Ken\AppData\Local\Programs\Python\Python312\python.exe"
$arch = "C:\Program Files\Epic Games\UE_5.7\Engine\Plugins\MetaHuman\MetaHumanCoreTechLib\Content\ArchetypeDNA"
$ref = "$root\_reference"
$tmpl = (Get-ChildItem $ref -Recurse -Filter template.dna -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -like "*mh_expression_editor*\MH.6\*" } | Select-Object -First 1).FullName
$masks = (Get-ChildItem $ref -Recurse -Directory -Filter masks -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -like "*mh_character_assembler*\assets\shared\masks" } | Select-Object -First 1).FullName
$out = "$root\tests\out"
# Unreal's per-region wrinkle masks, from a UE 5.7 DCC export of any MetaHuman in tests/out/doc_metahuman
# (optional: the check runs without them)
$ueMasks = Get-ChildItem "$out\doc_metahuman" -Recurse -Directory -Filter masks -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -like "*\SourceAssets\masks" } | Select-Object -First 1 -ExpandProperty FullName

function Run-Suite($name, [scriptblock]$cmd) {
    $o = & $cmd 2>&1 | Out-String
    $pass = ([regex]::Matches($o, "\[PASS\]")).Count
    $fail = ([regex]::Matches($o, "\[FAIL\]|Traceback")).Count
    "{0,-28} {1,3} passed  {2,3} failed" -f $name, $pass, $fail
    if ($fail) { ($o -split "`n" | Select-String "FAIL|Traceback|Error" | Select-Object -First 5) -join "`n" }
}

Run-Suite "DNA round trip" { & $py tests\dna_roundtrip.py "$arch\SKM_Face.dna" "$arch\SKM_Body.dna" $tmpl }
Run-Suite "DNA editing" { & $py tests\dna_edit_test.py "$arch\SKM_Body.dna" }
Run-Suite "Wrinkle masks" { if ($ueMasks) { & $py tests\check_wrinkle_masks.py $tmpl $masks $ueMasks } else { & $py tests\check_wrinkle_masks.py $tmpl $masks } }
Run-Suite "Expression maths" { & $py tests\expression_core_test.py $tmpl }
Run-Suite "Face features (Blender)" { & $blender -b --factory-startup --python tests\blender_face_features_test.py -- $tmpl }
Run-Suite "Character library (Blender)" { & $blender -b --factory-startup --python tests\blender_library_test.py -- $arch "$out\library_test" }
Run-Suite "Body rig (Blender)" { & $blender -b --factory-startup --python tests\blender_body_rig_test.py -- $arch "$out\body_rig" }
Run-Suite "Pose Editor (Blender)" { & $blender -b --factory-startup --python tests\blender_pose_test.py -- $arch "$out\pose" }
Run-Suite "Expression Ed. (Blender)" { & $blender -b --factory-startup --python tests\blender_expression_test.py -- $tmpl "$out\expr" }
Run-Suite "Groom Exporter (Blender)" { & $blender -b --factory-startup --python tests\blender_groom_test.py -- "$out\groom" }
Run-Suite "Unreal export (Blender)" { & $blender -b --factory-startup --python tests\blender_export_test.py -- $arch "$out\ue_export" $tmpl }
