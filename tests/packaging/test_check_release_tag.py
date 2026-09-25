"""Tests for the release-tag guard that gates the release workflow."""

import importlib.util

import pytest
from conftest import REPO_ROOT


def _load(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "packaging" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_release_tag = _load("check_release_tag")


@pytest.mark.parametrize("tag", ["v0.0.0", "v1.0.0", "v10.20.30", "v2.0.10"])
def test_release_tags_are_accepted(tag: str) -> None:
    assert check_release_tag.validate_tag(tag) == tag


@pytest.mark.parametrize(
    "tag",
    [
        "1.2.3",  # the v prefix is part of the documented form
        "v1.2",  # missing the patch component
        "v1",  # missing minor and patch
        "v2.0-rc",  # the case the guard exists for
        "v2.0.0-rc.1",  # pre-releases are refused, never published as normal releases
        "v1.2.3.4",  # too many components
        "v1.2.3+build",  # build metadata is outside the tag form
        "v1.2.3.0-rc1",
        "va.b.c",
        "latest",
        "v1.2.3 ",  # trailing whitespace
        "",
    ],
)
def test_malformed_tags_are_refused(tag: str) -> None:
    with pytest.raises(SystemExit, match="must be vMAJOR.MINOR.PATCH"):
        check_release_tag.validate_tag(tag)


def test_refusal_names_the_offending_tag() -> None:
    with pytest.raises(SystemExit, match="got 'v2.0-rc'"):
        check_release_tag.validate_tag("v2.0-rc")


def test_release_workflow_gates_every_build_on_the_guard() -> None:
    # The guard only prevents a partial release while it is wired in front of
    # the build matrix; removing either half must not pass unnoticed.
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "check_release_tag.py" in workflow
    assert "needs: validate-tag" in workflow
