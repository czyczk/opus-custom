#!/usr/bin/env python3
"""Build opusenc-senav for a requested OS/arch target.

This is the engine behind the repo `justfile`.  It tries hard to adapt to the
host it is running on:

  * Linux      -> native Linux build for the host arch; Windows/macOS targets
                  only when the corresponding cross toolchain is available.
  * WSL         -> same as Linux, plus Windows-native Visual Studio builds
                  (VS 2022 / 2026, auto-detected with vswhere).
  * Windows     -> Windows-native Visual Studio builds (VS 2022 / 2026).
  * macOS       -> macOS-native Apple clang builds.

Every unavailable combination fails before compiling, with a message that
names the missing tool and how to provide it.

Targets:
  windows-x86, windows-x86-64, windows-arm64
  linux-x86,   linux-x86-64,   linux-arm64
  macos-x86-64, macos-arm64,   macos-universal
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD_ROOT = ROOT / "build"
CACHE_ROOT = BUILD_ROOT / "_cache"
SOURCE_ROOT = BUILD_ROOT / "_src"
WORK_ROOT = BUILD_ROOT / "_work"
DEFAULT_OUT_ROOT = BUILD_ROOT / "out"

OPUS_TOOLS_DEFAULT = ROOT.parent / "opus-tools-custom"

LIBOGG_URL = "https://downloads.xiph.org/releases/ogg/libogg-1.3.5.tar.xz"
LIBOGG_SHA = "c4d91be36fc8e54deae7575241e03f4211eb102afb3fc0775fbbc1b740016705"
LIBOPUSENC_URL = "https://downloads.xiph.org/releases/opus/libopusenc-0.2.1.tar.gz"
LIBOPUSENC_SHA = "8298db61a8d3d63e41c1a80705baa8ce9ff3f50452ea7ec1c19a564fe106cbb9"
FLAC_URL = "https://downloads.xiph.org/releases/flac/flac-1.4.3.tar.xz"
FLAC_SHA = "6c58e69cd22348f441b861092b825e591d0b822e106de6eb0ee4d05d27205b70"
OPUS_PACKAGE_VERSION = os.environ.get("OPUS_PACKAGE_VERSION", "1.6.1")


class ToolError(RuntimeError):
    pass


class Target:
    def __init__(self, key: str, os_name: str, arch: str):
        self.key = key
        self.os = os_name
        self.arch = arch

    @property
    def exe(self) -> bool:
        return self.os == "windows"

    @property
    def name(self) -> str:
        return self.key

    @property
    def exe_name(self) -> str:
        return "opusenc-senav.exe" if self.exe else "opusenc-senav"

    def __repr__(self):
        return self.name


TARGETS = {
    "windows-x86": Target("windows-x86", "windows", "x86"),
    "windows-x86-64": Target("windows-x86-64", "windows", "x86_64"),
    "windows-arm64": Target("windows-arm64", "windows", "arm64"),
    "linux-x86": Target("linux-x86", "linux", "x86"),
    "linux-x86-64": Target("linux-x86-64", "linux", "x86_64"),
    "linux-arm64": Target("linux-arm64", "linux", "arm64"),
    "macos-x86-64": Target("macos-x86-64", "darwin", "x86_64"),
    "macos-arm64": Target("macos-arm64", "darwin", "arm64"),
    "macos-universal": Target("macos-universal", "darwin", "universal"),
}
TARGET_ALIASES = {
    "windows-x64": "windows-x86-64",
    "linux-x64": "linux-x86-64",
    "macos-x64": "macos-x86-64",
    "macos-universal2": "macos-universal",
}


def host_os() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def host_arch() -> str:
    m = platform.machine().lower()
    return {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64",
            "arm64": "arm64", "i386": "x86", "i486": "x86", "i586": "x86",
            "i686": "x86", "x86": "x86"}.get(m, m)


def is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        text = pathlib.Path("/proc/sys/kernel/osrelease").read_text()
        return "microsoft" in text.lower()
    except OSError:
        return False


def run(cmd, cwd=None, env=None, capture=False):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    kw = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL)
    if is_wsl() and any(str(c).lower().endswith(".exe") for c in cmd):
        # Detach Windows interop processes from this terminal; otherwise their
        # conhost output can escape the redirected pipe and flood the caller.
        kw["start_new_session"] = True
    if capture:
        return subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, **kw)
    return subprocess.run(cmd, check=True, **kw)


def which(name: str) -> pathlib.Path | None:
    p = shutil.which(name)
    return pathlib.Path(p) if p else None


def find_in_path(names):
    for name in names:
        p = which(name)
        if p:
            return p
    return None


def parse_target(name: str) -> Target:
    key = TARGET_ALIASES.get(name, name)
    if key not in TARGETS:
        raise ToolError(f"unknown target '{name}'; choices: "
                        + ", ".join(sorted(TARGETS)) + " (aliases: "
                        + ", ".join(sorted(TARGET_ALIASES)) + ")")
    return TARGETS[key]


# ------------------------------------------------------------- wsl helpers
def wslpath_to_linux(win_path: str) -> pathlib.Path | None:
    w = which("wslpath")
    if not w:
        return None
    r = subprocess.run([str(w), "-u", win_path], text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if r.returncode == 0 and r.stdout.strip():
        return pathlib.Path(r.stdout.strip())
    return None


def wslpath_to_windows(path: pathlib.Path) -> str:
    w = which("wslpath")
    if w and is_wsl():
        r = subprocess.run([str(w), "-w", str(path)], text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return str(path)


def windows_temp_dir() -> pathlib.Path:
    if host_os() == "windows":
        return pathlib.Path(tempfile.gettempdir())
    if is_wsl():
        ps = find_in_path(["powershell.exe"])
        if ps:
            r = subprocess.run([str(ps), "-NoProfile", "-Command", "$env:TEMP"],
                               text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
            if r.returncode == 0 and r.stdout.strip():
                lp = wslpath_to_linux(r.stdout.strip())
                if lp:
                    return lp
    return pathlib.Path(tempfile.gettempdir())


# ------------------------------------------------------- toolchain lookup
def find_cmake() -> pathlib.Path:
    p = which("cmake")
    if p:
        return p
    raise ToolError("cmake not found; install cmake (or on Windows: "
                    "https://cmake.org/download/) first")


def find_sena_root() -> pathlib.Path | None:
    cand = pathlib.Path.home() / "src" / "Rust_Projects" / "sena"
    return cand if cand.exists() else None


def find_llvm_lld_link() -> pathlib.Path | None:
    if env := os.environ.get("LLD_LINK"):
        p = pathlib.Path(env)
        if p.exists():
            return p
    cands = []
    sena = find_sena_root()
    if sena:
        cands.append(sena / ".cache" / "tools" / "lld14" / "usr" / "lib" / "llvm-14" / "bin" / "lld-link")
    cands += [pathlib.Path(p) for p in [
        "/usr/lib/llvm-14/bin/lld-link", "/usr/lib/llvm-13/bin/lld-link"]]
    for c in cands:
        if c.exists():
            return c
    p = which("lld-link")
    return pathlib.Path(p) if p else None


def find_xwin_root() -> pathlib.Path | None:
    for env in ("XWIN_CACHE_DIR", "WINDOWS_XWIN_ROOT"):
        p = pathlib.Path(os.environ[env]) if os.environ.get(env) else None
        if p and (p / "crt" / "include").exists():
            return p
        if p and (p / "xwin" / "crt" / "include").exists():
            return p / "xwin"
    cands = []
    sena = find_sena_root()
    if sena:
        cands.append(sena / ".cache" / "cargo-xwin" / "xwin")
    cands += [pathlib.Path.home() / ".cache" / "cargo-xwin" / "xwin"]
    for c in cands:
        if (c / "crt" / "include").exists() and (c / "sdk" / "include").exists():
            return c
    return None


def find_macos_sdk() -> pathlib.Path | None:
    if env := os.environ.get("MACOSX_SDK"):
        p = pathlib.Path(env)
        if p.exists():
            return p
    cands = []
    sena = find_sena_root()
    if sena:
        cands.append(sena / ".cache" / "MacOSX11.3.sdk")
    cands += sorted(pathlib.Path.home().glob("MacOSX*.sdk"), reverse=True)
    for c in cands:
        if (c / "System" / "Library" / "Frameworks").exists() or (c / "usr" / "include").exists():
            return c
    return None


def find_ld64_lld() -> pathlib.Path | None:
    if env := os.environ.get("LD64_LLD"):
        p = pathlib.Path(env)
        if p.exists():
            return p
    cands = []
    sena = find_sena_root()
    if sena:
        cands.append(sena / ".cache" / "tools" / "lld14" / "usr" / "lib" / "llvm-14" / "bin" / "ld64.lld")
    cands += [pathlib.Path("/usr/lib/llvm-14/bin/ld64.lld"),
              pathlib.Path("/usr/lib/llvm-13/bin/ld64.lld")]
    for c in cands:
        if c.exists():
            return c
    p = which("ld64.lld")
    return pathlib.Path(p) if p else None


def find_lipo() -> pathlib.Path | None:
    p = find_in_path(["llvm-lipo", "lipo", "llvm-lipo-14"])
    if p:
        return p
    sena = find_sena_root()
    cands = [pathlib.Path("/usr/lib/llvm-14/bin/llvm-lipo"),
             pathlib.Path("/usr/bin/llvm-lipo-14")]
    if sena:
        cands.append(sena / ".cache" / "tools" / "lld14" / "usr" / "lib" / "llvm-14" / "bin" / "llvm-lipo")
    for c in cands:
        if c.exists():
            return c
    return None


# --------------------------------------------------------- dependency sources
def sha256_of(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: pathlib.Path, sha256: str) -> pathlib.Path:
    if dest.exists() and dest.stat().st_size > 0:
        if sha256_of(dest) == sha256:
            return dest
        print(f"cached {dest.name} has wrong sha256; re-downloading")
        dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"download {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "opusenc-senav-builder/1"})
    with urllib.request.urlopen(req, timeout=180) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out)
    actual = sha256_of(dest)
    if actual != sha256:
        dest.unlink()
        raise ToolError(f"sha256 mismatch for {dest.name}: {actual} != {sha256}")
    return dest


def extract_tar(archive: pathlib.Path, dest_dir: pathlib.Path) -> pathlib.Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        names = tf.getnames()
        top = pathlib.PurePosixPath(names[0]).parts[0]
        try:
            tf.extractall(dest_dir, filter="data")
        except TypeError:
            tf.extractall(dest_dir)
    return dest_dir / top


def dep_source_dir(kind: str) -> pathlib.Path:
    if kind == "libogg":
        url, sha, arc = LIBOGG_URL, LIBOGG_SHA, "libogg-1.3.5.tar.xz"
        top = "libogg-1.3.5"
    elif kind == "libopusenc":
        url, sha, arc = LIBOPUSENC_URL, LIBOPUSENC_SHA, "libopusenc-0.2.1.tar.gz"
        top = "libopusenc-0.2.1"
    elif kind == "flac":
        url, sha, arc = FLAC_URL, FLAC_SHA, "flac-1.4.3.tar.xz"
        top = "flac-1.4.3"
    else:
        raise AssertionError(kind)
    archive = download(url, CACHE_ROOT / arc, sha)
    out = extract_tar(archive, SOURCE_ROOT)
    assert out.name == top, (out, top)
    return out


def patch_libopusenc_headers(src: pathlib.Path) -> None:
    hdr = src / "include" / "opusenc.h"
    text = hdr.read_text()
    new = text.replace("__opus_check_int_ptr", "opus_check_int_ptr").replace(
        "__opus_check_int", "opus_check_int")
    if new != text:
        hdr.write_text(new)


def prepare_dependencies() -> dict[str, pathlib.Path]:
    srcs = {}
    for kind in ("libogg", "libopusenc", "flac"):
        p = dep_source_dir(kind)
        if kind == "libopusenc":
            patch_libopusenc_headers(p)
        srcs[kind] = p
    return srcs


def opus_commit_id() -> str:
    """Short git hash of this opus-custom checkout, baked into the SenaV
    version string so a binary always reports its exact libopus provenance.
    'unknown' when built from an exported tree (no .git)."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def stage_opus_source() -> pathlib.Path:
    """Copy the current working tree (including uncommitted edits) once and
    pin the package version so the binary reports `libopus 1.6.1`."""
    dst = SOURCE_ROOT / "opus"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(
        ".git", "build", "out", ".vs", ".vscode", "CMakeSettings.json",
        "tools/__pycache__"))
    (dst / "package_version").write_text(
        f'AUTO_UPDATE=no\nPACKAGE_VERSION="{OPUS_PACKAGE_VERSION}"\n')
    return dst


