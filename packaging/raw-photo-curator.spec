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
    bundle_identifier="io.github.hyjing.raw-photo-curator",
    info_plist={"NSHighResolutionCapable": True},
)
