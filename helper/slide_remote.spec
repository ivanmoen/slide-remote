# PyInstaller spec for SlideRemoteHelper.exe
# Build with: python build.py   (or: pyinstaller slide_remote.spec)
#
# Two non-obvious bits this spec handles:
#   1. bless + bleak load WinRT runtime modules dynamically. PyInstaller's
#      static analyzer won't see those imports — collect_all picks them up.
#   2. pywin32 splits COM across pythoncom / pywintypes / win32com.client.
#      Collect all submodules so the COM dispatch table resolves at runtime.

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = []

for pkg in ("bless", "bleak", "winrt"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        # winrt may be split across multiple distributions on newer Python;
        # missing one is OK as long as bless/bleak collected its own copy.
        pass

hiddenimports += collect_submodules("win32com")
hiddenimports += ["pythoncom", "pywintypes", "win32com.client"]


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SlideRemoteHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # UPX compression triggers more AV false positives
    console=True,             # keep stdout visible so users can see logs / Ctrl+C
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
