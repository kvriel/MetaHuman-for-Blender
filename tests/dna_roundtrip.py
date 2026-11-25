"""Read each DNA file fully, write it back, and require byte-identical output.

python tests/dna_roundtrip.py <file.dna> [...]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addon"))

from mfb.dna.reader import read_dna  # noqa: E402
from mfb.dna.writer import dna_bytes  # noqa: E402

ok = True
for p in sys.argv[1:]:
    original = Path(p).read_bytes()
    t = time.perf_counter()
    dna = read_dna(p)
    out = dna_bytes(dna)
    same = out == original
    first = next((i for i, (a, b) in enumerate(zip(out, original)) if a != b), None) if not same else None
    layers = ", ".join(k for k in ("ml", "rbf", "rbf_ext", "joint_representations", "twist_swing")
                       if getattr(dna, k) is not None)
    print(f"[{'PASS' if same else 'FAIL'}] {Path(p).name}: {len(original)} bytes, "
          f"{(time.perf_counter() - t) * 1000:.0f} ms, decoded extra layers: {layers}"
          + ("" if same else f" (len {len(out)}, first diff at byte {first})"))
    ok &= same

# Upgrade: fake a 2.3 file without the 2.4/2.5 layers, upgrade it, read it back.
if sys.argv[1:]:
    import tempfile
    from mfb.dna.writer import write_dna
    dna = read_dna(sys.argv[1], geometry=False)
    dna.version = (2, 3)
    dna.layer_order = [(lid, v) for lid, v in dna.layer_order if lid in ("desc", "defn", "bhvr", "geom", "mlbh")]
    with tempfile.TemporaryDirectory() as tmp:
        old = write_dna(dna, Path(tmp) / "old.dna")
        new = write_dna(read_dna(old, geometry=False), Path(tmp) / "new.dna", upgrade=True)
        back = read_dna(new)
        good = (back.version == (2, 5) and all(l in back.layers for l in ("rbfb", "rbfe", "jbmd", "twsw"))
                and back.rbf is not None and len(back.rbf.solvers) == 0
                and len(back.meshes) == len(read_dna(sys.argv[1], geometry=False).mesh_raw))
        print(f"[{'PASS' if good else 'FAIL'}] upgrade 2.3 -> 2.5: layers {sorted(back.layers)}")
        ok &= good
sys.exit(0 if ok else 1)