# ------------------------------------------------------------- cmake helpers
class BuildSpec:
    def __init__(self, target: Target, prefix: pathlib.Path, work: pathlib.Path):
        self.target = target
        self.prefix = prefix
        self.work = work
        self.cmake = find_cmake()
        self.generator: list[str] = []
        self.toolchain_file: pathlib.Path | None = None
        self.env = os.environ.copy()
        self.release_flags: list[str] = []
        self.config = None
        self.native_host = False
        self.description = ""
        self.windows_paths = False
        self.extra_configure: list[str] = []


def cmake_configure(spec: BuildSpec, src: pathlib.Path, build: pathlib.Path,
                    extra: list[str] | None = None) -> None:
    src_arg, build_arg = str(src), str(build)
    prefix_arg = str(spec.prefix)
    toolchain_arg = str(spec.toolchain_file) if spec.toolchain_file else ""
    if spec.windows_paths:
        src_arg = wslpath_to_windows(pathlib.Path(src_arg))
        build_arg = wslpath_to_windows(pathlib.Path(build_arg))
        prefix_arg = wslpath_to_windows(pathlib.Path(prefix_arg))
        if toolchain_arg:
            toolchain_arg = wslpath_to_windows(pathlib.Path(toolchain_arg))
    cmd = [str(spec.cmake), "-S", src_arg, "-B", build_arg]
    if spec.generator:
        cmd += spec.generator
    if spec.toolchain_file:
        cmd += [f"-DCMAKE_TOOLCHAIN_FILE={toolchain_arg}"]
    cmd += [f"-DCMAKE_INSTALL_PREFIX={prefix_arg}",
            "-DCMAKE_BUILD_TYPE=Release",
            "-DBUILD_SHARED_LIBS=OFF"]
    cmd += spec.extra_configure
    cmd += (extra or [])
    run(cmd, env=spec.env)


