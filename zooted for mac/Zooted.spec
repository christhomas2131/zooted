# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — builds Zooted.app (macOS menu-bar utility).

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['pystray._darwin'] + collect_submodules('pystray')

a = Analysis(
    ['zooted.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('logo_zoot.png', '.'),
        ('icon_v2.png', '.'),
        ('zooted_head_icon_plate_1024.png', '.'),
        ('zooted_head_icon_1024.png', '.'),
    ],
    hiddenimports=hiddenimports,
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
    [],
    exclude_binaries=True,
    name='Zooted',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Zooted',
)

app = BUNDLE(
    coll,
    name='Zooted.app',
    icon='icon.icns',
    bundle_identifier='com.zooted.app',
    version='1.0.0',
    info_plist={
        # Menu-bar accessory: no Dock icon, no app menu — lives in the status bar.
        'LSUIElement': True,
        'CFBundleName': 'Zooted',
        'CFBundleDisplayName': 'Zooted',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,
    },
)
