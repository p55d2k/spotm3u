# Releases

Releases are created by pushing a version tag. Ordinary pushes and pull
requests run CI but do not publish a release.

## Maintainer workflow

1. Update `version` in `pyproject.toml` and refresh `uv.lock` when dependency
   metadata changes.
2. Run the checks in [development.md](development.md).
3. Push a tag using the `vMAJOR.MINOR.PATCH` form, for example:

   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

4. `.github/workflows/release.yml` builds one archive for each supported
   platform, verifies each archive with the smoke test, and publishes the
   artifacts to a GitHub Release with generated release notes. On macOS the
   published artifact is a `.dmg` rather than the ZIP.

Archives are named `SpotM3U-<version>-<platform>-<arch>.zip`. The tag's `v`
prefix is removed from the filename and release name. The Windows and Linux
archives contain the one-folder bundle directory (`SpotM3U/`); the macOS
archive contains the application bundle itself (`SpotM3U.app/`) and is built
only so CI can verify the same layout users get from the
`SpotM3U-<version>-macos-<arch>.dmg` release asset. A failed build or
verification job prevents publishing.

Releases are intentionally unsigned and un-notarized: the workflow does not use
Apple Developer Program membership, Developer ID certificates, signing secrets,
or notarization. The macOS verification job validates the bundle structure and
runs the smoke test, but never fails merely because the app is unsigned. macOS
ships as a DMG because a browser-downloaded ZIP would mark the unsigned app as
quarantined, which makes it hang on modern macOS before its window can open;
see the README for the installation flow.

Version tags should follow semantic versioning: increment the patch for
backward-compatible fixes, the minor for backward-compatible features, and the
major for incompatible changes. This repository does not currently automate
version-file updates from tags, so keep `pyproject.toml` and tags aligned.

See [packaging.md](packaging.md) for local PyInstaller builds and FFmpeg
staging.
