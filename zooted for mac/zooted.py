#!/usr/bin/env python3
"""
Zooted — macOS sleep prevention menu-bar utility.
"""

from __future__ import annotations

__version__ = "1.0.0"

import fcntl
import json
import logging
import logging.handlers
import math
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pystray
from PIL import Image, ImageDraw, ImageFilter
import tkinter as tk

# ──────────────────────────────────────────────────────────────────────────────
# Paths & constants
# ──────────────────────────────────────────────────────────────────────────────

_SUPPORT    = Path.home() / "Library" / "Application Support"
CONFIG_DIR  = _SUPPORT / "Zooted"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOCK_FILE   = CONFIG_DIR / "zooted.lock"
_APP_NAME   = "Zooted"
LOG_FILE    = CONFIG_DIR / "zooted.log"

# Launch-at-login is implemented as a per-user LaunchAgent plist.
_LAUNCH_LABEL = "com.zooted.app"
_LAUNCH_DIR   = Path.home() / "Library" / "LaunchAgents"
_LAUNCH_PLIST = _LAUNCH_DIR / f"{_LAUNCH_LABEL}.plist"

DURATION_OPTIONS: list[tuple[str, str, int | None]] = [
    ("30 MIN",  "quick boost",  30),
    ("1 HR",    "standard",     60),
    ("2 HRS",   "deep focus",  120),
    ("4 HRS",   "half day",    240),
    ("8 HRS",   "full shift",  480),
    ("∞",       "no limit",   None),
]

SETTINGS_DEFAULTS: dict = {
    "launch_at_startup":         False,
    "keep_display_on":           True,
    "loop_on_expiry":            False,
    "show_notifications":        True,
    "show_duration_on_startup":  True,
}

SETTINGS_META: list[tuple[str, str, str]] = [
    ("launch_at_startup",        "Launch at Login",
     "Start Zooted automatically when you log in to macOS"),
    ("show_duration_on_startup", "Show duration picker on startup",
     "Show the time selection screen when Zooted opens."),
    ("keep_display_on",          "Keep Display On",
     "Prevent screen timeout, not just system sleep"),
    ("loop_on_expiry",           "Loop on Expiry",
     "Automatically restart the timer when it runs out"),
    ("show_notifications",       "Show Notifications",
     "Send toast alerts before and after deactivation"),
]

_SENTINEL = object()
_lock_fp = None   # held open for process lifetime to retain the instance lock

# Single persistent Tk root — all dialogs are Toplevel children of this.
# Never destroyed; mainloop() is called twice (once for first-run, once for app).
_tk_root: tk.Tk | None = None

# ──────────────────────────────────────────────────────────────────────────────
# Colour palette
# ──────────────────────────────────────────────────────────────────────────────

_C_BG      = "#0F0E0C"   # warm near-black — amber undertone echoing the portrait
_C_CARD    = "#181613"   # warm dark surface
_C_CARD_HL = "#1E1C18"   # warm hover surface
_C_CARD_ON = "#1B1610"   # selected surface — dark amber warmth, portrait-pulled
_C_BORDER  = "#2A2620"   # warm structural border
_C_ACCENT  = "#4A7A5A"   # deep emerald, fractionally warmed
_C_ACCENT2 = "#5C8E6C"   # accent hover lift
_C_TEXT    = "#EAE6DE"   # warm off-white — echoes portrait highlight tone
_C_SUB     = "#7C7870"   # warm secondary gray
_C_MUTED   = "#3A3630"   # warm muted — outer border, close button
_C_CTA     = "#152618"   # CTA fill — ink-dark bottle green, denser than accent
_C_CTA_H   = "#1C3420"   # CTA hover — fractional lift, stays dark
_C_CTA_B   = "#224030"   # CTA border — subtle emerald edge definition
_CORNER_R  = 10

# Font families — resolved to best available at runtime by _init_fonts().
# _FF is the variable UI face; _FM is the monospace face used for the wordmark,
# duration values, and CTA labels (Consolas on Windows → Menlo/SF Mono here).
_FF = "Helvetica Neue"
_FM = "Menlo"


def _init_fonts() -> None:
    """Upgrade _FF/_FM to the best available typefaces after Tk is initialised."""
    global _FF, _FM, _F_TITLE, _F_CARD, _F_BTN
    import tkinter.font as tkfont
    available = set(tkfont.families())
    for candidate in ("SF Pro Text", "Helvetica Neue", "Helvetica", "Arial"):
        if candidate in available:
            _FF = candidate
            break
    for candidate in ("SF Mono", "Menlo", "Monaco", "Courier New"):
        if candidate in available:
            _FM = candidate
            break
    # Rebuild the fixed-family roles against the resolved monospace face.
    _F_TITLE = (_FM, 14, "")
    _F_CARD  = (_FM, 10, "")
    _F_BTN   = (_FM,  9, "")


# ──────────────────────────────────────────────────────────────────────────────
# Type scale — semantic font roles
# ──────────────────────────────────────────────────────────────────────────────
# Monospace roles are fixed-family tuples; _init_fonts() rebuilds them against
# the resolved _FM once Tk is up. _FF-based roles are *recipes* (size, style)
# resolved by _ff() at call time — that reads _FF after _init_fonts() runs
# (post-Tk), so the family upgrade is picked up correctly (a module-level tuple
# would have baked in the pre-init fallback).

_F_TITLE = ("Menlo", 14, "")   # dialog wordmark ("ZOOTED")
_F_CARD  = ("Menlo", 10, "")   # duration-card time value ("30 MIN")
_F_BTN   = ("Menlo", 9,  "")   # primary CTA button label
_F_ROW   = (10, "")               # settings row primary label
_F_DESC  = (8,  "")               # row desc / tagline / status / cancel / subtitle
_F_HINT  = (7,  "")               # version footer only

def _ff(recipe: tuple) -> tuple:
    """Resolve a variable-family font recipe against the runtime _FF."""
    size, style = recipe
    return (_FF, size, style) if style else (_FF, size)


