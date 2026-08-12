import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

MACOS_DIR = Path(__file__).resolve().parent.parent / "packaging" / "macos"
sys.path.insert(0, str(MACOS_DIR))
SPEC = importlib.util.spec_from_file_location(
    "slowbooks_macos_release", MACOS_DIR / "release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
sys.path.pop(0)


def test_parse_build_info_rejects_duplicate_keys():
    with pytest.raises(ValueError, match="invalid build metadata"):
        release._parse_key_values("git_sha=one\ngit_sha=two\n")


def test_developer_identities_only_returns_application_certificates():
    output = """
      1) ABCDEF1234 "Developer ID Application: Example Person (TEAM123456)"
      2) 1234ABCDEF "Apple Development: Example Person (TEAM123456)"
         2 valid identities found
    """

    assert release._developer_identities(output) == [
        "Developer ID Application: Example Person (TEAM123456)"
    ]


def test_verify_checksums_rejects_tampered_artifact(tmp_path):
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(b"original")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (tmp_path / "SHA256SUMS").write_text(
        f"{digest}  {artifact.name}\n", encoding="utf-8"
    )
    release._verify_checksums(tmp_path)

    artifact.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        release._verify_checksums(tmp_path)


def test_sign_app_signs_nested_code_inside_out(monkeypatch, tmp_path):
    app = tmp_path / "SlowBooks Pro.app"
    executable = app / "Contents" / "MacOS" / "SlowBooksPro"
    dylib = app / "Contents" / "Frameworks" / "libexample.dylib"
    plugin = app / "Contents" / "PlugIns" / "Example.bundle"
    plugin_binary = plugin / "Contents" / "MacOS" / "Example"
    for path in (executable, dylib, plugin_binary):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mach-o")

    classifications = {
        executable: (True, True),
        dylib: (True, False),
        plugin_binary: (True, True),
    }
    signed = []
    monkeypatch.setattr(
        release,
        "_is_macho",
        lambda path: classifications.get(path, (False, False)),
    )
    monkeypatch.setattr(
        release,
        "_sign",
        lambda path, identity, hardened_runtime: signed.append(
            (path, identity, hardened_runtime)
        ),
    )

    release._sign_app(app, "Developer ID Application: Example (TEAM123456)")

    signed_paths = [path for path, _, _ in signed]
    assert signed_paths.index(plugin_binary) < signed_paths.index(plugin)
    assert signed_paths.index(plugin) < signed_paths.index(app)
    assert signed_paths[-1] == app
    assert (dylib, "Developer ID Application: Example (TEAM123456)", False) in signed
    assert (
        executable,
        "Developer ID Application: Example (TEAM123456)",
        True,
    ) in signed


def test_signature_details_require_matching_team_timestamp_and_runtime():
    identity = "Developer ID Application: Example (TEAM123456)"
    details = (
        f"Authority={identity}\n"
        "TeamIdentifier=TEAM123456\n"
        "Timestamp=Aug 12, 2026 at 12:00:00\n"
        "flags=0x10000(runtime)\n"
    )

    assert (
        release._verify_signature_details(
            details,
            identity,
            expected_team_id="TEAM123456",
            hardened_runtime=True,
        )
        == "TEAM123456"
    )

    with pytest.raises(RuntimeError, match="hardened runtime"):
        release._verify_signature_details(
            details.replace("flags=0x10000(runtime)\n", ""),
            identity,
            expected_team_id="TEAM123456",
            hardened_runtime=True,
        )


def test_pre_notary_policy_allows_only_lone_unnotarized_rejection():
    syspolicy = subprocess.CompletedProcess(
        ("syspolicy_check",),
        70,
        stdout=("Codesign Error\nSeverity: Fatal\n" "Gatekeeper rejected this file.\n"),
        stderr="",
    )
    gatekeeper = subprocess.CompletedProcess(
        ("spctl",),
        3,
        stdout="",
        stderr="rejected\nsource=Unnotarized Developer ID\n",
    )

    assert release._expected_unnotarized_policy_result(syspolicy, gatekeeper)

    with_structure_error = subprocess.CompletedProcess(
        syspolicy.args,
        syspolicy.returncode,
        stdout=syspolicy.stdout + "Incorrect Bundle Structure\nSeverity: Warning\n",
        stderr="",
    )
    assert not release._expected_unnotarized_policy_result(
        with_structure_error, gatekeeper
    )


def test_notarize_inspects_accepted_log_and_staples(monkeypatch, tmp_path):
    dmg = tmp_path / "SlowBooksPro.dmg"
    dmg.write_bytes(b"dmg")
    submission_id = "11111111-2222-3333-4444-555555555555"
    calls = []

    def fake_run(*args, check=True):
        calls.append(args)
        if args[1:3] == ("notarytool", "submit"):
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps({"id": submission_id, "status": "Accepted"}),
                stderr="",
            )
        if args[1:3] == ("notarytool", "log"):
            Path(args[4]).write_text(
                json.dumps(
                    {"jobId": submission_id, "status": "Accepted", "issues": []}
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(release, "_run", fake_run)

    release._notarize(dmg, "slowbooks-notary", tmp_path)

    assert (tmp_path / "notary-log.json").is_file()
    assert any(args[1:3] == ("stapler", "staple") for args in calls)
    assert any(args[1:3] == ("stapler", "validate") for args in calls)


def test_notarize_rejects_log_with_errors(monkeypatch, tmp_path):
    dmg = tmp_path / "SlowBooksPro.dmg"
    dmg.write_bytes(b"dmg")
    submission_id = "11111111-2222-3333-4444-555555555555"

    def fake_run(*args, check=True):
        if args[1:3] == ("notarytool", "submit"):
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps({"id": submission_id, "status": "Accepted"}),
                stderr="",
            )
        if args[1:3] == ("notarytool", "log"):
            Path(args[4]).write_text(
                json.dumps(
                    {
                        "jobId": submission_id,
                        "status": "Accepted",
                        "issues": [{"severity": "error", "message": "bad code"}],
                    }
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(release, "_run", fake_run)

    with pytest.raises(RuntimeError, match="did not pass inspection"):
        release._notarize(dmg, "slowbooks-notary", tmp_path)
