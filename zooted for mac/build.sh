#!/usr/bin/env bash
# ============================================================
#  Zooted Build Script (macOS) — universal2 (Intel + Apple Silicon)
#  Produces dist/Zooted.app, a native menu-bar app for both arches.
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "============================================================"
echo " Zooted Build Script (macOS, universal2)"
echo "============================================================"

# ── [1/5] Python + virtualenv ───────────────────────────────
# A universal2 build needs a universal2 (or arm64) Python. The python.org
# installer provides one WITH universal2 Tcl/Tk bundled; Homebrew's Python is
# thin (single-arch) and also lacks Tk. Point PYTHON at a universal2 python,
# or install: https://www.python.org/downloads/macos/
PY="${PYTHON:-/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13}"
[ -x "$PY" ] || PY="python3"
echo "[1/5] Creating virtualenv (.venv) with: $PY"
if [ ! -d .venv ]; then "$PY" -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip

python -c "import tkinter" 2>/dev/null || {
    echo "ERROR: tkinter unavailable in $PY."
    echo "       Use the python.org universal2 Python (bundles Tk), or on Homebrew:"
    echo "       brew install python-tk@\$(python -c 'import sys;print(f\"{sys.version_info.major}.{sys.version_info.minor}\")')"
    exit 1
}

ARCHS=$(lipo -archs "$(python -c 'import sys;print(sys.executable)')" 2>/dev/null || echo "")
echo "      interpreter arches: ${ARCHS:-unknown}"
UNIVERSAL=0
if echo "$ARCHS" | grep -q x86_64 && echo "$ARCHS" | grep -q arm64; then UNIVERSAL=1; fi
[ "$UNIVERSAL" -eq 1 ] || echo "WARNING: interpreter is single-arch ($ARCHS) — the .app will NOT be universal2."

# ── [2/5] Dependencies ──────────────────────────────────────
echo "[2/5] Installing dependencies ..."
python -m pip install --quiet -r requirements.txt

# PyPI ships Pillow as per-arch wheels (not universal2), so on a single-arch
# host pip installs a thin Pillow — which would make the whole app thin. Fuse
# the x86_64 + arm64 wheels into one universal2 wheel and reinstall.
if [ "$UNIVERSAL" -eq 1 ]; then
    IMG=$(python - <<'PY'
import glob, os, PIL
m = glob.glob(os.path.join(os.path.dirname(PIL.__file__), "_imaging*.so"))
print(m[0] if m else "")
PY
)
    if [ -n "$IMG" ] && ! lipo -archs "$IMG" | grep -q arm64; then
        echo "      Pillow is thin — fusing x86_64 + arm64 into universal2 ..."
        PILVER=$(python -c "import PIL; print(PIL.__version__)")
        PYVER=$(python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        ABITAG=$(python -c "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')")
        python -m pip install --quiet delocate
        rm -rf .wheelfuse && mkdir -p .wheelfuse/dl .wheelfuse/out
        for plat in macosx_15_0_x86_64 macosx_15_0_arm64; do
            pip download "Pillow==$PILVER" --only-binary=:all: --no-deps \
                --implementation cp --abi "$ABITAG" --python-version "$PYVER" \
                --platform "$plat" -d .wheelfuse/dl >/dev/null
        done
        delocate-merge .wheelfuse/dl/*x86_64.whl .wheelfuse/dl/*arm64.whl -w .wheelfuse/out
        pip install --quiet --force-reinstall --no-deps .wheelfuse/out/*universal2*.whl
        rm -rf .wheelfuse
        echo "      Pillow fused."
    fi
fi

# ── [3/5] App icon (.icns) ──────────────────────────────────
echo "[3/5] Generating icon.icns ..."
SRC="cartoon_dock_icon_transparent_2048.png"
if [ -f "$SRC" ]; then
    ICONSET="icon.iconset"; rm -rf "$ICONSET"; mkdir -p "$ICONSET"
    for pair in 16:16x16 32:16x16@2x 32:32x32 64:32x32@2x \
                128:128x128 256:128x128@2x 256:256x256 \
                512:256x256@2x 512:512x512 1024:512x512@2x; do
        px="${pair%%:*}"; name="${pair##*:}"
        sips -z "$px" "$px" "$SRC" --out "$ICONSET/icon_${name}.png" >/dev/null
    done
    iconutil -c icns "$ICONSET" -o icon.icns
    rm -rf "$ICONSET"
else
    echo "WARNING: $SRC not found; keeping existing icon.icns if present."
fi

# ── [4/5] Build ─────────────────────────────────────────────
echo "[4/5] Building Zooted.app (this may take a minute) ..."
rm -rf build dist
pyinstaller --noconfirm Zooted.spec

# ── [5/5] Report ────────────────────────────────────────────
echo "[5/5] Verifying ..."
lipo -archs dist/Zooted.app/Contents/MacOS/Zooted
echo "============================================================"
echo " Build complete!  ==>  dist/Zooted.app"
echo " Package for sharing:  ./make_dmg.sh"
echo "============================================================"