def cmake_build(spec: BuildSpec, build: pathlib.Path, target_name: str | None = None) -> None:
    build_arg = str(build)
    if spec.windows_paths:
        build_arg = wslpath_to_windows(pathlib.Path(build_arg))
    cmd = [str(spec.cmake), "--build", build_arg, "-j", str(os.cpu_count() or 4)]
    if spec.config:
        cmd += ["--config", spec.config]
    if target_name:
        cmd += ["--target", target_name]
    run(cmd, env=spec.env)


def cmake_install(spec: BuildSpec, build: pathlib.Path) -> None:
    build_arg = str(build)
    if spec.windows_paths:
        build_arg = wslpath_to_windows(pathlib.Path(build_arg))
    cmd = [str(spec.cmake), "--install", build_arg]
    if spec.config:
        cmd += ["--config", spec.config]
    run(cmd, env=spec.env)


def build_libogg(spec: BuildSpec, src: pathlib.Path, build: pathlib.Path) -> None:
    extra = ["-DBUILD_TESTING=OFF", "-DINSTALL_CMAKE_PACKAGE_MODULE=OFF",
             "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"]
    if spec.release_flags:
        extra.append(f"-DCMAKE_C_FLAGS_RELEASE={' '.join(spec.release_flags)}")
    cmake_configure(spec, src, build, extra)
    cmake_build(spec, build)
    cmake_install(spec, build)


def build_flac(spec: BuildSpec, src: pathlib.Path, build: pathlib.Path) -> None:
    extra = ["-DBUILD_PROGRAMS=OFF", "-DBUILD_EXAMPLES=OFF",
             "-DBUILD_TESTING=OFF", "-DBUILD_DOCS=OFF",
             "-DBUILD_CXXLIBS=OFF", "-DINSTALL_MANPAGES=OFF",
             "-DWITH_OGG=OFF"]
    if spec.release_flags:
        extra.append(f"-DCMAKE_C_FLAGS_RELEASE={' '.join(spec.release_flags)}")
    cmake_configure(spec, src, build, extra)
    # FLAC 1.4.3 unconditionally adds microbench executables; they pull in -lrt
    # which does not exist in the macOS SDK, so build only the static library.
    cmake_build(spec, build, "FLAC")
    cmake_install(spec, build)


def build_libopus(spec: BuildSpec, src: pathlib.Path, build: pathlib.Path) -> None:
    extra = ["-DOPUS_BUILD_PROGRAMS=OFF", "-DOPUS_BUILD_TESTING=OFF"]
    if spec.target.os == "windows":
        extra += ["-DOPUS_STATIC_RUNTIME=ON"]
        if spec.target.arch == "arm64" and "clang-cl" in spec.description:
            # clang-cl has no MSVC __emit intrinsic for ARM64; all ARM64 CPUs
            # running Windows have NEON, so presume it and skip runtime probing.
            extra += ["-DOPUS_MAY_HAVE_NEON=OFF", "-DOPUS_PRESUME_NEON=ON"]
        if spec.target.arch == "x86" and "clang-cl" in spec.description:
            # i686-pc-windows-msvc does not imply SSE/SSE2 for clang-cl, but
            # opus' CMake selects SSE sources anyway.  A scalar build is the
            # portable choice for a 32-bit Windows binary.
            extra += ["-DOPUS_DISABLE_INTRINSICS=ON"]
        if spec.release_flags:
            extra.append(f"-DCMAKE_C_FLAGS_RELEASE={' '.join(spec.release_flags)}")
    cmake_configure(spec, src, build, extra)
    cmake_build(spec, build)
    cmake_install(spec, build)


# --------------------------------------------------- generated CMake projects
def write_libopusenc_project(dst: pathlib.Path, ope_src: pathlib.Path,
                             prefix: pathlib.Path,
                             windows: bool = False) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    ope_text = cmake_path_text(ope_src, windows)
    prefix_text = cmake_path_text(prefix, windows)
    (dst / "CMakeLists.txt").write_text(f"""cmake_minimum_required(VERSION 3.16)
project(libopusenc_senav C)
set(OPE_SRC "{ope_text}")
set(PREFIX "{prefix_text}")
add_library(opusenc STATIC
  "${{OPE_SRC}}/src/ogg_packer.c"
  "${{OPE_SRC}}/src/opus_header.c"
  "${{OPE_SRC}}/src/opusenc.c"
  "${{OPE_SRC}}/src/picture.c"
  "${{OPE_SRC}}/src/resample.c"
  "${{OPE_SRC}}/src/unicode_support.c")
target_include_directories(opusenc
  PUBLIC "${{OPE_SRC}}/include"
  PRIVATE "${{OPE_SRC}}/src" "${{PREFIX}}/include/opus")
target_compile_definitions(opusenc PRIVATE
  RANDOM_PREFIX=libopusenc
  OUTSIDE_SPEEX
  RESAMPLE_FULL_SINC_TABLE
  PACKAGE_NAME="libopusenc"
  PACKAGE_VERSION="0.2.1")
if(MSVC)
  target_compile_definitions(opusenc PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()
install(TARGETS opusenc ARCHIVE DESTINATION lib)
install(FILES "${{OPE_SRC}}/include/opusenc.h" DESTINATION include/opus)
""")


