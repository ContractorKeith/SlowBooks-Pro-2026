import hashlib
import importlib.util
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
