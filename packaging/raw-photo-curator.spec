from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
hiddenimports = collect_submodules("raw_photo_curator")

a = Analysis(
    [str(root / "packaging/macos_entry.py")],
    pathex=[str(root / "src")],
    hiddenimports=hiddenimports,
    datas=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="RAWPhotoCurator", console=False
)
collection = COLLECT(exe, a.binaries, a.datas, name="RAWPhotoCurator")
app = BUNDLE(
    collection,
    name="RAWPhotoCurator.app",
    icon=str(root / "build/RAWPhotoCurator.icns"),
    bundle_identifier="io.github.hyjing.raw-photo-curator",
    info_plist={
        "CFBundleDisplayName": "RAW Photo Curator",
        "CFBundleName": "RAW Photo Curator",
        "CFBundleShortVersionString": "0.2.1",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "Copyright © 2026 RAW Photo Curator contributors",
    },
)