def write_opusenc_project(dst: pathlib.Path, tools_src: pathlib.Path,
                          ope_src: pathlib.Path, prefix: pathlib.Path,
                          windows: bool = False, commit: str = "unknown") -> None:
    dst.mkdir(parents=True, exist_ok=True)
    tools_text = cmake_path_text(tools_src, windows)
    ope_text = cmake_path_text(ope_src, windows)
    prefix_text = cmake_path_text(prefix, windows)
    (dst / "CMakeLists.txt").write_text(f"""cmake_minimum_required(VERSION 3.16)
project(opusenc_senav C)
set(TOOLS_SRC "{tools_text}")
set(OPE_SRC "{ope_text}")
set(PREFIX "{prefix_text}")

set(SOURCES
  "${{TOOLS_SRC}}/src/opus_header.c"
  "${{TOOLS_SRC}}/src/opusenc.c"
  "${{TOOLS_SRC}}/src/tagcompare.c"
  "${{TOOLS_SRC}}/src/audio-in.c"
  "${{TOOLS_SRC}}/src/diag_range.c"
  "${{TOOLS_SRC}}/src/flac.c"
  "${{TOOLS_SRC}}/src/picture.c")
if(WIN32)
  list(APPEND SOURCES
    "${{TOOLS_SRC}}/win32/unicode_support.c"
    "${{TOOLS_SRC}}/share/getopt.c"
    "${{TOOLS_SRC}}/share/getopt1.c")
endif()

add_executable(opusenc-senav ${{SOURCES}})
set_target_properties(opusenc-senav PROPERTIES OUTPUT_NAME "opusenc-senav")
target_include_directories(opusenc-senav PRIVATE
  "${{TOOLS_SRC}}/src"
  "${{TOOLS_SRC}}/include"
  "${{TOOLS_SRC}}/win32"
  "${{OPE_SRC}}/include"
  "${{PREFIX}}/include"
  "${{PREFIX}}/include/opus")
target_compile_definitions(opusenc-senav PRIVATE
  PACKAGE_NAME="opus-tools"
  PACKAGE_VERSION="0.2"
  HAVE_LIBFLAC
  SENAV_OPUS_COMMIT="{commit}")
if(WIN32)
  target_compile_definitions(opusenc-senav PRIVATE
    FLAC__NO_DLL
    _CRT_SECURE_NO_WARNINGS)
endif()

find_library(OPUSENC_LIB NAMES opusenc libopusenc PATHS "${{PREFIX}}/lib" NO_DEFAULT_PATH REQUIRED)
find_library(FLAC_LIB NAMES FLAC libFLAC PATHS "${{PREFIX}}/lib" NO_DEFAULT_PATH REQUIRED)
find_library(OPUS_LIB NAMES opus libopus PATHS "${{PREFIX}}/lib" NO_DEFAULT_PATH REQUIRED)
find_library(OGG_LIB NAMES ogg libogg PATHS "${{PREFIX}}/lib" NO_DEFAULT_PATH REQUIRED)
target_link_libraries(opusenc-senav PRIVATE
  "${{OPUSENC_LIB}}" "${{FLAC_LIB}}" "${{OPUS_LIB}}" "${{OGG_LIB}}")
if(WIN32)
  target_link_libraries(opusenc-senav PRIVATE shell32)
else()
  target_link_libraries(opusenc-senav PRIVATE m)
endif()

install(TARGETS opusenc-senav RUNTIME DESTINATION bin)
""")


def find_library_file(prefix: pathlib.Path, names) -> pathlib.Path:
    libdir = prefix / "lib"
    for name in names:
        p = libdir / name
        if p.exists():
            return p
    found = list(libdir.glob("*"))
    raise ToolError(f"library {names[0]} not found in {libdir}; have: "
                    + ", ".join(sorted(p.name for p in found)))


def build_libopusenc(spec: BuildSpec, ope_src: pathlib.Path,
                     project: pathlib.Path, build: pathlib.Path) -> None:
    write_libopusenc_project(project, ope_src, spec.prefix, spec.windows_paths)
    extra = []
    if spec.release_flags:
        extra.append(f"-DCMAKE_C_FLAGS_RELEASE={' '.join(spec.release_flags)}")
    cmake_configure(spec, project, build, extra)
    cmake_build(spec, build)
    cmake_install(spec, build)


def build_opusenc(spec: BuildSpec, tools_src: pathlib.Path, ope_src: pathlib.Path,
                  project: pathlib.Path, build: pathlib.Path) -> pathlib.Path:
    write_opusenc_project(project, tools_src, ope_src, spec.prefix,
                          spec.windows_paths, commit=opus_commit_id())
    extra = []
    if spec.release_flags:
        extra.append(f"-DCMAKE_C_FLAGS_RELEASE={' '.join(spec.release_flags)}")
    cmake_configure(spec, project, build, extra)
    cmake_build(spec, build)
    found = sorted(build.rglob(spec.target.exe_name))
    if not found:
        # MSVC multi-config generator puts the binary under a config subdir.
        for p in build.rglob("*"):
            if p.is_file() and p.name == spec.target.exe_name:
                found.append(p)
    if not found:
        raise ToolError(f"cmake build finished but {spec.target.exe_name} not found under {build}")
    return found[0]


def copy_out(binary: pathlib.Path, out: pathlib.Path, target: Target) -> pathlib.Path:
    if out.suffix and out.suffix.lower() != ".dSYM":
        final = out
    else:
        final = out / target.name / target.exe_name
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, final)
    return final


# ------------------------------------------------------------ strategy specs
def normalize_macos_arch(arch: str) -> str:
    return {"x86_64": "x86_64", "x86-64": "x86_64",
            "arm64": "arm64", "aarch64": "arm64"}.get(arch, arch)


