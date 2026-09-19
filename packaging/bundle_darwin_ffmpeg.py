"""Make a staged macOS FFmpeg self-contained by bundling its library closure.

Homebrew FFmpeg links against many libraries under ``/opt/homebrew`` (or
``/usr/local``). Copying only the two executables yields a bundle that dies on a
machine without Homebrew. This copies the transitive Homebrew dylib closure next
to the executables and rewrites every load command to ``@loader_path/<name>`` so
the directory can be shipped anywhere. System libraries (``/usr/lib``,
``/System``) stay where they are.

Only used on macOS; ``otool`` and ``install_name_tool`` come from the Xcode
command line tools that ship on GitHub macOS runners.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

EXECUTABLE_NAMES = ("ffmpeg", "ffprobe")
HOMEBREW_PREFIXES = ("/opt/homebrew", "/usr/local")


class DarwinBundleError(RuntimeError):
    """Raised when a macOS FFmpeg bundle could not be made self-contained."""


def _tool(name: str) -> str:
    toolchain = (
        Path("/Applications/Xcode.app")
        / "Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin"
        / name
    )
    if toolchain.is_file():
        return str(toolchain)
    existing = shutil.which(name)
    if existing:
        return existing
    raise DarwinBundleError(f"{name} not found; Xcode command line tools required")


def _codesign_available() -> bool:
    return shutil.which("codesign") is not None


def _dylib_deps(binary: Path) -> list[str]:
    out = subprocess.check_output([_tool("otool"), "-L", str(binary)], text=True)
    deps: list[str] = []
    for line in out.splitlines()[1:]:
        dep = line.strip().split(" (compatibility", 1)[0].strip()
        if dep:
            deps.append(dep)
    return deps


def _homebrew_dep(target_name: str) -> bool:
    return target_name.startswith(HOMEBREW_PREFIXES)


def _remove_code_signature(path: Path) -> None:
    if not _codesign_available():
        return
    subprocess.run(
        ["codesign", "--remove-signature", str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _ad_hoc_sign(path: Path) -> None:
    if not _codesign_available():
        return
    subprocess.run(
        ["codesign", "--force", "--sign", "-", str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def bundle_darwin_dylibs(out_dir: str | Path) -> list[Path]:
    """Bundle the Homebrew dylib closure into ``out_dir``.

    ``out_dir`` must already contain ``ffmpeg`` and ``ffprobe``. Returns every
    file whose load commands were rewritten (binaries plus copied dylibs).
    """
    out = Path(out_dir)
    binaries = [out / name for name in EXECUTABLE_NAMES]
    for binary in binaries:
        if not binary.is_file():
            raise DarwinBundleError(f"{binary} not found in staging directory")

    renamed: dict[str, str] = {}
    staged_by_name: dict[str, Path] = {}
    queue = [*binaries]
    while queue:
        current = queue.pop()
        for dep in _dylib_deps(current):
            if dep in renamed or not _homebrew_dep(dep):
                continue
            target = out / Path(dep).name
            if target.name not in staged_by_name:
                shutil.copyfile(dep, target)
                _remove_code_signature(target)
                staged_by_name[target.name] = target
                queue.append(target)
            renamed[dep] = f"@loader_path/{target.name}"

    touched = [*binaries, *(staged_by_name.values())]
    for path in touched:
        _remove_code_signature(path)
        for old, new in renamed.items():
            if old in _dylib_deps(path):
                subprocess.run(
                    [_tool("install_name_tool"), "-change", old, new, str(path)],
                    check=True,
                )
    for path in touched:
        _ad_hoc_sign(path)
    return touched


def bundle_darwin_ffmpeg(out_dir: str | Path, which=shutil.which) -> None:
    """Stage Homebrew FFmpeg into ``out_dir`` as a portable set of binaries."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name in EXECUTABLE_NAMES:
        source = which(name)
        if not source:
            raise DarwinBundleError(f"{name}: not found on PATH; brew install ffmpeg first")
        target = out / Path(source).name
        shutil.copyfile(source, target)
        target.chmod(0o755)
    bundle_darwin_dylibs(out_dir)


if __name__ == "__main__":
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "ffmpeg-stage")
    bundle_darwin_ffmpeg(out_dir)
    print(f"bundled self-contained FFmpeg into {out_dir.resolve()}")