# ──────────────────────────────────────────────────────────────────────────────
# Wake-lock helpers (caffeinate)
# ──────────────────────────────────────────────────────────────────────────────
# macOS ships `caffeinate` for exactly this. We hold the lock by keeping a
# caffeinate child process alive and release it by terminating that process:
#   -i  prevent idle system sleep   (≈ Windows ES_SYSTEM_REQUIRED)
#   -d  prevent display sleep       (≈ Windows ES_DISPLAY_REQUIRED)
# A single worker thread serialises state changes off a queue, mirroring the
# original design so callers stay fire-and-forget.

_wake_lock_queue: queue.SimpleQueue = None  # type: ignore[assignment]
_caffeinate_proc: subprocess.Popen | None = None

def _wake_lock_worker() -> None:
    """Runs forever on its own thread; reconciles the caffeinate child to state."""
    global _caffeinate_proc
    logging.info("WakeLock worker thread started")
    while True:
        try:
            enabled, keep_display = _wake_lock_queue.get()
            # Tear down any existing lock first — idempotent, avoids stacking.
            if _caffeinate_proc is not None and _caffeinate_proc.poll() is None:
                _caffeinate_proc.terminate()
                try:
                    _caffeinate_proc.wait(timeout=2)
                except Exception:
                    _caffeinate_proc.kill()
            _caffeinate_proc = None
            if enabled:
                # -w <pid> ties the lock to our lifetime: caffeinate auto-exits
                # if we die (crash / SIGKILL included), so it can never orphan
                # and hold the Mac awake — matching Windows' auto-clear-on-exit.
                args = ["caffeinate", "-w", str(os.getpid()), "-i"]
                if keep_display:
                    args.append("-d")
                _caffeinate_proc = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                logging.info("caffeinate started: %s (pid=%s)", args, _caffeinate_proc.pid)
            else:
                logging.info("caffeinate released")
        except Exception:
            logging.exception("WakeLock worker: unhandled exception — continuing")

def _set_sleep_prevention(enabled: bool, keep_display: bool = True) -> None:
    logging.info("_set_sleep_prevention(enabled=%s, keep_display=%s) queuing",
                 enabled, keep_display)
    _wake_lock_queue.put((enabled, keep_display))


