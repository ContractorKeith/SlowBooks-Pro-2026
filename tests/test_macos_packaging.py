import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

MACOS_DIR = Path(__file__).resolve().parent.parent / "packaging" / "macos"


def _load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, MACOS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(MACOS_DIR))
audit = _load_module("slowbooks_macos_audit", "audit_bundle.py")
prepare = _load_module("slowbooks_macos_prepare", "prepare_bundle.py")
sys.path.pop(0)


def _fake_app(tmp_path):
    app = tmp_path / "SlowBooks Pro.app"
    main = app / "Contents" / "MacOS" / "SlowBooksPro"
    main.parent.mkdir(parents=True)
    main.write_bytes(b"main")
    frameworks = app / "Contents" / "Frameworks"
    frameworks.mkdir()
    (app / "Contents" / "Resources").mkdir()
    for name in audit.REQUIRED_LIBRARIES:
        (frameworks / name).write_bytes(b"library")
    return app, main


def test_audit_resolves_rpath_dependencies_inside_bundle(monkeypatch, tmp_path):
    app, main = _fake_app(tmp_path)
    dependency = "@rpath/libpango-1.0.dylib"

    def fake_run(*args):
        command, option, raw_path = args
        path = Path(raw_path)
        if command == "file":
            return "Mach-O 64-bit executable" if path == main else "data"
        if command == "lipo":
            return "arm64"
        if option == "-D":
            return f"{path}:"
        if option == "-L":
            return f"{path}:\n\t{dependency} (compatibility version 1.0.0)"
        if option == "-l":
            return "cmd LC_RPATH\n path @executable_path/../Frameworks (offset 12)"
        raise AssertionError(args)

    monkeypatch.setattr(audit, "_run", fake_run)

    report = audit.audit_bundle(app, "arm64")

    assert dependency in report
    assert "resolved=Contents/Frameworks/libpango-1.0.dylib" in report


def test_audit_rejects_unresolved_rpath_dependency(monkeypatch, tmp_path):
    app, main = _fake_app(tmp_path)

    def fake_run(*args):
        command, option, raw_path = args
        path = Path(raw_path)
        if command == "file":
            return "Mach-O 64-bit executable" if path == main else "data"
        if command == "lipo":
            return "arm64"
        if option == "-D":
            return f"{path}:"
        if option == "-L":
            return f"{path}:\n\t@rpath/libmissing.dylib (compatibility version 1.0.0)"
        if option == "-l":
            return "cmd LC_RPATH\n path @executable_path/../Frameworks (offset 12)"
        raise AssertionError(args)

    monkeypatch.setattr(audit, "_run", fake_run)

    with pytest.raises(RuntimeError, match="unresolved bundled dependency"):
        audit.audit_bundle(app, "arm64")


def test_prepare_bundle_removes_only_resources_links_to_frameworks(tmp_path):
    app, _ = _fake_app(tmp_path)
    resources = app / "Contents" / "Resources"
    frameworks = app / "Contents" / "Frameworks"
    binary_link = resources / "libpango-1.0.dylib"
    data_file = resources / "data.json"
    unrelated_link = resources / "data-link.json"
    binary_link.symlink_to("../Frameworks/libpango-1.0.dylib")
    data_file.write_text("{}", encoding="utf-8")
    unrelated_link.symlink_to("data.json")

    removed = prepare.prepare_bundle(app)

    assert removed == [binary_link]
    assert not binary_link.exists()
    assert unrelated_link.is_symlink()
    assert frameworks.is_dir()


def test_audit_rejects_resources_link_to_frameworks(monkeypatch, tmp_path):
    app, main = _fake_app(tmp_path)
    resources = app / "Contents" / "Resources"
    (resources / "libpango-1.0.dylib").symlink_to("../Frameworks/libpango-1.0.dylib")

    def fake_run(*args):
        command, option, raw_path = args
        path = Path(raw_path)
        if command == "file":
            return "Mach-O 64-bit executable" if path == main else "data"
        if command == "lipo":
            return "arm64"
        if option in {"-D", "-L"}:
            return f"{path}:"
        raise AssertionError(args)

    monkeypatch.setattr(audit, "_run", fake_run)

    with pytest.raises(RuntimeError, match="Resources link points into Frameworks"):
        audit.audit_bundle(app, "arm64")


def test_build_iconset_writes_every_apple_icon_size(tmp_path):
    if importlib.util.find_spec("PIL") is None:
        pytest.skip("macOS packaging dependencies are not installed")

    source = Path(__file__).resolve().parent.parent / "assets" / "icon-256.png"
    output = tmp_path / "SlowBooksPro.iconset"
    expected_sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }

    subprocess.run(
        [sys.executable, str(MACOS_DIR / "build_icon.py"), str(source), str(output)],
        check=True,
    )

    assert {path.name for path in output.iterdir()} == set(expected_sizes)
    verify_script = """
from pathlib import Path
import sys
from PIL import Image

output = Path(sys.argv[1])
expected_sizes = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}
for filename, size in expected_sizes.items():
    with Image.open(output / filename) as generated:
        assert generated.size == (size, size)
"""
    subprocess.run([sys.executable, "-c", verify_script, str(output)], check=True)
