"""Build SlideRemoteHelper.exe via PyInstaller.

Usage:
    python build.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = HERE / "slide_remote.spec"


def _clean() -> None:
    for d in ("build", "dist", "__pycache__"):
        p = HERE / d
        if p.exists():
            print(f"  cleaning {p}")
            shutil.rmtree(p, ignore_errors=True)


def main() -> int:
    if not SPEC.exists():
        print(f"error: spec file not found: {SPEC}", file=sys.stderr)
        return 1

    _clean()

    print(f"\n>>> pyinstaller --clean {SPEC.name}\n")
    rc = subprocess.call(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(SPEC)],
        cwd=str(HERE),
    )
    if rc != 0:
        print(f"\nPyInstaller failed (exit {rc})", file=sys.stderr)
        return rc

    exe_name = "SlideRemoteHelper.exe" if sys.platform == "win32" else "SlideRemoteHelper"
    exe = HERE / "dist" / exe_name
    if exe.exists():
        size_mb = exe.stat().st_size / (1024 * 1024)
        print(f"\nBuilt: {exe}")
        print(f"Size:  {size_mb:.1f} MB")
        print("\nRun it with:  .\\dist\\SlideRemoteHelper.exe")
    else:
        print("warning: build finished but exe not found at expected path", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
