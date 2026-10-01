# Spec: clipboard copy keeps app's on-screen vibrancy

Status: implemented
Scope: `fullview.py`, `gallerypanel.py`, `clipboard_util.py`, `tests/test_gui.py`

## Problem

Grid thumbnails and the detail view look rich; copied images pasted into
Twitter/web look muted.

## Diagnosis (measured)

- Source/cache images carry no colour profile. On the wide-gamut (Display P3)
  Mac the app shows the raw values in screen space, so they look rich.
- Copying the raw pixmap leaves paste targets to assume sRGB (muted).
- First attempt: tag the clipboard image Display P3. Verified via `NSPasteboard`
  that `public.tiff` is tagged Display P3 — but pastes stayed muted. Twitter
  colour-manages the P3 tag down to sRGB, so the result matches the untagged
  sRGB render.
- Conclusion: tagging cannot survive a target that normalises to sRGB. The
  appearance must be **baked into sRGB pixel values**.

Measured on the user's screenshot (same screen, same image): app region mean
saturation 0.60 vs pasted 0.55.

## Decision

Convert the clipboard image from the screen colour space (Display P3) down to
sRGB before copying. This bakes the wide-gamut look into sRGB numbers, so:

- targets that assume sRGB (ICC-ignoring) render it correctly;
- targets that colour-manage sRGB (Twitter) pass it through unchanged;
- the paste matches the app's on-screen appearance.

Example: `(200, 60, 30)` in P3 becomes `(217, 42, 0)` in sRGB — more saturated,
which is exactly the app's unmanaged look.

## Implementation

`clipboard_util.py`:
- `_screen_color_space()`: use `QScreen.colorSpace()` when available; otherwise
  Display P3 (Qt 6.11 does not expose `QScreen::colorSpace()`; the target Macs
  and iPhone sources are Display P3).
- `image_for_clipboard(pixmap)`: `toImage()`, set the source colour space to the
  screen space, `convertedToColorSpace(sRGB)`; guard null/invalid and fall back
  to an sRGB tag.

Callers:
- `fullview.py:459-467` `_copy_image()` → `setImage(image_for_clipboard(pixmap))`.
- `gallerypanel.py:212-224` `_copy_selected()` → same.

Display pipeline untouched: `thumbgen.py`, `thumbstore.py`, `THUMB_VERSION`.

## Tests

`tests/test_gui.py`:
- `test_clipboard_image_bakes_to_srgb`: a saturated colour comes out sRGB-tagged
  and strictly more saturated (red up, blue down).
- `test_copy_image_sets_clipboard`: clipboard image non-null after copy.

## Verification

Real pasteboard probe (macOS, PyObjC): after copy, `public.tiff` is tagged
`sRGB` and pixels are baked (`(217, 42, 0)` from `(200, 60, 30)`).

Manual: restart the app, copy from detail view, paste into Twitter compose —
must match the detail view's vibrancy.

## Risks

- Fallback assumes Display P3 when Qt can't report the screen profile; on a
  non-P3 display this could over-saturate. Acceptable for the target Macs.
- Conversion is per-copy, once, on an already-decoded pixmap; negligible cost.
