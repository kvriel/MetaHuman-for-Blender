"""Package the macOS installer: a zip holding the extension zip and the double-clickable scripts.

    python installer/package_macos.py <version> <extension.zip> <output.zip>

The zip holds one folder, "MetaHuman for Blender <version>", with the two .command scripts
(marked executable, LF line endings), READ ME.txt and the extension zip.
"""
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "installer" / "macos"
SCRIPTS = ("Install MetaHuman for Blender.command", "Uninstall MetaHuman for Blender.command")


def add(zf: zipfile.ZipFile, name: str, data: bytes, mode: int) -> None:
    info = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
    info.create_system = 3                      # Unix, so macOS keeps the permissions
    info.external_attr = (0o100000 | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


def main() -> None:
    version, ext_zip, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    folder = f"MetaHuman for Blender {version}/"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as zf:
        for name in SCRIPTS:
            text = (SRC / name).read_bytes().replace(b"\r\n", b"\n")
            add(zf, folder + name, text, 0o755)
        add(zf, folder + "READ ME.txt", (SRC / "READ ME.txt").read_bytes().replace(b"\r\n", b"\n"), 0o644)
        add(zf, folder + f"mfb-{version}.zip", ext_zip.read_bytes(), 0o644)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
