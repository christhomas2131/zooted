# Zooted for Mac

**Keep your Mac awake without touching a single power setting.**

Zooted sits in your menu bar, holds a `caffeinate` wake-lock for as long as you tell it to, then quietly steps aside. That's the whole deal.

This is the macOS port of the Windows tray utility — same UI, same behavior, Mac-native plumbing underneath. Ships as a **universal2** app: native on both Intel and Apple Silicon, no Rosetta.

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

For a **universal2** build (native Intel + Apple Silicon) you need a universal2
Python with Tk. The easiest is the official installer from
[python.org/downloads/macos](https://www.python.org/downloads/macos/) — it's
universal2 *and* bundles universal2 Tcl/Tk (Homebrew's Python is single-arch and
ships no Tk). Then:

```sh
# Build — venv, deps (fuses Pillow to universal2), icon, and the .app.
# build.sh auto-detects /Library/Frameworks/Python.framework/.../python3.13;
# override with PYTHON=/path/to/universal2/python3 ./build.sh
./build.sh
```

The finished bundle lands at `dist/Zooted.app` and is verified with `lipo` at the
end. Drag it to `/Applications` and launch it — it appears in the menu bar (no
Dock icon).

> **Why the Pillow fuse?** PyPI ships Pillow as *per-architecture* wheels, so on
> a single-arch host `pip` installs a thin Pillow. `build.sh` downloads both the
> x86_64 and arm64 wheels and merges them into one universal2 wheel
> (`delocate-merge`) so the whole app stays fat. On Homebrew Python (no
> universal2), the build falls back to a single-arch `.app`.

### Run the script directly (no build)

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python zooted.py
```

---

## Notes & gotchas

- **Use python.org Python for a universal2 build.** Homebrew Python is single-arch (and has no Tk), so it can only produce a thin `.app`. The python.org installer is universal2 and bundles Tk. `build.sh` warns if the interpreter isn't universal2.
- **First notification may prompt for permission.** macOS asks whether to allow notifications; allow it to see the 5-minute and deactivation alerts.
- **The app is unsigned.** On first launch, macOS Gatekeeper may block it — right-click → Open, or allow it under System Settings → Privacy & Security.

---

## License

MIT — do whatever you want with it.
