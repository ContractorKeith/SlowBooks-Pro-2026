#!/usr/bin/env python3
"""Audit a frozen macOS app's architecture and native-library closure."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

REQUIRED_LIBRARIES = {
    "libfontconfig.1.dylib",
    "libgobject-2.0.0.dylib",
    "libharfbuzz-subset.0.dylib",
    "libharfbuzz.0.dylib",
    "libpango-1.0.dylib",
    "libpangoft2-1.0.dylib",
}
ALLOWED_ABSOLUTE_PREFIXES = ("/System/Library/", "/usr/lib/")


def _run(*args: str) -> str:
    return subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _dependencies(path: Path) -> list[str]:
    lines = _run("otool", "-L", str(path)).splitlines()[1:]
    return [line.strip().split(" (", 1)[0] for line in lines if line.strip()]


def audit_bundle(app: Path, expected_arch: str) -> str:
    if not app.is_dir() or app.suffix != ".app":
        raise ValueError(f"not an app bundle: {app}")

    report = []
    failures = []
    bundled_names = set()
    macho_count = 0

    for path in sorted(app.rglob("*")):
        bundled_names.add(path.name)
        if path.is_symlink() or not path.is_file():
            continue
        description = _run("file", "-b", str(path))
        if "Mach-O" not in description:
            continue

        macho_count += 1
        architectures = _run("lipo", "-archs", str(path)).split()
        relative = path.relative_to(app)
        report.append(f"{relative}\tarchitectures={','.join(architectures)}")
        if expected_arch not in architectures:
            failures.append(f"{relative}: missing {expected_arch} slice")

        for dependency in _dependencies(path):
            report.append(f"  {dependency}")
            if dependency.startswith("/") and not dependency.startswith(
                ALLOWED_ABSOLUTE_PREFIXES
            ):
                failures.append(f"{relative}: external dependency {dependency}")

    if macho_count == 0:
        failures.append("bundle contains no Mach-O files")
    for library in sorted(REQUIRED_LIBRARIES - bundled_names):
        failures.append(f"required bundled library missing: {library}")

    report.append(f"mach_o_count={macho_count}")
    if failures:
        report.extend(f"ERROR: {failure}" for failure in failures)
        raise RuntimeError("\n".join(report))
    return "\n".join(report) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--expected-arch", default="arm64")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.write_text(
        audit_bundle(args.app, args.expected_arch),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
