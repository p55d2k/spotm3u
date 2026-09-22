"""Refuse a release tag that is not ``vMAJOR.MINOR.PATCH``.

The release workflow runs this in its first job, before a single platform build
starts. Every artifact embeds the tag as ``spotm3u.__version__`` and the in-app
update check compares it as a semantic version, so a tag this rejects (``v2.0``,
``v2.0-rc``, ``v2.0.0-rc.1``) must never reach a bundle or a GitHub Release:
failing here is what stops a malformed tag from publishing a partial or
un-versioned release.

Pre-release tags are refused on purpose. Nothing in the release workflow marks a
release as a prerelease, and ``releases/latest`` -- the endpoint every installed
app queries -- only skips prereleases, so an RC published as an ordinary release
would be offered to every running copy.

Usage:

    python3 packaging/check_release_tag.py v1.2.3
"""

from __future__ import annotations

import os
import re
import sys

# The tag form documented in docs/releases.md: a literal ``v`` followed by
# three numeric components and nothing else.
TAG_PATTERN = re.compile(r"^v\d+\.\d+\.\d+$")


def validate_tag(tag: str) -> str:
    """Return ``tag`` when it is releasable, or raise ``SystemExit`` explaining why not.

    Raises rather than returning a flag so an invalid tag fails the workflow
    step without the caller having to check a return value.
    """
    if TAG_PATTERN.match(tag):
        return tag
    message = f"release tags must be vMAJOR.MINOR.PATCH (got {tag!r})"
    if os.environ.get("GITHUB_ACTIONS"):
        # Surface the failure as an Actions annotation as well as a failed step.
        print(f"::error::{message}")
    raise SystemExit(message)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <tag>")
    print(f"release tag {validate_tag(sys.argv[1])} is valid")
