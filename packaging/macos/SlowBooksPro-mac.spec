# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the native macOS build (.app bundle).
#
# STARTING POINT — adapted from packaging/windows/SlowBooksPro.spec and
# not yet validated on real Apple hardware. Expected iteration areas:
#   * WeasyPrint natives: on macOS these come from Homebrew (pango,
#     fontconfig, gobject-introspection). PyInstaller's hooks usually pick
#     the dylibs up automatically from the brew prefix; if PDF rendering
#     fails in the frozen app, stage them explicitly like the Windows
#     build stages its gtk-dlls.
#   * pywebview uses the Cocoa/WebKit backend on macOS (pyobjc) — no
#     pythonnet, no WebView2, one less runtime dependency than Windows.
#   * Icon: assets/icon-256.png → .icns (see the workflow step).

import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))


def _tree(src_rel, dest):
    """Recursively collect a repo directory as data files, skipping caches."""
    out = []
    src_root = os.path.join(ROOT, src_rel)
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fname in filenames:
            if fname.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(dirpath, fname)
            rel_dir = os.path.relpath(dirpath, src_root)
            target = dest if rel_dir == "." else os.path.join(dest, rel_dir)
            out.append((full, target))
    return out


datas = [
    (os.path.join(ROOT, "index.html"), "."),
    (os.path.join(ROOT, "alembic.ini"), "."),
    (os.path.join(ROOT, ".env.example"), "."),
]
datas += _tree("app/static", "app/static")
datas += _tree("app/templates", "app/templates")
# Alembic loads migration scripts as FILES at runtime (script_location) —
# they must exist on disk in the bundle, not just inside the PYZ.
datas += _tree("migrations", "migrations")

hiddenimports = (
    collect_submodules("app")
    + collect_submodules("uvicorn")
    + collect_submodules("alembic")
    + [
        # pywebview's macOS backend (Cocoa/WebKit via pyobjc)
        "webview.platforms.cocoa",
    ]
)

a = Analysis(
    [os.path.join(ROOT, "desktop_launcher.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=hiddenimports,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="SlowBooksPro",
    console=False,
    icon=os.path.join(SPECPATH, "slowbookspro.icns"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="SlowBooksPro",
)

app = BUNDLE(
    coll,
    name="SlowBooks Pro.app",
    icon=os.path.join(SPECPATH, "slowbookspro.icns"),
    bundle_identifier="com.vonholtencodes.slowbookspro",
    info_plist={
        "CFBundleShortVersionString": os.environ.get("APP_VERSION", "0.0.0"),
        "NSHighResolutionCapable": True,
        # The app runs a local web server for its own UI
        "NSLocalNetworkUsageDescription": (
            "SlowBooks Pro runs a local server for its user interface "
            "and, in Server Edition mode, for other computers you allow."
        ),
    },
)
