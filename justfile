# opusenc-senav build matrix.
#
#   just list                     show target/OS matrix and host capability
#   just doctor                   show detected toolchain paths
#   just build <target>           generic entry (e.g. windows-arm64)
#   just build-windows-arm64      dedicated recipes, one per target
#
# Recipe parameters are positional, in this order:
#   OUT     output directory (default: build/out) or an exact file path
#   VS      Visual Studio preference for Windows targets: auto | 2022 | 2026
#   BACKEND Windows build backend: auto | vs | clang
#
# Examples:
#   just build-linux-arm64
#   just build-windows-arm64 /tmp/out 2022 vs
#   just build windows-x86-64 /tmp/out auto clang

set shell := ["bash", "-uc"]

# show all targets and whether the current host/toolchain can build them
list:
    @python3 tools/build_opusenc.py --list

# show detected toolchain paths and host capabilities
doctor:
    @python3 tools/build_opusenc.py --doctor

# generic recipe: `just build windows-x86-64`
build TARGET OUT="build/out" VS="auto" BACKEND="auto":
    @python3 tools/build_opusenc.py "{{TARGET}}" --out "{{OUT}}" --vs "{{VS}}" --backend "{{BACKEND}}"

# Windows
build-windows-x86 OUT="build/out" VS="auto" BACKEND="auto":
    @python3 tools/build_opusenc.py windows-x86 --out "{{OUT}}" --vs "{{VS}}" --backend "{{BACKEND}}"

build-windows-x86-64 OUT="build/out" VS="auto" BACKEND="auto":
    @python3 tools/build_opusenc.py windows-x86-64 --out "{{OUT}}" --vs "{{VS}}" --backend "{{BACKEND}}"

build-windows-arm64 OUT="build/out" VS="auto" BACKEND="auto":
    @python3 tools/build_opusenc.py windows-arm64 --out "{{OUT}}" --vs "{{VS}}" --backend "{{BACKEND}}"

# Windows aliases
build-windows-x64 OUT="build/out" VS="auto" BACKEND="auto":
    @python3 tools/build_opusenc.py windows-x86-64 --out "{{OUT}}" --vs "{{VS}}" --backend "{{BACKEND}}"

# Linux
build-linux-x86 OUT="build/out" VS="auto" BACKEND="auto":
    @python3 tools/build_opusenc.py linux-x86 --out "{{OUT}}" --vs "{{VS}}" --backend "{{BACKEND}}"

build-linux-x86-64 OUT="build/out" VS="auto" BACKEND="auto":
    @python3 tools/build_opusenc.py linux-x86-64 --out "{{OUT}}" --vs "{{VS}}" --backend "{{BACKEND}}"

build-linux-arm64 OUT="build/out" VS="auto" BACKEND="auto":
    @python3 tools/build_opusenc.py linux-arm64 --out "{{OUT}}" --vs "{{VS}}" --backend "{{BACKEND}}"

# Linux aliases
build-linux-x64 OUT="build/out" VS="auto" BACKEND="auto":
    @python3 tools/build_opusenc.py linux-x86-64 --out "{{OUT}}" --vs "{{VS}}" --backend "{{BACKEND}}"

# macOS
build-macos-x86-64 OUT="build/out" VS="auto" BACKEND="auto":
    @python3 tools/build_opusenc.py macos-x86-64 --out "{{OUT}}" --vs "{{VS}}" --backend "{{BACKEND}}"

build-macos-arm64 OUT="build/out" VS="auto" BACKEND="auto":
    @python3 tools/build_opusenc.py macos-arm64 --out "{{OUT}}" --vs "{{VS}}" --backend "{{BACKEND}}"

build-macos-universal OUT="build/out" VS="auto" BACKEND="auto":
    @python3 tools/build_opusenc.py macos-universal --out "{{OUT}}" --vs "{{VS}}" --backend "{{BACKEND}}"

# remove per-target work dirs; keep downloaded dependency tarballs
clean-work:
    @rm -rf build/_work

# remove everything under the git-ignored build directory
distclean:
    @rm -rf build
