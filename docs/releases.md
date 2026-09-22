# Releases

Releases are created by pushing a version tag. Ordinary pushes and pull
requests run CI but do not publish a release.

## Maintainer workflow

1. Bump `version` in `pyproject.toml` and `__version__` in
   `src/spotm3u/__init__.py`, and refresh `uv.lock` when dependency metadata
   changes. The release workflow stamps `__version__` from the tag for the
   packaged build, so this keeps development runs consistent with releases.
2. Run the checks in [development.md](development.md).
3. Push a tag using the `vMAJOR.MINOR.PATCH` form, for example:

   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

   The `v` prefix is required and the format is enforced: the first job in
   `release.yml` refuses anything else -- a missing component (`v2.0`), a bare
   `2.0.0`, or a pre-release (`v2.0.0-rc.1`) -- before a single platform build
   starts, so a malformed tag cannot publish a partial or un-versioned release.
   Pre-release tags are not supported at all: nothing marks such a release as a
   prerelease, and `releases/latest` would hand it to every installed app.

4. `.github/workflows/release.yml` builds one archive for each supported
   platform, verifies each archive with the smoke test, and publishes the
   artifacts to a GitHub Release with generated release notes. On macOS the
   published artifact is a `.pkg` rather than the ZIP.

Archives are named `SpotM3U-<version>-<platform>-<arch>.zip`. The tag's `v`
prefix is removed from the filename and release name. The Windows and Linux
archives contain the one-folder bundle directory (`SpotM3U/`); the macOS
archive contains the application bundle itself (`SpotM3U.app/`) and is built
only so CI can verify the same layout users get from the
`SpotM3U-<version>-macos-<arch>.pkg` release asset. A failed build
or verification job prevents publishing.

Releases are intentionally unsigned and un-notarized: the workflow does not use
Apple Developer Program membership, Developer ID certificates, signing secrets,
or notarization. The macOS verification job validates the bundle structure and
runs the smoke test, but never fails merely because the app is unsigned. macOS
ships as an installer package because a browser-downloaded ZIP would mark the
unsigned app as quarantined, which makes it hang on modern macOS before its
window can open, whereas the installer writes the app files fresh and
unquarantined; see the README for the installation flow.

Version tags should follow semantic versioning: increment the patch for
backward-compatible fixes, the minor for backward-compatible features, and the
major for incompatible changes.

Once the tag is accepted, the workflow runs `packaging/stamp_version.py <tag>`
to write the tag into `src/spotm3u/__init__.py`'s `__version__`, so every
packaged app reports the release it was actually built from. This matters because the in-app
update check compares that constant against the published release tag: a bundle
carrying a stale version would offer an update it already has, forever. The
verification job asserts the packaged app reports the tag (`smoke_test.py
--expect-version`). Development runs use the committed constant instead, which
is why the files are bumped by hand above.

Every running app checks the published release (see
[web.md](web.md#in-app-update-notice)) and offers to download the new
platform installer. Keep the `SpotM3U-<version>-<platform>-<arch>.*` asset
naming stable - the in-app download relies on it. Draft and pre-releases are
never surfaced (`releases/latest` only returns a non-draft, non-prerelease
release).

See [packaging.md](packaging.md) for local PyInstaller builds and FFmpeg
staging.
