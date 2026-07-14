# Zooted for Mac

**Keep your Mac awake without touching a single power setting.**

Zooted sits in your menu bar, holds a `caffeinate` wake-lock for as long as you tell it to, then quietly steps aside. That's the whole deal.

This is the macOS port of the Windows tray utility — same UI, same behavior, Mac-native plumbing underneath.

---

## Features

- **Duration presets** — 30 min, 1 hr, 2 hrs, 4 hrs, 8 hrs, or indefinite. Pick one at launch or switch any time from the menu bar.
- **Keep display on** — prevents the screen from sleeping, not just system idle sleep.
- **Timer with notifications** — a macOS notification when 5 minutes are left, and another when Zooted deactivates.
- **Loop on expiry** — auto-restart the timer when it runs out.
- **Optional startup picker** — show the duration screen on launch, or skip it and go straight to the menu bar.
- **Launch at Login** — installs a per-user LaunchAgent so Zooted starts when you log in.
- **Persistent logging** — every activation, deactivation, and wake-lock change is written to `~/Library/Application Support/Zooted/zooted.log` (capped at 1 MB, auto-rotated).
- **Single-instance** — launching again does nothing; the menu-bar icon is already there.

---

## How the Mac version differs from Windows

| Concern              | Windows                          | macOS (this port)                                   |
| -------------------- | -------------------------------- | --------------------------------------------------- |
| Wake-lock            | `SetThreadExecutionState`        | `caffeinate -i [-d]` child process                  |
| Launch at startup    | `HKCU\...\Run` registry value    | LaunchAgent plist in `~/Library/LaunchAgents`       |
| Single instance      | Named mutex                      | `flock` on `zooted.lock`                            |
| Notifications        | `win10toast` / `plyer`           | `osascript` (`display notification`)                |
| Config location      | `%APPDATA%\Zooted`               | `~/Library/Application Support/Zooted`              |
| Packaging            | `Zooted.exe` (PyInstaller)       | `Zooted.app` (PyInstaller, `LSUIElement`)           |

---

## Build from source

Requires **Python 3.11+** with Tk. On Homebrew Python you must install the Tk bindings separately:

```sh
# Homebrew Python — install Tk bindings for your version, e.g. 3.14:
brew install python-tk@3.14

# Build — creates a virtualenv, installs deps, generates the icon, builds the .app
./build.sh
```

The finished bundle lands at `dist/Zooted.app`. Drag it to `/Applications` and launch it — it appears in the menu bar (no Dock icon).

### Run the script directly (no build)

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python zooted.py
```

---

## Notes & gotchas

- **Homebrew Python has no Tk by default.** `import tkinter` fails until `python-tk@<version>` is installed. `build.sh` checks for this and tells you.
- **First notification may prompt for permission.** macOS asks whether to allow notifications; allow it to see the 5-minute and deactivation alerts.
- **The app is unsigned.** On first launch, macOS Gatekeeper may block it — right-click → Open, or allow it under System Settings → Privacy & Security.

---

## License

MIT — do whatever you want with it.