def write_compiler_wrapper(dst: pathlib.Path, base: str, extra: str) -> pathlib.Path:
    dst.mkdir(parents=True, exist_ok=True)
    name = dst / f"{pathlib.Path(base).name}-senav-cc"
    name.write_text(f"#!/bin/sh\nexec {base} {extra} \"$@\"\n")
    name.chmod(0o755)
    return name


def make_native_linux_spec(target: Target, work: pathlib.Path) -> BuildSpec:
    if host_arch() != target.arch:
        raise ToolError(f"native linux build requested but host arch is {host_arch()}, not {target.arch}")
    spec = BuildSpec(target, work / "prefix", work)
    spec.native_host = True
    spec.description = f"native gcc ({host_arch()})"
    return spec


def make_linux_cross_spec(target: Target, work: pathlib.Path) -> BuildSpec:
    if target.arch == "x86":
        gcc = find_in_path(["i686-linux-gnu-gcc", "i586-linux-gnu-gcc", "x86-linux-gnu-gcc"])
        gxx = find_in_path(["i686-linux-gnu-g++", "i586-linux-gnu-g++", "x86-linux-gnu-g++"])
    else:
        gcc = which(f"{target.arch}-linux-gnu-gcc")
        gxx = which(f"{target.arch}-linux-gnu-g++")
    if target.arch == "x86" and host_arch() == "x86_64":
        # Try multilib gcc -m32 / g++ -m32 first; wrapper makes CMake happy.
        probe = work / "probe-m32.c"
        probe.write_text("int main(void){return 0;}\n")
        ok = subprocess.run(["gcc", "-m32", str(probe), "-o", str(work / "probe-m32")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if ok:
            gcc = write_compiler_wrapper(work / "bin", "gcc", "-m32")
            gxx = write_compiler_wrapper(work / "bin", "g++", "-m32")
    if not gcc:
        if target.arch == "x86":
            raise ToolError(
                "linux-x86 cross compiler not found; expected i686-linux-gnu-gcc or a working "
                "`gcc -m32` (install gcc-multilib / gcc-i686-linux-gnu)")
        raise ToolError(
            f"linux-{target.arch} cross compiler not found; expected "
            f"{target.arch}-linux-gnu-gcc and matching libc headers/libs "
            f"(install gcc-{target.arch}-linux-gnu)")
    if not gxx:
        gxx = gcc  # static-library-only projects do not need a real C++ driver
    tc = work / "linux-cross.cmake"
    tc.write_text(f"""set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR {target.arch})
set(CMAKE_C_COMPILER {gcc})
set(CMAKE_CXX_COMPILER {gxx})
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
""")
    spec = BuildSpec(target, work / "prefix", work)
    spec.toolchain_file = tc
    spec.description = f"cross gcc ({gcc.name})"
    return spec


def write_clang_cl_toolchain(spec: BuildSpec, triple: str, xwin: pathlib.Path,
                             lld_link: pathlib.Path) -> pathlib.Path:
    clang_cl = which("clang-cl")
    if clang_cl is None:
        clang_cl = write_compiler_wrapper(spec.work / "bin", str(which("clang")), "--driver-mode=cl")
    crt = xwin / "crt"
    sdk = xwin / "sdk"
    proc = {"x86": "X86", "x86_64": "AMD64", "arm64": "ARM64"}[spec.target.arch]
    lib_arch = {"x86": "x86", "x86_64": "x86_64", "arm64": "aarch64"}[spec.target.arch]
    lld_dir = lld_link.parent
    spec.env["PATH"] = str(lld_dir) + os.pathsep + str(spec.work / "bin") + os.pathsep + spec.env.get("PATH", "")
    tc = spec.work / "windows-clang.cmake"
    tc.write_text(f"""set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_PROCESSOR {proc})
set(CMAKE_C_COMPILER {clang_cl})
set(CMAKE_CXX_COMPILER {clang_cl})
set(CMAKE_AR llvm-lib)
set(CMAKE_RC_COMPILER llvm-rc)
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

set(COMPILE_FLAGS
    --target={triple}
    -Wno-unused-command-line-argument
    -fuse-ld=lld-link
    /imsvc {crt.as_posix()}/include
    /imsvc {sdk.as_posix()}/include/ucrt
    /imsvc {sdk.as_posix()}/include/um
    /imsvc {sdk.as_posix()}/include/shared
    /imsvc {sdk.as_posix()}/include/winrt)
string(REPLACE ";" " " COMPILE_FLAGS "${{COMPILE_FLAGS}}")
set(CMAKE_C_FLAGS "${{CMAKE_C_FLAGS}} ${{COMPILE_FLAGS}}" CACHE STRING "" FORCE)
set(CMAKE_CXX_FLAGS "${{CMAKE_CXX_FLAGS}} ${{COMPILE_FLAGS}} /EHsc" CACHE STRING "" FORCE)

set(LINK_FLAGS
    /manifest:no
    -libpath:"{crt.as_posix()}/lib/{lib_arch}"
    -libpath:"{sdk.as_posix()}/lib/um/{lib_arch}"
    -libpath:"{sdk.as_posix()}/lib/ucrt/{lib_arch}")
string(REPLACE ";" " " LINK_FLAGS "${{LINK_FLAGS}}")
set(CMAKE_EXE_LINKER_FLAGS "${{CMAKE_EXE_LINKER_FLAGS}} ${{LINK_FLAGS}}" CACHE STRING "" FORCE)
set(CMAKE_RC_FLAGS "-I {sdk.as_posix()}/include/um -I {sdk.as_posix()}/include/shared" CACHE STRING "")
""")
    spec.toolchain_file = tc
    return tc


def make_windows_clang_spec(target: Target, work: pathlib.Path) -> BuildSpec:
    clang = which("clang")
    if clang is None:
        raise ToolError("clang not found; install clang for Windows cross builds")
    if host_os() == "windows" and which("clang-cl") is None:
        raise ToolError(
            "backend clang on Windows requires clang-cl (install LLVM and put clang-cl on PATH); "
            "otherwise use the default Visual Studio backend")
    xwin = find_xwin_root()
    if xwin is None:
        raise ToolError(
            "Windows SDK/CRT not found for clang cross build; set XWIN_CACHE_DIR to a cargo-xwin "
            "cache (with crt/ and sdk/) or install MSVC/Windows SDK")
    lld = find_llvm_lld_link()
    if lld is None:
        raise ToolError("lld-link not found; install lld (apt install lld) or set LLD_LINK")
    if which("llvm-lib") is None or which("llvm-rc") is None:
        raise ToolError("llvm-lib / llvm-rc not found; install llvm")
    triple = {"x86": "i686-pc-windows-msvc",
              "x86_64": "x86_64-pc-windows-msvc",
              "arm64": "aarch64-pc-windows-msvc"}[target.arch]
    lib_arch = {"x86": "x86", "x86_64": "x86_64", "arm64": "aarch64"}[target.arch]
    required = [xwin / "crt" / "lib" / lib_arch,
                xwin / "sdk" / "lib" / "ucrt" / lib_arch,
                xwin / "sdk" / "lib" / "um" / lib_arch]
    missing = [str(d) for d in required if not d.exists()]
    if missing:
        raise ToolError(
            f"Windows {target.arch} CRT/SDK libraries are not present in the xwin cache "
            f"({xwin}); missing: {', '.join(missing)}. Run `cargo xwin` / provision that target "
            "or use the Visual Studio backend (--backend vs / WSL).")
    spec = BuildSpec(target, work / "prefix", work)
    write_clang_cl_toolchain(spec, triple, xwin, lld)
    spec.release_flags = ["/MT", "/O2", "/Ob2", "/DNDEBUG"]
    spec.description = f"clang-cl {triple}"
    return spec


# ------------------------------------------------- Visual Studio discovery
def vswhere() -> pathlib.Path | None:
    cands = []
    if host_os() == "windows":
        base = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        cands.append(pathlib.Path(base) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe")
    if is_wsl():
        cands.append(pathlib.Path("/mnt/c/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe"))
    for c in cands:
        if c.exists():
            return c
    return None


def vs_instances(target_arch: str) -> list[pathlib.Path]:
    vsw = vswhere()
    if vsw is None:
        return []
    req = ("Microsoft.VisualStudio.Component.VC.Tools.ARM64" if target_arch == "arm64"
           else "Microsoft.VisualStudio.Component.VC.Tools.x86.x64")
    r = subprocess.run([str(vsw), "-all", "-products", "*", "-requires", req,
                        "-property", "installationPath"],
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        p = pathlib.Path(line)
        if is_wsl():
            lp = wslpath_to_linux(str(p))
            if lp:
                out.append(lp)
            continue
        out.append(p)
    return out


def vs_year(path: pathlib.Path) -> str:
    # installationPath is .../<year>/<edition>; VS 2026's folder is "18".
    return path.parent.name if path.parent.name in ("2022", "18") else "18"


def pick_vs(target_arch: str, preference: str) -> pathlib.Path:
    instances = vs_instances(target_arch)
    if not instances:
        raise ToolError(
            f"Visual Studio with C++ {target_arch} tools not found "
            f"(vswhere query used {target_arch})")
    if preference == "2022":
        for p in instances:
            if vs_year(p) == "2022":
                return p
        raise ToolError("Visual Studio 2022 was requested but was not found")
    if preference == "2026":
        for p in instances:
            if vs_year(p) == "18":
                return p
        raise ToolError("Visual Studio 2026 was requested but was not found")
    for p in instances:
        if vs_year(p) == "2022":
            return p
    for p in instances:
        if vs_year(p) == "18":
            return p
    return instances[0]


def windows_cmake() -> pathlib.Path:
    if host_os() == "windows":
        return find_cmake()
    if is_wsl():
        if env := os.environ.get("WINDOWS_CMAKE"):
            p = pathlib.Path(env)
            if p.exists():
                return p
        p = pathlib.Path("/mnt/c/Program Files/CMake/bin/cmake.exe")
        if p.exists():
            return p
        p = which("cmake.exe")
        if p:
            return p
        raise ToolError(
            "Windows cmake.exe not found; install CMake for Windows or set WINDOWS_CMAKE=/mnt/c/path/to/cmake.exe")
    return find_cmake()


def vs_generator(vs: pathlib.Path, arch: str) -> list[str]:
    year = vs_year(vs)
    gen = "Visual Studio 17 2022" if year == "2022" else "Visual Studio 18 2026"
    a = {"x86": "Win32", "x86_64": "x64", "arm64": "ARM64"}[arch]
    return ["-G", gen, "-A", a]


def make_windows_vs_spec(target: Target, work: pathlib.Path,
                         preference: str) -> BuildSpec:
    if host_os() not in ("windows",) and not is_wsl():
        raise ToolError("Visual Studio builds require Windows or WSL")
    vs = pick_vs(target.arch, preference)
    cmake_exe = windows_cmake()
    spec = BuildSpec(target, work / "prefix", work)
    spec.cmake = cmake_exe
    spec.generator = vs_generator(vs, target.arch)
    spec.config = "Release"
    spec.windows_paths = True
    spec.release_flags = ["/MT", "/O2", "/Ob2", "/DNDEBUG"]
    spec.extra_configure = ["-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded"]
    spec.description = f"Visual Studio {vs_year(vs)} ({vs.name})"
    return spec


# ------------------------------------------------------------------ macOS
def write_macos_toolchain(spec: BuildSpec, arch: str, sdk: pathlib.Path,
                          ld64: pathlib.Path) -> pathlib.Path:
    triple = "arm64-apple-macos11" if arch == "arm64" else "x86_64-apple-macos11"
    tc = spec.work / f"macos-{arch}.cmake"
    tc.write_text(f"""set(CMAKE_SYSTEM_NAME Darwin)
set(CMAKE_SYSTEM_PROCESSOR {"arm64" if arch == "arm64" else "x86_64"})
set(CMAKE_C_COMPILER /usr/bin/clang)
set(CMAKE_C_COMPILER_TARGET {triple})
set(CMAKE_CXX_COMPILER /usr/bin/clang++)
set(CMAKE_CXX_COMPILER_TARGET {triple})
set(CMAKE_AR /usr/bin/llvm-ar)
set(CMAKE_RANLIB /usr/bin/llvm-ranlib)
set(CMAKE_OSX_SYSROOT {sdk.as_posix()})
set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0")
set(CMAKE_C_FLAGS "-isysroot {sdk.as_posix()}")
set(CMAKE_CXX_FLAGS "-isysroot {sdk.as_posix()} -stdlib=libc++")
set(CMAKE_EXE_LINKER_FLAGS "-fuse-ld={ld64.as_posix()} -Wl,-platform_version,macos,11.0,11.0")
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
""")
    spec.toolchain_file = tc
    return tc


def make_macos_cross_spec(target: Target, arch: str, work: pathlib.Path) -> BuildSpec:
    if host_os() == "macos":
        raise ToolError("internal error: native mac target routed to cross spec")
    clang = which("clang")
    if clang is None:
        raise ToolError("clang not found; install clang for macOS cross builds")
    sdk = find_macos_sdk()
    if sdk is None:
        raise ToolError("MacOSX SDK not found; set MACOSX_SDK=/path/to/MacOSX.sdk")
    ld64 = find_ld64_lld()
    if ld64 is None:
        raise ToolError("ld64.lld not found; install lld or set LD64_LLD")
    if which("llvm-ar") is None or which("llvm-ranlib") is None:
        raise ToolError("llvm-ar / llvm-ranlib not found; install llvm")
    spec = BuildSpec(target, work / "prefix", work)
    write_macos_toolchain(spec, arch, sdk, ld64)
    spec.extra_configure = ["-DCMAKE_OSX_ARCHITECTURES=" + normalize_macos_arch(arch)]
    spec.description = f"clang -> {arch}-apple-macos11"
    return spec


def make_macos_native_spec(target: Target, work: pathlib.Path) -> BuildSpec:
    if host_os() != "macos":
        raise ToolError("native macOS build requires a macOS host")
    if which("clang") is None:
        raise ToolError("Apple clang not found; install Xcode Command Line Tools")
    spec = BuildSpec(target, work / "prefix", work)
    spec.native_host = True
    if target.arch == "universal":
        spec.extra_configure = ["-DCMAKE_OSX_ARCHITECTURES=arm64;x86_64",
                                "-DCMAKE_OSX_DEPLOYMENT_TARGET=11.0"]
        spec.description = "Apple clang universal (arm64 + x86_64)"
    else:
        spec.extra_configure = ["-DCMAKE_OSX_ARCHITECTURES=" + normalize_macos_arch(target.arch),
                                "-DCMAKE_OSX_DEPLOYMENT_TARGET=11.0"]
        spec.description = f"Apple clang ({normalize_macos_arch(target.arch)})"
    return spec


def make_work(key: str) -> pathlib.Path:
    w = WORK_ROOT / key
    w.mkdir(parents=True, exist_ok=True)
    return w


def resolve_spec(target: Target, vs_pref: str, backend: str) -> BuildSpec:
    if target.os == "linux":
        if host_os() != "linux":
            raise ToolError(f"linux targets require a Linux host (current host: {host_os()})")
        if target.arch == host_arch():
            return make_native_linux_spec(target, make_work(f"{target.name}/native"))
        return make_linux_cross_spec(target, make_work(f"{target.name}/cross"))

    if target.os == "windows":
        if backend == "vs":
            return make_windows_vs_spec(target, make_work(f"{target.name}/vs"), vs_pref)
        if backend == "clang":
            return make_windows_clang_spec(target, make_work(f"{target.name}/clang"))
        if host_os() == "windows":
            return make_windows_vs_spec(target, make_work(f"{target.name}/vs"), vs_pref)
        if is_wsl():
            # WSL can reach the Windows toolchain; use it unless it is absent.
            try:
                return make_windows_vs_spec(target, make_work(f"{target.name}/vs"), vs_pref)
            except ToolError as e:
                print(f"note: Visual Studio path unavailable ({e}); trying clang-cl cross build")
                return make_windows_clang_spec(target, make_work(f"{target.name}/clang"))
        return make_windows_clang_spec(target, make_work(f"{target.name}/clang"))

    if target.os == "darwin":
        if target.arch == "universal":
            if host_os() == "macos":
                return make_macos_native_spec(target, make_work(f"{target.name}/native"))
            raise ToolError(
                "macos-universal cross build is handled as two clang slices + llvm-lipo; "
                "select macos-arm64 or macos-x86-64 for a single-slice cross build")
        if host_os() == "macos":
            return make_macos_native_spec(target, make_work(f"{target.name}/native"))
        if host_os() == "linux":
            return make_macos_cross_spec(
                target, normalize_macos_arch(target.arch),
                make_work(f"{target.name}/cross-{normalize_macos_arch(target.arch)}"))
        raise ToolError(f"macOS targets are not supported from host OS {host_os()}")

    raise ToolError(f"unsupported target {target.name}")


# ------------------------------------------------------------- orchestration
def cmake_path_text(path: pathlib.Path, windows: bool = False) -> str:
    if windows and is_wsl():
        return wslpath_to_windows(path).replace("\\", "/")
    return path.as_posix()


def build_all_libraries(spec: BuildSpec, srcs: dict[str, pathlib.Path],
                        opus_src: pathlib.Path) -> None:
    wb = spec.work / "build"
    wb.mkdir(parents=True, exist_ok=True)
    print(f"\n== build dependencies for {spec.target.name} ({spec.description}) ==")
    build_libogg(spec, srcs["libogg"], wb / "libogg")
    build_flac(spec, srcs["flac"], wb / "flac")
    build_libopus(spec, opus_src, wb / "libopus")
    proj = spec.work / "proj-libopusenc"
    build_libopusenc(spec, srcs["libopusenc"], proj, wb / "libopusenc")
    print(f"dependency prefix: {spec.prefix}")


def find_opus_tools_src(env_override: str | None = None) -> pathlib.Path:
    if env_override:
        p = pathlib.Path(env_override)
    else:
        env = os.environ.get("OPUS_TOOLS_SRC")
        p = pathlib.Path(env) if env else OPUS_TOOLS_DEFAULT
    p = p.expanduser().resolve()
    if not (p / "src" / "opusenc.c").exists():
        raise ToolError(
            f"opus-tools source not found at {p}; clone opus-tools-custom next to this repo "
            "or set OPUS_TOOLS_SRC=/path/to/opus-tools-custom")
    return p


def vs_stage_sources(target: Target, spec: BuildSpec, srcs: dict[str, pathlib.Path],
                     opus_src: pathlib.Path, tools_src: pathlib.Path):
    """Copy all inputs to the Windows-side temp dir so VS/MSBuild never sees WSL UNC paths."""
    stage = windows_temp_dir() / "opusenc-senav" / target.name
    if stage.exists():
        shutil.rmtree(stage)
    sstage = stage / "src"
    for name, p in [("libogg", srcs["libogg"]), ("flac", srcs["flac"]),
                    ("libopusenc", srcs["libopusenc"]), ("opus", opus_src),
                    ("opus-tools", tools_src)]:
        print(f"stage {name} -> {stage}")
        shutil.copytree(p, sstage / name)
    spec.work = stage / "work"
    spec.prefix = stage / "prefix"
    spec.work.mkdir(parents=True, exist_ok=True)
    spec.prefix.mkdir(parents=True, exist_ok=True)
    return (sstage / "libogg", sstage / "flac", sstage / "libopusenc",
            sstage / "opus", sstage / "opus-tools")


def build_one_target(target: Target, out: pathlib.Path, vs_pref: str,
                     backend: str, tools_override: str | None = None) -> pathlib.Path:
    print(f"\n===== target {target.name} =====")
    spec = resolve_spec(target, vs_pref, backend)
    print(f"host: {host_os()}/{host_arch()} wsl={is_wsl()}")
    print(f"toolchain: {spec.description}")

    srcs = prepare_dependencies()
    opus_src = stage_opus_source()
    tools_src = find_opus_tools_src(tools_override)

    if spec.windows_paths:
        l_ogg, l_flac, l_ope, opus_src, tools_src = vs_stage_sources(
            target, spec, srcs, opus_src, tools_src)
        srcs = {"libogg": l_ogg, "flac": l_flac, "libopusenc": l_ope}

    build_all_libraries(spec, srcs, opus_src)
    proj = spec.work / "proj-opusenc"
    binary = build_opusenc(spec, tools_src, srcs["libopusenc"], proj,
                           spec.work / "build" / "opusenc")
    final = copy_out(binary, out, target)
    print(f"built {final}")
    return final


# ---------------------------------------------------------------- universal
def build_macos_universal(out: pathlib.Path, vs_pref: str, backend: str,
                          tools_override: str | None) -> pathlib.Path:
    if host_os() == "macos":
        return build_one_target(TARGETS["macos-universal"], out, vs_pref, backend,
                                tools_override)
    if find_lipo() is None:
        raise ToolError("llvm-lipo (or lipo) not found; install llvm to build macos-universal on Linux")
    if out.suffix:
        final = out
        base_dir = out.parent / (out.stem + ".slices")
    else:
        final = out / "macos-universal" / "opusenc-senav"
        base_dir = final.parent / ".slices"
    base_dir.mkdir(parents=True, exist_ok=True)
    slices = []
    for arch, key in (("arm64", "macos-arm64"), ("x86_64", "macos-x86-64")):
        t = TARGETS[key]
        b = build_one_target(t, base_dir / arch, vs_pref, backend, tools_override)
        slices.append(b)
    final.parent.mkdir(parents=True, exist_ok=True)
    run([str(find_lipo()), "-create", "-output", str(final)] + [str(p) for p in slices])
    print(f"built universal {final}")
    return final


# --------------------------------------------------------------------- cli
def target_support_line(name: str) -> str:
    t = parse_target(name)
    if name == "macos-universal" and host_os() == "linux":
        if find_lipo() is None:
            return f"{name:18} {'unavailable':12} llvm-lipo not found; install llvm"
        return f"{name:18} {'available':12} two clang cross slices + llvm-lipo"
    try:
        spec = resolve_spec(t, "auto", "auto")
        status = "available"
        detail = spec.description
    except ToolError as e:
        status = "unavailable"
        detail = str(e)
    return f"{name:18} {status:12} {detail}"


def cmd_list() -> int:
    print(f"host: {host_os()}/{host_arch()} wsl={is_wsl()}\n")
    for name in TARGETS:
        print(target_support_line(name))
    print("\naliases:", ", ".join(sorted(TARGET_ALIASES)))
    return 0


def cmd_doctor() -> int:
    print(f"host: {host_os()}/{host_arch()} wsl={is_wsl()}")
    print(f"repo: {ROOT}")
    for label, p in [
        ("cmake", find_cmake()),
        ("opus-tools", find_opus_tools_src() if (OPUS_TOOLS_DEFAULT / "src" / "opusenc.c").exists() else None),
        ("clang", which("clang")),
        ("lld-link", find_llvm_lld_link()),
        ("xwin SDK/CRT", find_xwin_root()),
        ("MacOSX SDK", find_macos_sdk()),
        ("ld64.lld", find_ld64_lld()),
        ("llvm-lipo", find_lipo()),
        ("vswhere", vswhere()),
    ]:
        print(f"{label:14}: {p or 'NOT FOUND'}")
    return 0


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", nargs="?", help="one of the supported targets or aliases")
    ap.add_argument("--out", default=None,
                    help="output directory (or exact file if the name has an extension); default: build/out/<target>/")
    ap.add_argument("--vs", choices=["auto", "2022", "2026"], default="auto",
                    help="Visual Studio preference for Windows targets (default: auto)")
    ap.add_argument("--backend", choices=["auto", "vs", "clang"], default="auto",
                    help="Windows build backend: auto / vs (Visual Studio) / clang (clang-cl cross)")
    ap.add_argument("--opus-tools-src", default=None,
                    help="path to opus-tools-custom source (default: ../opus-tools-custom or $OPUS_TOOLS_SRC)")
    ap.add_argument("--list", action="store_true", help="show target matrix and host capability")
    ap.add_argument("--doctor", action="store_true", help="show detected toolchain paths")
    ap.add_argument("--clean", action="store_true", help="wipe build/_work before building")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    if args.list:
        return cmd_list()
    if args.doctor:
        return cmd_doctor()
    if not args.target:
        print("missing TARGET; use --list to see choices", file=sys.stderr)
        return 2

    target = parse_target(args.target)
    if args.clean and WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    default_out = DEFAULT_OUT_ROOT / target.name
    out = pathlib.Path(args.out) if args.out else default_out

    try:
        if target.name == "macos-universal":
            final = build_macos_universal(out, args.vs, args.backend,
                                          args.opus_tools_src)
        else:
            final = build_one_target(target, out, args.vs, args.backend,
                                     args.opus_tools_src)
    except ToolError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3

    # Native smoke test when the binary can run on this host directly.
    if (target.os == host_os() and
            (target.arch == host_arch() or target.arch == "universal")):
        try:
            r = subprocess.run([str(final), "--version"], text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=30)
            print(r.stdout.strip().splitlines()[0] if r.stdout else f"exit {r.returncode}")
        except Exception as e:
            print(f"(could not smoke-run {final}: {e})")
    print(f"\nOK -> {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
