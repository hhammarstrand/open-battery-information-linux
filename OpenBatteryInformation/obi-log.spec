# -*- mode: python ; coding: utf-8 -*-
#
# Headless command line logger. Console application, no Tk: this is what you
# put on a machine that only needs to record a battery over time.

a = Analysis(
    ['obi_cli.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'core', 'core.makita', 'core.obi_link', 'core.sampling', 'core.serial_ports',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'tests', 'tools'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='obi-log',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
