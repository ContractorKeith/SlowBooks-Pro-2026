#!/usr/bin/env python3
"""Create a signed, notarized, and stapled DMG from one Actions artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from audit_bundle import audit_bundle

BUNDLE_ID = "com.vonholtencodes.slowbookspro"
IDENTITY_PATTERN = re.compile(
    r'^\s*\d+\)\s+[0-9A-Fa-f]+\s+"(Developer ID Application:[^"]+)"$',
    re.MULTILINE,
)


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(args), flush=True)
    return subprocess.run(args, check=check, capture_output=True, text=True)


def _parse_key_values(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            raise ValueError(f"invalid build metadata line: {line!r}")
        values[key] = value
    return values


def _verify_checksums(artifact_dir: Path) -> None:
    sums_path = artifact_dir / "SHA256SUMS"
    if not sums_path.is_file():
        raise ValueError(f"missing checksum manifest: {sums_path}")
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        expected, separator, raw_name = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"invalid checksum line: {line!r}")
        name = raw_name.removeprefix("*")
        candidate = (artifact_dir / name).resolve()
        try:
            candidate.relative_to(artifact_dir.resolve())
        except ValueError as exc:
            raise ValueError(
                f"checksum path escapes artifact directory: {name}"
            ) from exc
        if not candidate.is_file():
            raise ValueError(f"checksummed file is missing: {name}")
        with candidate.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"checksum mismatch for {name}")


def _developer_identities(output: str) -> list[str]:
    return IDENTITY_PATTERN.findall(output)


def _select_identity(requested: str | None) -> str:
    result = _run("security", "find-identity", "-v", "-p", "codesigning")
    identities = _developer_identities(result.stdout)
    if requested:
        if requested not in identities:
            raise ValueError(
                "requested Developer ID Application identity is unavailable"
            )
        return requested
    if len(identities) != 1:
        raise ValueError(
            "expected exactly one Developer ID Application identity; "
            "pass --identity to select one explicitly"
        )
    return identities[0]


def _clear_xattrs(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        try:
            names = os.listxattr(path, follow_symlinks=False)
        except OSError:
            continue
        for name in names:
            try:
                os.removexattr(path, name, follow_symlinks=False)
            except OSError:
                pass


def _is_macho(path: Path) -> tuple[bool, bool]:
    description = _run("file", "-b", str(path)).stdout
    return "Mach-O" in description, "executable" in description


def _sign(path: Path, identity: str, hardened_runtime: bool) -> None:
    command = ["codesign", "--force", "--timestamp"]
    if hardened_runtime:
        command.extend(["--options", "runtime"])
    command.extend(["--sign", identity, str(path)])
    _run(*command)


def _sign_app(app: Path, identity: str) -> None:
    macho_targets = []
    for path in app.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        is_macho, is_executable = _is_macho(path)
        if is_macho:
            macho_targets.append((path, is_executable))

    for path, is_executable in sorted(
        macho_targets,
        key=lambda item: len(item[0].parts),
        reverse=True,
    ):
        _sign(path, identity, hardened_runtime=is_executable)

    nested_suffixes = {".framework", ".bundle", ".plugin", ".xpc", ".appex", ".app"}
    nested = [
        path
        for path in app.rglob("*")
        if path.is_dir() and path.suffix in nested_suffixes
    ]
    for path in sorted(nested, key=lambda item: len(item.parts), reverse=True):
        _sign(path, identity, hardened_runtime=True)

    _sign(app, identity, hardened_runtime=True)


def _verify_signed_app(app: Path, report_dir: Path) -> None:
    _run("codesign", "--verify", "--deep", "--strict", "--verbose=4", str(app))
    details = _run("codesign", "-dvvv", str(app)).stderr
    if "Authority=Developer ID Application:" not in details:
        raise RuntimeError("app is not signed with a Developer ID Application identity")
    if "TeamIdentifier=" not in details or "Timestamp=" not in details:
        raise RuntimeError("app signature is missing Team ID or secure timestamp")
    if "runtime" not in details:
        raise RuntimeError("app signature does not enable hardened runtime")
    (report_dir / "codesign-app.txt").write_text(details, encoding="utf-8")
    _run("xcrun", "syspolicy_check", "notary-submission", str(app))


def _notarize(dmg: Path, profile: str, report_dir: Path) -> None:
    result = _run(
        "xcrun",
        "notarytool",
        "submit",
        str(dmg),
        "--keychain-profile",
        profile,
        "--wait",
        "--output-format",
        "json",
        check=False,
    )
    submit_path = report_dir / "notary-submit.json"
    submit_path.write_text(result.stdout, encoding="utf-8")
    (report_dir / "notary-submit.stderr.txt").write_text(
        result.stderr, encoding="utf-8"
    )
    try:
        payload = json.loads(result.stdout)
        submission_id = payload["id"]
        accepted = payload.get("status") == "Accepted"
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(
            f"notarytool returned an unreadable result; see {submit_path}"
        ) from exc

    log_path = report_dir / "notary-log.json"
    _run(
        "xcrun",
        "notarytool",
        "log",
        submission_id,
        str(log_path),
        "--keychain-profile",
        profile,
        check=False,
    )
    if result.returncode or not accepted:
        raise RuntimeError(f"notarization was not accepted; see {log_path}")

    _run("xcrun", "stapler", "staple", "-v", str(dmg))
    _run("xcrun", "stapler", "validate", "-v", str(dmg))


def build_release(
    artifact_dir: Path,
    expected_sha: str,
    output_root: Path,
    identity: str | None,
    notary_profile: str,
) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("macOS release tooling must run on macOS")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("--expected-sha must be a full lowercase commit SHA")

    artifact_dir = artifact_dir.resolve()
    _verify_checksums(artifact_dir)
    build_info = _parse_key_values(
        (artifact_dir / "build-info.txt").read_text(encoding="utf-8")
    )
    if build_info.get("git_sha") != expected_sha:
        raise ValueError("artifact commit does not match --expected-sha")
    if build_info.get("architecture") != "arm64":
        raise ValueError("artifact is not the expected arm64 build")
    version = build_info.get("app_version", "")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("artifact has an invalid application version")

    basename = f"SlowBooksPro-{version}-macos-arm64"
    app_zip = artifact_dir / f"{basename}-unsigned-app.zip"
    if not app_zip.is_file():
        raise ValueError(f"missing unsigned app archive: {app_zip.name}")

    selected_identity = _select_identity(identity)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_dir = output_root.resolve() / f"{basename}-{expected_sha[:12]}-{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=False)
    (report_dir / "source-build-info.txt").write_text(
        (artifact_dir / "build-info.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with tempfile.TemporaryDirectory(prefix="slowbooks-release-") as temporary:
        work_dir = Path(temporary)
        _run("ditto", "-x", "-k", str(app_zip), str(work_dir))
        app = work_dir / "SlowBooks Pro.app"
        if not app.is_dir():
            raise RuntimeError("transport archive did not contain SlowBooks Pro.app")

        _clear_xattrs(app)
        _sign_app(app, selected_identity)
        _verify_signed_app(app, report_dir)
        (report_dir / "native-linkage.txt").write_text(
            audit_bundle(app, "arm64"),
            encoding="utf-8",
        )

        stage = work_dir / "dmg-stage"
        stage.mkdir()
        staged_app = stage / app.name
        _run("ditto", str(app), str(staged_app))
        (stage / "Applications").symlink_to("/Applications")
        _clear_xattrs(staged_app)
        _run(
            "codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=4",
            str(staged_app),
        )

        candidate_dmg = report_dir / f"{basename}-candidate.dmg"
        _run(
            "hdiutil",
            "create",
            "-volname",
            "SlowBooks Pro",
            "-srcfolder",
            str(stage),
            "-ov",
            "-format",
            "UDZO",
            str(candidate_dmg),
        )
        _clear_xattrs(candidate_dmg)
        _run(
            "codesign",
            "--force",
            "--timestamp",
            "--sign",
            selected_identity,
            "--identifier",
            f"{BUNDLE_ID}.dmg",
            str(candidate_dmg),
        )
        _run("hdiutil", "verify", str(candidate_dmg))
        _run("codesign", "--verify", "--strict", "--verbose=4", str(candidate_dmg))
        _notarize(candidate_dmg, notary_profile, report_dir)

        final_dmg = report_dir / f"{basename}.dmg"
        candidate_dmg.rename(final_dmg)
        _run("hdiutil", "verify", str(final_dmg))
        _run("codesign", "--verify", "--strict", "--verbose=4", str(final_dmg))
        _run(
            "spctl",
            "--assess",
            "--type",
            "open",
            "--verbose=4",
            "--context",
            "context:primary-signature",
            str(final_dmg),
        )

    with final_dmg.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (report_dir / "SHA256SUMS").write_text(
        f"{digest}  {final_dmg.name}\n",
        encoding="utf-8",
    )
    print(f"Release candidate ready: {final_dmg}")
    return final_dmg


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--identity")
    parser.add_argument("--notary-profile", default="slowbooks-notary")
    args = parser.parse_args()
    build_release(
        args.artifact_dir,
        args.expected_sha,
        args.output_root,
        args.identity,
        args.notary_profile,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
