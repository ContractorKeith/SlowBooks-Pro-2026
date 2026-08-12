# macOS build — maintainer notes

Maintainer: **@ContractorKeith** (signing + notarization with his Apple
Developer ID; credited in the main README). Owner approval is required
for every merge to `main` (CODEOWNERS + branch protection).

## What exists

- `.github/workflows/macos.yml` — builds an **unsigned** `.app` with
  PyInstaller on a hosted `macos-14` runner, runs the same `--smoke-test`
  gate as the Windows build (boot server, migrate a company, render a
  PDF), and packages a DMG artifact. Runnable today via
  *Actions → macOS → Run workflow*.
- `SlowBooksPro-mac.spec` — adapted from the Windows spec: Cocoa/WebKit
  pywebview backend (no pythonnet/WebView2 on macOS), same data tree
  (index.html, static, templates, migrations, alembic.ini).

## What needs a real Mac + Developer ID (the maintainer's part)

1. **Validate the spec.** First run will likely surface WeasyPrint dylib
   or pyobjc packaging issues — iterate on the spec, not the workflow.
2. **Pick a signing flow:**
   - *CI signing*: provide the secrets documented in the workflow's
     disabled block to the repo owner (only admins can add secrets),
     then enable those steps. Note this stores your signing identity as
     repo secrets — your call.
   - *Local signing*: download the unsigned artifact, then on your Mac:
     `codesign --deep --force --options runtime --sign "Developer ID
     Application: <name>" "SlowBooks Pro.app"`, zip it, `xcrun notarytool
     submit --wait`, `xcrun stapler staple`, rebuild the DMG. Attach the
     signed DMG to the GitHub release (owner can grant release upload or
     attach it for you).
3. **Known launcher notes for macOS:**
   - Data dir: `get_data_dir()` in `desktop_launcher.py` resolves the
     per-user data area — verify it lands in
     `~/Library/Application Support/SlowBooksPro` when frozen.
   - There is a crash report from an earlier native macOS run — get the
     current one into an issue so it's tracked.
4. **Server Edition:** `--serve-lan` works headless on macOS the same as
   Windows; nothing platform-specific expected.

## Ground rules (same as every contribution)

- PRs into `main`; owner review required before merge.
- CI (lint + pytest + CodeQL) must be green.
- The version source of truth is `app/__init__.py` — the workflow reads
  it; don't hardcode versions anywhere.
