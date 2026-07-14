#!/usr/bin/env bash
# ============================================================
#  Zooted Build Script (macOS)
#  Produces dist/Zooted.app — a menu-bar sleep-prevention app.
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "============================================================"
echo " Zooted Build Script (macOS)"
echo "============================================================"

# ── [1/4] Python + virtualenv ───────────────────────────────
# Requires the Tk bindings for your Python. With Homebrew Python:
#     brew install python-tk@3.14
PY="${PYTHON:-python3}"
echo "[1/4] Creating virtualenv (.venv) with $PY ..."
if [ ! -d .venv ]; then
    "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip

echo "[1/4] Verifying tkinter is available ..."
if ! python -c "import tkinter" 2>/dev/null; then
    echo "ERROR: tkinter is not available in $PY."
    echo "       On Homebrew Python run:  brew install python-tk@\$(python -c 'import sys;print(f\"{sys.version_info.major}.{sys.version_info.minor}\")')"
    exit 1
fi

# ── [2/4] Dependencies ──────────────────────────────────────
echo "[2/4] Installing dependencies ..."
python -m pip install --quiet -r requirements.txt

# ── [3/4] App icon (.icns) ──────────────────────────────────
echo "[3/4] Generating icon.icns ..."
SRC="cartoon_dock_icon_transparent_2048.png"
if [ -f "$SRC" ]; then
    ICONSET="icon.iconset"
    rm -rf "$ICONSET"; mkdir -p "$ICONSET"
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

# ── [4/4] Build ─────────────────────────────────────────────
echo "[4/4] Building Zooted.app (this may take a minute) ..."
rm -rf build dist
pyinstaller --noconfirm Zooted.spec

echo
echo "============================================================"
echo " Build complete!  ==>  dist/Zooted.app"
echo "============================================================"
echo "Drag dist/Zooted.app to /Applications and launch it."
echo "It lives in the menu bar (no Dock icon)."
