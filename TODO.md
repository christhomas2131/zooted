# GLM Autopolish — Frontend Pass

Scope: visual layer of `zooted.py` only. No business logic, no config, no deps.
Gate: `python3 -m py_compile zooted.py` (Windows-only modules can't import on macOS).
Design direction: existing "instrument panel" — warm near-black, restrained, deliberate motion. Stay inside it.

## TODO (ordered by impact)

- [x] **1. Declutter duration dialog: drop the redundant logo-baseline rule**
      The portrait already reads as a unit with the title — the 1px `_C_BORDER` rule placed
      3px below the portrait crowds the title (sits only 5px above "ZOOTED").
      Keep the single rule under the tagline; remove the rule directly under the logo.
      Accept: only one horizontal rule remains in the duration dialog header; portrait-to-title spacing is at least 10px; py_compile passes.

- [x] **2. Replace settings zebra striping with a single consistent surface + thin dividers**
      Alternating `_C_CARD`/`_C_BG` reads like a data table, not an instrument panel.
      Use one surface tone for all rows, separated by 1px dividers in `_C_BORDER`.
      Accept: every settings row uses the same bg; dividers exist between rows; py_compile passes.

- [x] **3. Unify the "ZOOTED" title size across dialogs**
      Duration dialog uses `("Consolas", 14, "")`; settings uses `("Consolas", 11, "")`.
      Pick one (14 — it anchors the duration dialog, which is the primary surface).
      Accept: every "ZOOTED" header label uses the same Consolas size/weight; py_compile passes.

- [x] **4. Use `__version__` for the duration dialog footer instead of hardcoded "zooted v1.0"**
      Tray menu already says `Zooted v{__version__}` (1.0.0); footer says "zooted v1.0".
      Replace the literal with `zooted v{__version__}` so they stay in sync.
      Accept: footer string references `__version__`; no other "v1.0" literal remains; py_compile passes.

- [x] **5. Standardize primary CTA width across dialogs**
      Duration "CONFIRM" uses `btn_w = W - 48` (full-width minus 24px side padding).
      Settings "SAVE SETTINGS" uses `btn_w = 220` (centered, leaving ~80px gutter each side).
      Make settings use the same full-width-minus-padding pattern.
      Accept: both primary CTAs use `W - 48` width with `x=24`; py_compile passes.

- [x] **6. Vertically align close button with the header label**
      Close sits at `y=13`; settings header label sits at `y=14`. 1px misalignment.
      Move close to `y=14` to share the header baseline.
      Accept: close glyph and header label share the same `y` origin; py_compile passes.

- [x] **7. Symmetrize settings horizontal padding**
      Row text starts at `x=16`; toggle sits at `W - TW - 20` (20px from right edge).
      Use 20px on both sides so left/right gutters match and the panel reads balanced.
      Accept: text starts at `x=20`; toggle right edge is 20px from the dialog right edge; py_compile passes.

- [x] **8. Refresh pill button catch-light / shadow on hover**
      `_draw_pill_btn` computes hl/shd lines from the static `_fill`; on hover only the
      polygon fill swaps to `_fhov`, leaving the directional lines keyed to the old tone.
      Track the line item ids and recompute their colour on Enter/Leave.
      Accept: hover updates polygon fill AND both highlight/shadow lines; py_compile passes.

- [ ] **9. Add symmetric deselect animation to duration cards**
      Selecting a card fades border `_C_BORDER → _C_ACCENT`; clicking another card snaps the
      previously-selected card's border straight back to `_C_BORDER`. Mirror the fade.
      Accept: moving selection from A to B fades A's border out over the same 6×20ms as B fades in; py_compile passes.

- [ ] **10. Introduce a small named type scale and replace magic font tuples**
       Sizes in use: 7, 8, 8, 8, 9, 10, 11, 14. Define `F_TITLE`, `F_BODY`, `F_DESC`,
       `F_META`, `F_HINT`, `F_MONO_LABEL` and use them in both dialogs.
       Accept: no raw `(family, size, ...)` literals remain in dialog code; py_compile passes.

## Out of scope (noted, not attempted)
- Cannot render tkinter on macOS / cannot run the actual dialogs — visual verification is by reasoning only.
- Pillow-based icon rendering (`_render_face_icon`, `_build_logo_pil`) — already carefully tuned, not a dialog-surface polish.
- Tray menu cosmetics — Windows-only native menu, noTk styling hooks.
