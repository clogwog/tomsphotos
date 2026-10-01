"""Clipboard helpers: export images that keep the app's on-screen vibrancy.

The app renders cached images that carry no colour profile, so on a wide-gamut
screen the raw values are shown in the screen's colour space (Display P3 on the
Macs this app targets) — which is why the grid and detail view look rich.

Copying a raw QPixmap leaves paste targets to assume/convert sRGB, which mutes
the image, and services like Twitter additionally colour-manage any embedded
profile down to sRGB. Interpreting the pixels as the screen colour space and
baking them into sRGB reproduces the same appearance even after the target
normalises to sRGB.
"""

from PySide6.QtGui import QColorSpace, QGuiApplication


def _screen_color_space():
    """Best-effort screen colour space.

    Qt does not expose QScreen::colorSpace() on all builds (notably 6.11), so
    fall back to Display P3, which is the profile of the wide-gamut Macs this
    app targets and of the iPhone/camera sources it browses.
    """
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        getter = getattr(screen, "colorSpace", None)
        if callable(getter):
            cs = getter()
            if cs is not None and cs.isValid():
                return cs
    return QColorSpace(QColorSpace.NamedColorSpace.DisplayP3)


def image_for_clipboard(pixmap):
    """Return an sRGB QImage whose appearance matches the app's screen render.

    The screen-space values are converted down to sRGB so the look survives a
    target that assumes or colour-manages sRGB (browser paste, Twitter upload).
    """
    img = pixmap.toImage()
    srgb = QColorSpace(QColorSpace.NamedColorSpace.SRgb)
    screen_cs = _screen_color_space()
    if screen_cs is None or not screen_cs.isValid():
        img.setColorSpace(srgb)
        return img
    img.setColorSpace(screen_cs)
    converted = img.convertedToColorSpace(srgb)
    if converted is None or converted.isNull():
        img.setColorSpace(srgb)
        return img
    return converted
