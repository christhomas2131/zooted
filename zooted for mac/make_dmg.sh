#!/usr/bin/env bash
# ============================================================
#  Build a drag-to-Applications DMG for Zooted.app
#  Output: Zooted-<version>.dmg
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

APP="dist/Zooted.app"
VOLNAME="Zooted"
VERSION="1.0.0"
DMG="Zooted-${VERSION}.dmg"

if [ ! -d "$APP" ]; then
    echo "ERROR: $APP not found. Run ./build.sh first."
    exit 1
fi

echo "[1/5] Preparing staging folder ..."
STAGE="$(mktemp -d)/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"    # drag target

# Clean any stale mount from a previous run.
hdiutil detach "/Volumes/$VOLNAME" >/dev/null 2>&1 || true

echo "[2/5] Creating writable image ..."
SIZE_MB=$(( $(du -sm "$STAGE" | cut -f1) + 60 ))
RW="$(mktemp -u).dmg"
hdiutil create -volname "$VOLNAME" -srcfolder "$STAGE" -fs HFS+ \
    -format UDRW -size "${SIZE_MB}m" -ov -quiet -o "$RW"

echo "[3/5] Mounting and laying out the window ..."
DEV=$(hdiutil attach -readwrite -noverify -noautoopen "$RW" | \
      grep -Eo '/Volumes/.*' | head -1)
sleep 1

# Finder window layout. Non-fatal: a denied automation prompt or headless
# session just leaves default icon positions — the DMG still works.
osascript <<EOF || echo "   (layout skipped — DMG is still functional)"
tell application "Finder"
    tell disk "$VOLNAME"
        open
        set current view of container window to icon view
        set toolbar visible of container window to false
        set statusbar visible of container window to false
        set the bounds of container window to {250, 150, 750, 500}
        set opts to the icon view options of container window
        set arrangement of opts to not arranged
        set icon size of opts to 100
        set position of item "Zooted.app" of container window to {130, 165}
        set position of item "Applications" of container window to {370, 165}
        update without registering applications
        delay 1
        close
    end tell
end tell
EOF
sync

echo "[4/5] Detaching and compressing ..."
hdiutil detach "$DEV" -quiet || hdiutil detach "$DEV" -force -quiet || true
rm -f "$DMG"
hdiutil convert "$RW" -format UDZO -imagekey zlib-level=9 -ov -quiet -o "$DMG"
rm -f "$RW"
rm -rf "$(dirname "$STAGE")"

echo "[5/5] Done."
echo "============================================================"
echo " Created: $(pwd)/$DMG"
echo " Size:    $(du -h "$DMG" | cut -f1)"
echo "============================================================"
