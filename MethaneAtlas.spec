# -*- mode: python ; coding: utf-8 -*-
#
# Methane Atlas desktop build.
#
# The entire built site — map, 798 plumes, plume imagery, TROPOMI overlays,
# facility records — is bundled into the executable under "site", so the app
# runs with nothing else on the machine but a browser. That is ~26 MB of data
# and makes for a chunky exe, which is the right trade: a launcher that depends
# on the repository still being at a particular path is a launcher that breaks
# the first time the folder moves.
#
# Rebuild the site first, or this ships whatever was last exported:
#     cd web && npm run build
#     C:\Python313\python.exe -m PyInstaller MethaneAtlas.spec --noconfirm

a = Analysis(
    ['desktop\\launch.py'],
    pathex=[],
    binaries=[],
    datas=[('web/out', 'site')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Methane Atlas',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # No console: this is a double-click app. Every failure path therefore
    # writes to %LOCALAPPDATA%\MethaneAtlas\launch.log instead of a terminal.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['desktop\\methane-atlas.ico'],
)
