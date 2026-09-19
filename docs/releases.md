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
   artifacts to a GitHub Release with generated release notes.

Archives are named
`spotm3u-<version>-<platform>-<arch>.zip`. The tag's `v` prefix is removed from
the filename and release name. A failed build or verification job prevents
publishing.

Version tags should follow semantic versioning: increment the patch for
backward-compatible fixes, the minor for backward-compatible features, and the
major for incompatible changes. This repository does not currently automate
version-file updates from tags, so keep `pyproject.toml` and tags aligned.

See [packaging.md](packaging.md) for local PyInstaller builds and FFmpeg
staging.