def _acquire_instance_lock() -> bool:
    """Single-instance guard via an exclusive advisory lock on a lockfile.

    The file handle is intentionally kept alive for the process lifetime; the
    lock releases automatically when the process exits.
    """
    global _lock_fp
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _lock_fp = open(LOCK_FILE, "w")
        fcntl.flock(_lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _launch_program_args() -> list[str]:
    """Command LaunchAgent should run: the frozen .app binary, or python + script."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.abspath(__file__)]


def _set_startup(enabled: bool) -> None:
    try:
        if enabled:
            _LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
            args_xml = "".join(f"\t\t<string>{a}</string>\n"
                               for a in _launch_program_args())
            plist = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n'
                '<dict>\n'
                f'\t<key>Label</key>\n\t<string>{_LAUNCH_LABEL}</string>\n'
                '\t<key>ProgramArguments</key>\n\t<array>\n'
                f'{args_xml}'
                '\t</array>\n'
                '\t<key>RunAtLoad</key>\n\t<true/>\n'
                '</dict>\n'
                '</plist>\n'
            )
            _LAUNCH_PLIST.write_text(plist, encoding="utf-8")
        else:
            if _LAUNCH_PLIST.exists():
                _LAUNCH_PLIST.unlink()
    except Exception:
        logging.exception("_set_startup(%s) failed", enabled)


def _get_startup() -> bool:
    return _LAUNCH_PLIST.exists()

# ──────────────────────────────────────────────────────────────────────────────
# Notifications
# ──────────────────────────────────────────────────────────────────────────────

def _as_applescript_str(s: str) -> str:
    """Quote a Python string as an AppleScript string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _notify(title: str, body: str) -> None:
    def _run() -> None:
        try:
            script = (f"display notification {_as_applescript_str(body)} "
                      f"with title {_as_applescript_str(title)}")
            subprocess.run(
                ["osascript", "-e", script],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=6,
            )
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()

# ──────────────────────────────────────────────────────────────────────────────
# Resource helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_resource(filename: str) -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / filename          # type: ignore[attr-defined]
    try:
        return Path(__file__).parent / filename
    except NameError:
        return Path(filename)

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

def _load_config() -> dict | None:
    try:
        if not CONFIG_FILE.exists():
            return None
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if "default_duration_minutes" not in data:
            return None
        for k, v in SETTINGS_DEFAULTS.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return None


def _save_config_partial(**kwargs) -> None:
    existing: dict = {}
    try:
        if CONFIG_FILE.exists():
            existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    existing.update(kwargs)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")

# ──────────────────────────────────────────────────────────────────────────────
# Logo / icon image builders
# ──────────────────────────────────────────────────────────────────────────────

def _build_logo_pil(size: int = 92) -> Image.Image:
    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hp = 4
    hd.ellipse([hp, hp, size - hp, size - hp], fill=(58, 122, 87, 90))
    halo = halo.filter(ImageFilter.GaussianBlur(radius=9))
    circle = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cd = ImageDraw.Draw(circle)
    cp = 8
    cd.ellipse([cp, cp, size - cp, size - cp], fill=(17, 18, 20, 245))
    cd.ellipse([cp, cp, size - cp, size - cp], outline=(58, 122, 87, 255), width=2)
    cd.ellipse([cp + 5, cp + 5, size - cp - 5, size - cp - 5],
               outline=(30, 60, 44, 60), width=1)
    m, yt, yb = size * 0.24, size * 0.30, size * 0.70
    lw = max(3, round(size / 18))
    for seg in ([(m, yt), (size - m, yt)],
                [(size - m, yt), (m, yb)],
                [(m, yb), (size - m, yb)]):
        cd.line(seg, fill=(58, 122, 87, 255), width=lw)
    r = lw // 2
    for px, py in [(m, yt), (size - m, yt), (m, yb), (size - m, yb)]:
        cd.ellipse([px - r, py - r, px + r, py + r], fill=(58, 122, 87, 255))
    return Image.alpha_composite(halo, circle)


def _load_head_image(target_w: int = 150) -> Image.Image:
    """Load the floating head portrait (zooted_head_icon_plate_1024.png)."""
    for fname in ("zooted_head_icon_plate_1024.png", "zooted_head_icon_1024.png"):
        src = _get_resource(fname)
        if src.exists():
            img = Image.open(src).convert("RGBA")
            orig_w, orig_h = img.size
            target_h = round(orig_h * target_w / orig_w)
            img = img.resize((target_w, target_h), Image.LANCZOS)
            bg = Image.new("RGBA", img.size, (*bytes.fromhex(_C_BG[1:]), 255))
            bg.paste(img, mask=img.split()[3])
            return bg
    return _build_logo_pil(target_w)


def _load_logo_image(target_w: int = 130) -> Image.Image:
    src = _get_resource("logo_zoot.png")
    if not src.exists():
        return _build_logo_pil(target_w)
    img = Image.open(src).convert("RGBA")
    pixels = list(img.getdata())
    img.putdata([(r, g, b, 0) if r > 238 and g > 238 and b > 238
                 else (r, g, b, a) for r, g, b, a in pixels])
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    orig_w, orig_h = img.size
    target_h = round(orig_h * target_w / orig_w)
    img = img.resize((target_w, target_h), Image.LANCZOS)
    bg = Image.new("RGBA", img.size, (*bytes.fromhex(_C_BG[1:]), 255))
    bg.paste(img, mask=img.split()[3])
    return _apply_edge_fade(bg, fade=22)


def _apply_edge_fade(img: Image.Image, fade: int = 22) -> Image.Image:
    """Dissolve portrait edges into background with an ease-in alpha gradient."""
    from PIL import ImageChops
    w, h = img.size
    mask = Image.new("L", (w, h), 255)
    bg_pixel = (*bytes.fromhex(_C_BG[1:]), 255)
    overlay = Image.new("RGBA", (w, h), bg_pixel)
    pixels = mask.load()
    for y in range(h):
        for x in range(w):
            # distance to nearest edge as a 0..1 fraction
            dx = min(x, w - 1 - x) / fade
            dy = min(y, h - 1 - y) / fade
            t  = min(min(dx, dy), 1.0)
            # ease-in: slow start — portrait centre stays opaque
            eased = t * t
            pixels[x, y] = round(eased * 255)
    composited = Image.composite(img, overlay, mask)
    return composited


_tray_cache: dict[bool, Image.Image] = {}


def _face_square_crop(img: Image.Image) -> Image.Image:
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    w, h = img.size
    sq    = w
    eye_y = int(h * 0.42)
    y1 = max(0, eye_y - sq // 2)
    y2 = y1 + sq
    if y2 > h:
        y2 = h
        y1 = max(0, h - sq)
    return img.crop((0, y1, w, y2))


def _render_face_icon(size: int, active: bool) -> Image.Image | None:
    from PIL import ImageFilter, ImageOps
    # High-res face cutout → tight crop → sharp downsample
    for fname in ("icon_v2.png", "logo_zoot.png"):
        src = _get_resource(fname)
        if src.exists():
            break
    else:
        return None
    img = Image.open(src).convert("RGBA")
    # Strip white/near-white background
    img.putdata([(pr, pg, pb, 0) if pr > 230 and pg > 230 and pb > 230
                 else (pr, pg, pb, pa) for pr, pg, pb, pa in img.getdata()])
    # Crop tight to face content
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    # Two-step downsample — intermediate pass preserves edge detail
    mid = max(size * 4, 256)
    if max(img.size) > mid:
        img = img.resize((mid, mid), Image.LANCZOS)
    img = img.resize((size, size), Image.LANCZOS)
    img = img.filter(ImageFilter.UnsharpMask(radius=0.8, percent=180, threshold=2))
    if not active:
        rc, gc, bc, ac = img.split()
        grey = ImageOps.grayscale(Image.merge("RGB", (rc, gc, bc))).convert("RGB")
        grey = grey.point(lambda x: int(x * 0.45))
        img  = Image.merge("RGBA", (*grey.split(), ac))
    bg = Image.new("RGBA", (size, size), (11, 11, 11, 255))
    bg.paste(img, mask=img.split()[3])
    return bg


def _make_icon_image(active: bool) -> Image.Image:
    if active in _tray_cache:
        return _tray_cache[active]
    face = _render_face_icon(64, active)
    if face is not None:
        _tray_cache[active] = face
        return face
    sz, pad = 64, 4
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    outer = (pad, pad, sz - pad - 1, sz - pad - 1)
    if active:
        d.ellipse(outer, fill=(0, 255, 65, 255))
        d.ellipse((pad + 11, pad + 9, pad + 23, pad + 21),
                  fill=(200, 255, 210, 140))
    else:
        d.ellipse(outer, fill=(72, 72, 72, 210))
        d.ellipse((pad + 9, pad + 9, sz - pad - 10, sz - pad - 10),
                  fill=(44, 44, 44, 210))
    _tray_cache[active] = img
    return img

# ──────────────────────────────────────────────────────────────────────────────
# Canvas geometry helpers
# ──────────────────────────────────────────────────────────────────────────────

def _lerp_color(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    return (f"#{round(r1+(r2-r1)*t):02x}"
            f"{round(g1+(g2-g1)*t):02x}"
            f"{round(b1+(b2-b1)*t):02x}")


def _rounded_poly(x1: int, y1: int, x2: int, y2: int, r: int) -> list[int]:
    return [
        x1 + r, y1,     x2 - r, y1,
        x2,     y1,     x2,     y1 + r,
        x2,     y2 - r, x2,     y2,
        x2 - r, y2,     x1 + r, y2,
        x1,     y2,     x1,     y2 - r,
        x1,     y1 + r, x1,     y1,
    ]


def _draw_pill_btn(cv: tk.Canvas, w: int, h: int, *,
                   fill: str, border: str, text: str,
                   text_fill: str, font) -> tuple:
    r    = 3
    pts  = _rounded_poly(1, 1, w - 1, h - 1, r)
    bdr  = border if border else fill
    rect = cv.create_polygon(pts, smooth=True, fill=fill, outline=bdr, width=1)
    hl_id = shd_id = None
    if border:
        # Top catch-light + bottom shadow — surface directionality without gradients.
        # Held as ids so hover can recompute their colour against the new fill
        # (otherwise the lines stay keyed to the resting tone and read as stale).
        hl  = _lerp_color(fill, "#FFFFFF", 0.14)
        shd = _lerp_color(fill, "#000000", 0.20)
        hl_id  = cv.create_line(r + 3, 2,     w - r - 3, 2,     fill=hl,  width=1)
        shd_id = cv.create_line(r + 3, h - 3, w - r - 3, h - 3, fill=shd, width=1)
    txt  = cv.create_text(w // 2, h // 2, text=text, fill=text_fill, font=font)
    return rect, txt, hl_id, shd_id

# ──────────────────────────────────────────────────────────────────────────────
# Duration card widget
# ──────────────────────────────────────────────────────────────────────────────

class _DurationCard:
    def __init__(self, parent: tk.Widget, label: str, sub: str,
                 variable: tk.IntVar, value: int, cw: int, ch: int) -> None:
        self._var, self._val = variable, value
        self._label, self._sub = label, sub
        self._cv = tk.Canvas(parent, width=cw, height=ch,
                             bg=_C_BG, highlightthickness=0, cursor="hand2")
        self._cw, self._ch = cw, ch
        self._cv.bind("<Button-1>", self._select)
        self._cv.bind("<Enter>",    lambda e: self._set_hover(True))
        self._cv.bind("<Leave>",    lambda e: self._set_hover(False))
        self._hover          = False
        self._prev_sel       = False
        self._anim_gen       = 0   # incremented to abort stale animation callbacks
        self._initialized    = False
        self._current_border = _C_BORDER   # last settled border colour (animation start)
        variable.trace_add("write", self._redraw)
        self._redraw()
        self._initialized = True

    def _select(self, *_) -> None:
        self._var.set(self._val)

    def _set_hover(self, v: bool) -> None:
        self._hover = v
        self._redraw()

    def _redraw(self, *_) -> None:
        w, h = self._cw, self._ch
        self._cv.delete("all")
        sel = self._var.get() == self._val

        bg  = _C_CARD_ON if sel else (_C_CARD_HL if self._hover else _C_CARD)
        pts = _rounded_poly(1, 1, w - 1, h - 1, 2)
        target = _C_ACCENT if sel else _C_BORDER

        if not self._initialized:
            # First paint — settle at the resting target, no animation.
            self._current_border = target
            self._cv.create_polygon(pts, smooth=False, fill=bg,
                                    outline=target, width=1)
            self._anim_gen += 1
        elif target != self._current_border:
            # State changed — walk border from previous target to new target.
            # Same 6 × 20 ms ease-out for both directions, so select and
            # deselect read as a symmetric pair rather than a fade + a snap.
            poly = self._cv.create_polygon(pts, smooth=False, fill=bg,
                                           outline=self._current_border, width=1)
            self._anim_gen += 1
            gen   = self._anim_gen
            start = self._current_border
            self._current_border = target
            self._cv.after(15, lambda: self._animate_border(1, gen, poly, start, target))
        else:
            # Static render — state unchanged but bg may have (hover).
            self._cv.create_polygon(pts, smooth=False, fill=bg,
                                    outline=target, width=1)
            self._anim_gen += 1   # abort any in-flight animation

        self._prev_sel = sel

        # Left-aligned text — duration value + descriptor as a two-line field
        lbl_color = "#F5F2EA" if sel else _C_TEXT
        sub_color = _C_ACCENT if sel else _C_SUB
        self._cv.create_text(14, h // 2 - 8, text=self._label,
                             fill=lbl_color, anchor="w",
                             font=_F_CARD)
        self._cv.create_text(14, h // 2 + 8, text=self._sub,
                             fill=sub_color, anchor="w",
                             font=_ff(_F_DESC))

    def _animate_border(self, step: int, gen: int, poly: int,
                        c0: str, c1: str) -> None:
        """Ease-out border colour fade c0 → c1 — 6 frames × 20 ms = 120 ms.

        Used for both select and deselect; the gen token aborts any
        in-flight animation when a newer _redraw has taken over.
        """
        if gen != self._anim_gen:
            return
        STEPS = 6
        eased  = 1 - (1 - min(step, STEPS) / STEPS) ** 2
        colour = _lerp_color(c0, c1, eased)
        try:
            self._cv.itemconfig(poly, outline=colour)
        except tk.TclError:
            return
        if step < STEPS:
            self._cv.after(20, lambda: self._animate_border(step + 1, gen, poly, c0, c1))

    @property
    def widget(self) -> tk.Canvas:
        return self._cv

# ──────────────────────────────────────────────────────────────────────────────
# Toggle switch widget
# ──────────────────────────────────────────────────────────────────────────────

class _Toggle:
    TW, TH  = 36, 14   # narrow, flat — instrument panel, not consumer app
    _STEPS  = 7
    _FRAME  = 16        # 7 × 16ms ≈ 112ms — crisp, not sluggish

    def __init__(self, parent: tk.Widget, variable: tk.BooleanVar) -> None:
        self._var      = variable
        self._cv       = tk.Canvas(parent, width=self.TW, height=self.TH,
                                   bg=_C_BG, highlightthickness=0, cursor="hand2")
        self._cv.bind("<Button-1>", lambda e: self._var.set(not self._var.get()))
        self._anim_gen = 0
        on = bool(variable.get())
        ks = self.TH - 4
        self._knob_x   = self.TW - 2 - ks if on else 2   # current knob x
        variable.trace_add("write", self._on_change)
        self._draw(on, self._knob_x)

    def _on_change(self, *_) -> None:
        on  = bool(self._var.get())
        ks  = self.TH - 4
        x0  = self._knob_x                           # start from current position
        x1  = self.TW - 2 - ks if on else 2          # travel to new position
        self._anim_gen += 1
        gen = self._anim_gen
        self._cv.after(0, lambda: self._animate(1, gen, on, x0, x1))

    def _animate(self, step: int, gen: int, on: bool, x0: int, x1: int) -> None:
        if gen != self._anim_gen:
            return
        t            = min(step, self._STEPS) / self._STEPS
        eased        = t * t                          # ease-in — slow start, confident finish
        kx           = round(x0 + (x1 - x0) * eased)
        self._knob_x = kx
        self._draw(on, kx)
        if step < self._STEPS:
            self._cv.after(self._FRAME, lambda: self._animate(step + 1, gen, on, x0, x1))

    def _draw(self, on: bool, kx: int) -> None:
        w, h = self.TW, self.TH
        self._cv.delete("all")
        pts = _rounded_poly(0, 0, w, h, 3)
        self._cv.create_polygon(pts, smooth=True,
                                fill="#172A1E" if on else _C_BG,
                                outline=_C_ACCENT if on else _C_BORDER, width=1)
        ks = h - 4
        self._cv.create_oval(kx, 2, kx + ks, 2 + ks,
                             fill=_C_ACCENT if on else _C_SUB, outline="")

    @property
    def widget(self) -> tk.Canvas:
        return self._cv

# ──────────────────────────────────────────────────────────────────────────────
# Foreground helpers
# ──────────────────────────────────────────────────────────────────────────────

def _show_in_taskbar() -> None:
    """Bring the app frontmost so a borderless dialog is visible and clickable.

    A menu-bar-only (accessory) app is not the active application, so newly
    created windows can open behind whatever the user was using. Activating
    the shared NSApplication pulls the dialog to the foreground.
    """
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass

def _hide_from_taskbar() -> None:
    """No-op on macOS — dialogs are transient Toplevels; nothing to unmap."""
    pass

# ──────────────────────────────────────────────────────────────────────────────
# Shared dialog chrome
# ──────────────────────────────────────────────────────────────────────────────

def _make_dialog(W: int, H: int) -> tuple[tk.Toplevel, tk.Frame]:
    """
    Create a borderless Toplevel centred on screen.
    Returns (window, shell) where shell is the content frame.
    Uses the single persistent _tk_root as parent — no new Tk() ever created.
    """
    dlg = tk.Toplevel(_tk_root)
    dlg.overrideredirect(True)
    dlg.attributes("-topmost", True)
    dlg.configure(bg=_C_MUTED)           # 1 px warm outer border
    dlg.update_idletasks()
    sw = dlg.winfo_screenwidth()
    sh = dlg.winfo_screenheight()
    dlg.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}")
    shell = tk.Frame(dlg, bg=_C_BG)
    shell.place(x=1, y=1, width=W - 2, height=H - 2)
    tk.Frame(shell, bg=_C_ACCENT).place(x=0, y=0, width=W - 2, height=1)
    return dlg, shell


def _attach_drag(dlg: tk.Toplevel, *widgets: tk.Widget) -> None:
    def _ds(e: tk.Event) -> None:
        dlg._dx = e.x_root - dlg.winfo_x()   # type: ignore[attr-defined]
        dlg._dy = e.y_root - dlg.winfo_y()   # type: ignore[attr-defined]
    def _dm(e: tk.Event) -> None:
        dlg.geometry(f"+{e.x_root - dlg._dx}+{e.y_root - dlg._dy}")  # type: ignore[attr-defined]
    for w in widgets:
        w.bind("<Button-1>", _ds)
        w.bind("<B1-Motion>", _dm)


def _place_close(shell: tk.Frame, W: int, on_close: "callable") -> None:
    SZ = 18
    cv = tk.Canvas(shell, width=SZ, height=SZ,
                   bg=_C_BG, highlightthickness=0, cursor="hand2")
    cv.place(x=W - 32, y=12)   # share header baseline with ZOOTED label (y=12)

    def _draw(color: str) -> None:
        cv.delete("all")
        p = 4
        cv.create_line(p, p, SZ - p, SZ - p, fill=color, width=1)
        cv.create_line(SZ - p, p, p, SZ - p, fill=color, width=1)

    _draw(_C_SUB)
    cv.bind("<Button-1>", lambda e: on_close())
    cv.bind("<Enter>",    lambda e: _draw(_C_TEXT))
    cv.bind("<Leave>",    lambda e: _draw(_C_SUB))


def _place_pill_btn(shell: tk.Frame, W: int, y: int, text: str,
                    command: "callable", btn_w: int = 0, h: int = 46,
                    fill: str = "", fill_hover: str = "",
                    border: str = "") -> None:
    if btn_w == 0:
        btn_w = W - 60
    _fill = fill or _C_ACCENT
    _fhov = fill_hover or _C_ACCENT2
    cv = tk.Canvas(shell, width=btn_w, height=h,
                   bg=_C_BG, highlightthickness=0, cursor="hand2")
    cv.place(x=(W - btn_w) // 2, y=y)
    rect, _, hl_id, shd_id = _draw_pill_btn(cv, btn_w, h, fill=_fill, border=border,
                                            text=text, text_fill=_C_TEXT,
                                            font=_F_BTN)

    def _apply(fill_color: str) -> None:
        cv.itemconfig(rect, fill=fill_color)
        if hl_id is not None:
            cv.itemconfig(hl_id,  fill=_lerp_color(fill_color, "#FFFFFF", 0.14))
            cv.itemconfig(shd_id, fill=_lerp_color(fill_color, "#000000", 0.20))

    cv.bind("<Button-1>", lambda e: command())
    cv.bind("<Enter>",    lambda e: _apply(_fhov))
    cv.bind("<Leave>",    lambda e: _apply(_fill))

# ──────────────────────────────────────────────────────────────────────────────
# Duration / Timer dialog
# ──────────────────────────────────────────────────────────────────────────────

def _show_duration_dialog(
    title: str,
    current: int | None,
    on_save: "callable[[int | None], None]",
    _quit_after: bool = False,
    status_str: str | None = None,
) -> None:
    """
    Show the timer duration picker as a Toplevel.
    _quit_after=True is used for first-time setup to exit the bootstrap mainloop.
    """
    from PIL import ImageTk

    W = 380
    CARD_H, CARD_GAP = 60, 10
    GRID_X  = 24
    CARD_W  = (W - 2 * GRID_X - CARD_GAP) // 2
    GRID_ROWS = math.ceil(len(DURATION_OPTIONS) / 2)
    GRID_H    = GRID_ROWS * CARD_H + (GRID_ROWS - 1) * CARD_GAP

    logo_pil        = _load_logo_image(target_w=175)
    LOGO_W, LOGO_H_px = logo_pil.size

    LOGO_Y    = 16
    TITLE_Y   = LOGO_Y + LOGO_H_px + 12   # portrait→title breathing room (no divider rule)
    TAGLINE_Y = TITLE_Y + 22
    RULE_Y    = TAGLINE_Y + 22
    GRID_Y    = RULE_Y + 14
    CONFIRM_Y = GRID_Y + GRID_H + 20
    CONFIRM_H = 42
    CANCEL_Y  = CONFIRM_Y + CONFIRM_H + 8
    VERSION_Y = CANCEL_Y + 18
    H         = VERSION_Y + 18

    _show_in_taskbar()

    dlg, shell = _make_dialog(W, H)
    _attach_drag(dlg, shell)

    def _close() -> None:
        _hide_from_taskbar()
        dlg.destroy()
        if _quit_after:
            _tk_root.quit()

    _place_close(shell, W, _close)

    # Logo — portrait centre. No divider rule: portrait already reads as a unit
    # with the title; an extra rule here crowded the title baseline.
    logo_img = ImageTk.PhotoImage(logo_pil)
    dlg._logo_ref = logo_img                        # type: ignore[attr-defined]
    logo_lbl = tk.Label(shell, image=logo_img, bg=_C_BG, bd=0)
    logo_lbl.place(x=(W - LOGO_W) // 2, y=LOGO_Y)
    _attach_drag(dlg, logo_lbl)

    tk.Label(shell, text="ZOOTED", bg=_C_BG, fg=_C_TEXT,
             font=_F_TITLE,
             ).place(x=0, y=TITLE_Y, width=W - 2)

    tk.Label(shell, text="quiet. persistent. present.",
             bg=_C_BG, fg=_C_SUB, font=_ff(_F_DESC),
             ).place(x=0, y=TAGLINE_Y, width=W - 2)

    tk.Frame(shell, bg=_C_BORDER, height=1).place(
        x=GRID_X, y=RULE_Y, width=W - 2 * GRID_X, height=1)

    init_val = 0 if current is None else current
    var = tk.IntVar(value=init_val)

    for i, (lbl, sub, mins) in enumerate(DURATION_OPTIONS):
        col, row = i % 2, i // 2
        val = 0 if mins is None else mins
        card = _DurationCard(shell, lbl, sub, var, val, CARD_W, CARD_H)
        card.widget.place(x=GRID_X + col * (CARD_W + CARD_GAP),
                          y=GRID_Y  + row * (CARD_H + CARD_GAP))

    def _confirm() -> None:
        v      = var.get()
        result = None if v == 0 else v
        _hide_from_taskbar()
        dlg.destroy()
        if _quit_after:
            _tk_root.quit()
        on_save(result)

    _place_pill_btn(shell, W, CONFIRM_Y, "CONFIRM", _confirm,
                    btn_w=W - 48, h=CONFIRM_H,
                    fill=_C_CTA, fill_hover=_C_CTA_H, border=_C_CTA_B)

    cancel_lbl = tk.Label(shell, text="cancel", bg=_C_BG, fg=_C_MUTED,
                          font=_ff(_F_DESC), cursor="hand2")
    cancel_lbl.place(x=0, y=CANCEL_Y, width=W, anchor="nw")
    cancel_lbl.bind("<Button-1>", lambda e: _close())
    cancel_lbl.bind("<Enter>",    lambda e: cancel_lbl.config(fg=_C_TEXT))
    cancel_lbl.bind("<Leave>",    lambda e: cancel_lbl.config(fg=_C_MUTED))

    if status_str:
        # Canvas dot + status text — centered as a unit
        DOT  = 4
        ROW_H_ST = 12
        sf = tk.Frame(shell, bg=_C_BG)
        dot_cv = tk.Canvas(sf, width=DOT + 8, height=ROW_H_ST,
                           bg=_C_BG, highlightthickness=0)
        dot_cv.create_oval(4, (ROW_H_ST - DOT) // 2,
                           4 + DOT, (ROW_H_ST + DOT) // 2,
                           fill=_C_ACCENT, outline="")
        dot_cv.pack(side="left")
        tk.Label(sf, text=status_str, bg=_C_BG, fg=_C_ACCENT,
                 font=_ff(_F_DESC)).pack(side="left")
        sf.place(relx=0.5, y=VERSION_Y, anchor="n")
    else:
        tk.Label(shell, text=f"zooted v{__version__}", bg=_C_BG, fg=_C_MUTED,
                 font=_ff(_F_HINT),
                 ).place(x=0, y=VERSION_Y, width=W - 2)

    dlg.focus_force()

# ──────────────────────────────────────────────────────────────────────────────
# Settings dialog
# ──────────────────────────────────────────────────────────────────────────────

def _show_settings_dialog(
    current_settings: dict,
    on_save: "callable[[dict], None]",
) -> None:
    W        = 380
    ROW_H    = 56
    HEADER_H = 46
    ROWS_Y   = HEADER_H + 6
    SAVE_Y   = ROWS_Y + len(SETTINGS_META) * ROW_H + 14
    SAVE_H   = 40
    CANCEL_Y = SAVE_Y + SAVE_H + 10
    H        = CANCEL_Y + 36

    dlg, shell = _make_dialog(W, H)
    _attach_drag(dlg, shell)
    _place_close(shell, W, dlg.destroy)

    tk.Label(shell, text="ZOOTED", bg=_C_BG, fg=_C_TEXT,
             font=_F_TITLE).place(x=14, y=12)
    tk.Label(shell, text="settings", bg=_C_BG, fg=_C_SUB,
             font=_ff(_F_DESC)).place(x=78, y=19)
    tk.Frame(shell, bg=_C_BORDER, height=1).place(x=0, y=HEADER_H,
                                                   width=W, height=1)

    vars_: dict[str, tk.BooleanVar] = {}

    for i, (key, label, desc) in enumerate(SETTINGS_META):
        y       = ROWS_Y + i * ROW_H
        val     = _get_startup() if key == "launch_at_startup" \
                  else current_settings.get(key, SETTINGS_DEFAULTS[key])
        bv      = tk.BooleanVar(value=bool(val))
        vars_[key] = bv

        # One consistent surface for all rows — instrument-panel cohesion,
        # not a data table. Dividers between rows carry the structure.
        row_frame  = tk.Frame(shell, bg=_C_BG, height=ROW_H)
        row_frame.place(x=0, y=y, width=W, height=ROW_H)

        tk.Label(row_frame, text=label, bg=_C_BG, fg=_C_TEXT,
                 font=_ff(_F_ROW), anchor="w",
                 ).place(x=20, y=10)
        tk.Label(row_frame, text=desc, bg=_C_BG, fg=_C_SUB,
                 font=_ff(_F_DESC), anchor="w",
                 ).place(x=20, y=30)

        toggle = _Toggle(row_frame, bv)
        toggle.widget.place(x=W - _Toggle.TW - 20,
                            y=(ROW_H - _Toggle.TH) // 2)

        if i < len(SETTINGS_META) - 1:
            tk.Frame(shell, bg=_C_BORDER, height=1).place(
                x=20, y=y + ROW_H, width=W - 40, height=1)

    def _save() -> None:
        result = {k: v.get() for k, v in vars_.items()}
        dlg.destroy()
        on_save(result)

    _place_pill_btn(shell, W, SAVE_Y, "SAVE SETTINGS", _save,
                    btn_w=W - 48, h=SAVE_H)

    cancel_lbl = tk.Label(shell, text="cancel", bg=_C_BG, fg=_C_MUTED,
                          font=_ff(_F_DESC), cursor="hand2")
    cancel_lbl.place(x=0, y=CANCEL_Y, width=W, anchor="nw")
    cancel_lbl.bind("<Button-1>", lambda e: dlg.destroy())
    cancel_lbl.bind("<Enter>",    lambda e: cancel_lbl.config(fg=_C_TEXT))
    cancel_lbl.bind("<Leave>",    lambda e: cancel_lbl.config(fg=_C_MUTED))

    dlg.focus_force()

# ──────────────────────────────────────────────────────────────────────────────
# Core application
# ──────────────────────────────────────────────────────────────────────────────

class ZootedApp:
    def __init__(self, config: dict, stop_event: threading.Event):
        self.default_duration: int | None = config.get("default_duration_minutes", 60)
        self.settings: dict = {k: config.get(k, v)
                               for k, v in SETTINGS_DEFAULTS.items()}
        self._stop_event = stop_event
        self._active     = False
        self._end_time: float | None = None
        self._notified_5min = False
        self._lock = threading.Lock()
        self._icon: pystray.Icon | None = None

    def _status_label(self) -> str:
        if not self._active:
            return "Inactive"
        if self._end_time is None:
            return "Active — Indefinite"
        secs = self._end_time - time.monotonic()
        if secs <= 0:
            return "Inactive"
        m = int(secs // 60)
        if m >= 60:
            h, rm = divmod(m, 60)
            return f"Active — {h}h {rm}m remaining"
        return f"Active — {m}m {int(secs % 60)}s remaining"

    def _activate(self) -> None:
        dur = self.default_duration
        logging.info("ACTIVATE: duration=%s minutes", dur)
        with self._lock:
            self._active        = True
            self._notified_5min = False
            self._end_time      = (None if dur is None
                                   else time.monotonic() + dur * 60)
        _set_sleep_prevention(True, self.settings.get("keep_display_on", True))
        self._refresh_icon()

    def _deactivate(self) -> None:
        logging.info("DEACTIVATE: releasing sleep prevention")
        with self._lock:
            self._active        = False
            self._end_time      = None
            self._notified_5min = False
        _set_sleep_prevention(False)
        self._refresh_icon()

    def toggle(self, icon=None, item=None) -> None:
        logging.info("TOGGLE: manual toggle requested (currently active=%s)", self._active)
        if self._active:
            self._deactivate()
        else:
            self._activate()

    def _refresh_icon(self) -> None:
        ic = self._icon
        if ic is None:
            return
        ic.icon = _make_icon_image(self._active)
        ic.title = f"Zooted — {self._status_label()}"
        try:
            ic.update_menu()
        except Exception:
            pass

    def _quick_activate(self, minutes: int | None) -> None:
        """Set duration and activate immediately from tray submenu."""
        self.default_duration = minutes
        _save_config_partial(default_duration_minutes=minutes)
        self._activate()

    def _build_menu(self) -> pystray.Menu:
        def _action(m):
            return lambda icon, item: self._quick_activate(m)

        def _checked(m):
            return lambda item: self.default_duration == m

        duration_items = []
        for label, sub, mins in DURATION_OPTIONS:
            duration_items.append(
                pystray.MenuItem(
                    f"{label}  —  {sub}",
                    _action(mins),
                    checked=_checked(mins),
                    radio=True,
                )
            )
        duration_items.append(pystray.Menu.SEPARATOR)
        duration_items.append(
            pystray.MenuItem("Custom…", self._request_timer)
        )

        return pystray.Menu(
            pystray.MenuItem(lambda _: self._status_label(),
                             action=None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda _: "Deactivate" if self._active else "Activate",
                self.toggle, default=True),
            pystray.MenuItem("Timer", pystray.Menu(*duration_items)),
            pystray.MenuItem("Settings", self._request_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._exit),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Zooted v{__version__}", action=None, enabled=False),
        )

    # Menu callbacks post onto the Tk thread via after() — no queue needed
    def _request_timer(self, icon=None, item=None) -> None:
        status = self._status_label() if self._active else None
        _tk_root.after(0, lambda: _show_duration_dialog(
            "Zooted — Timer",
            self.default_duration,
            self._on_prefs_saved,
            status_str=status,
        ))

    def _on_prefs_saved(self, minutes: int | None) -> None:
        self.default_duration = minutes
        _save_config_partial(default_duration_minutes=minutes)

    def _request_settings(self, icon=None, item=None) -> None:
        _tk_root.after(0, lambda: _show_settings_dialog(
            self.settings,
            self._on_settings_saved,
        ))

    def _on_settings_saved(self, new_settings: dict) -> None:
        old = self.settings.copy()
        self.settings.update(new_settings)
        _save_config_partial(**new_settings)
        _set_startup(new_settings.get("launch_at_startup", False))
        if self._active and \
                new_settings.get("keep_display_on") != old.get("keep_display_on"):
            _set_sleep_prevention(True, new_settings.get("keep_display_on", True))

    def _timer_loop(self) -> None:
        tick = 0
        logging.info("Timer loop started")
        while not self._stop_event.is_set():
            try:
                time.sleep(1)
                tick += 1
                if not self._active:
                    if tick % 30 == 0:
                        # Re-assert release while inactive so a stray caffeinate
                        # child never lingers holding the wake-lock.
                        _set_sleep_prevention(False)
                        self._refresh_icon()
                    continue
                if tick % 30 == 0:
                    _set_sleep_prevention(True, self.settings.get("keep_display_on", True))
                end = self._end_time
                if end is None:
                    if tick % 60 == 0:
                        self._refresh_icon()
                    continue
                remaining = end - time.monotonic()
                if remaining <= 300 and not self._notified_5min:
                    self._notified_5min = True
                    if self.settings.get("show_notifications", True):
                        _notify("Zooted",
                                "5 minutes remaining before your PC can sleep.")
                if remaining <= 0:
                    logging.info(
                        "Timer expired at tick=%d (duration was %s min)",
                        tick, self.default_duration,
                    )
                    if self.settings.get("loop_on_expiry", False):
                        self._activate()
                    else:
                        self._deactivate()
                        if self.settings.get("show_notifications", True):
                            _notify("Zooted",
                                    "Zooted has deactivated. "
                                    "Click the tray icon to reactivate.")
                    continue
                self._refresh_icon()
            except Exception:
                logging.exception("Unhandled exception in timer loop at tick=%d", tick)
        logging.info("Timer loop exited (stop_event set)")

    def _exit(self, icon=None, item=None) -> None:
        logging.info("EXIT: user requested exit")
        self._deactivate()
        self._stop_event.set()
        if self._icon:
            self._icon.stop()
        _tk_root.after(0, _tk_root.quit)

    def run_tray(self) -> None:
        self._icon = pystray.Icon(
            name="Zooted",
            icon=_make_icon_image(self._active),
            title=f"Zooted — {self._status_label()}",
            menu=self._build_menu(),
        )
        self._icon.run_detached()

# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    """Configure rotating file logging to ~/Library/Application Support/Zooted/zooted.log (cap 1 MB)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=1_000_000,   # 1 MB
        backupCount=1,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(handler)


def main() -> None:
    global _tk_root, _wake_lock_queue

    if not _acquire_instance_lock():
        sys.exit(0)

    _setup_logging()
    logging.info("Zooted starting up")

    # Start the dedicated wake-lock thread before anything else
    _wake_lock_queue = queue.SimpleQueue()
    t = threading.Thread(target=_wake_lock_worker, daemon=True, name="WakeLock")
    t.start()

    # Create the ONE persistent Tk root — hidden, never destroyed
    _tk_root = tk.Tk()
    _tk_root.title("Zooted")
    _tk_root.withdraw()
    _init_fonts()
    # Set window icon — iconphoto with full-res PNG scales down cleanly
    try:
        from PIL import ImageTk
        _src = _get_resource("icon_v2.png")
        if _src.exists():
            _icon_img = Image.open(str(_src)).convert("RGBA")
            _icon_photo = ImageTk.PhotoImage(_icon_img)
            _tk_root.iconphoto(True, _icon_photo)
            _tk_root._icon_photo_ref = _icon_photo  # prevent GC
    except Exception:
        pass

    config = _load_config()
    base_settings = config if config is not None else {}
    logging.info("Config loaded: %s", json.dumps(base_settings) if base_settings else "none (first run)")

    show_picker = base_settings.get("show_duration_on_startup", True)

    if show_picker:
        # ── Show launch dialog — user picks duration ───────────────────────────
        chosen: list = [_SENTINEL]

        def _store(m: int | None) -> None:
            chosen[0] = m

        default_dur = base_settings.get("default_duration_minutes", 60)
        _tk_root.after(0, lambda: _show_duration_dialog(
            "Zooted", default_dur, _store, _quit_after=True
        ))
        _tk_root.mainloop()   # exits when dialog closes (confirm or X)

        if chosen[0] is _SENTINEL:
            sys.exit(0)       # user dismissed without confirming

        chosen_duration = chosen[0]
    else:
        # ── Skip picker — go straight to tray with 1-hour default ─────────────
        chosen_duration = base_settings.get("default_duration_minutes", 60)
        logging.info("Startup picker skipped; using duration=%s minutes", chosen_duration)

    # Merge chosen duration into config and persist
    if config is None:
        config = {**SETTINGS_DEFAULTS}
    config["default_duration_minutes"] = chosen_duration
    _save_config_partial(**config)

    # ── Full app init ─────────────────────────────────────────────────────────
    stop_event = threading.Event()
    app = ZootedApp(config, stop_event)
    app._activate()       # always activate — user just confirmed a duration

    app.run_tray()
    threading.Thread(target=app._timer_loop, daemon=True).start()

    # ── Main event loop — processes Timer / Settings dialogs via after() ──────
    _tk_root.mainloop()


if __name__ == "__main__":
    main()
