# -*- mode: python ; coding: utf-8 -*-
#
# Desktop application for Linux. Produces a single self-contained executable
# in dist/obi-linux that bundles Python, Tk and pyserial.

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Modules and interfaces are imported by name at runtime, so the
        # directories have to exist on disk for pkgutil to scan them.
        ('modules', 'modules'),
        ('interfaces', 'interfaces'),
        ('icon.png', '.')
    ],
    hiddenimports=[
        'modules', 'modules.makita_lxt',
        'interfaces', 'interfaces.arduino_obi',
        'components', 'components.default_module', 'components.logging_frame',
        'core', 'core.makita', 'core.obi_link', 'core.sampling', 'core.serial_ports',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tests', 'tools'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='obi-linux',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.png',
)
