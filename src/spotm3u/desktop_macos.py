"""macOS-specific window chrome for the frameless SpotM3U window.

pywebview's frameless Cocoa window gets a transparent, hidden title bar and a
full-size content view, so the web content already extends under the title-bar
area. The one thing frameless mode does not keep is the window's traffic
lights: pywebview hides the native close/minimize/zoom buttons and expects the
page to draw its own controls (which is what Windows and Linux do here).

On macOS SpotM3U instead puts the native controls back and lets AppKit own
close, minimize, fullscreen/zoom, hover states, accessibility and the native
fullscreen traffic-light reveal behaviour. The page only contributes a
transparent drag strip (see ``templates/_titlebar.html``), so nothing on the
page needs to fake a macOS traffic light.

The one wrinkle is timing: pywebview only builds the native NSWindow once the
GUI loop starts, so ``window.native`` is ``None`` before that. The chrome is
therefore applied from a callback handed to ``webview.start(func=...)``, which
waits for the window to exist and then schedules the AppKit changes on the main
thread. AppKit is imported lazily and the entry point is a no-op on any
platform that is not macOS, so the module stays importable and headless-safe.
"""

from __future__ import annotations

import sys
import typing

if typing.TYPE_CHECKING:
    from webview import Window

# Cached AppHelper.callAfter (schedules on AppKit's main thread); False once the
# helper is known to be unavailable, None before it has been looked up.
_call_after = None


def configure_native_chrome(window: Window) -> bool:
    """Re-enable the native macOS traffic lights on a frameless window.

    ``window`` is the ``webview.Window`` returned by ``create_window``; the
    underlying AppKit ``NSWindow`` only appears (as ``window.native``) after the
    GUI loop has begun, so this waits for it first. Returns ``True`` when the
    window was configured and ``False`` on a no-op (not macOS, or no native
    window to touch).
    """
    if sys.platform != "darwin":
        return False
    _wait_for_native_window(window)
    native = getattr(window, "native", None)
    if native is None:
        return False
    try:
        import AppKit  # type: ignore[import-not-found]  # provided by the pywebview stack
    except ImportError:  # pragma: no cover - macOS only, where pywebview pulls pyobjc in
        return False

    schedule = _call_after_dispatcher()
    if schedule is not None:
        # NSWindow must only be touched on AppKit's main thread; the block
        # runs when the run loop picks it up, right after the window shows.
        schedule(_reconfigure_ns_window, native, AppKit)
    else:
        _reconfigure_ns_window(native, AppKit)
    return True


def _wait_for_native_window(window: Window) -> None:
    # cocoa raises events.before_show at the end of window construction, so by
    # the time it is set, window.native (the NSWindow) exists. Allow for stubs
    # and for the event being absent on non-NSWindow builds.
    events = getattr(window, "events", None)
    before_show = getattr(events, "before_show", None) if events is not None else None
    if before_show is not None:
        before_show.wait(10)


def _call_after_dispatcher():
    """Return AppHelper.callAfter (schedule on AppKit's main thread) or None."""
    global _call_after
    if _call_after is None:
        try:
            from PyObjCTools import AppHelper  # type: ignore[import-not-found]
        except ImportError:
            _call_after = False
        else:
            _call_after = AppHelper.callAfter
    return _call_after if _call_after else None


def _reconfigure_ns_window(ns_window, AppKit) -> bool:
    """Turn the frameless NSWindow into a native-chrome macOS window.

    Only AppKit calls are made here; the changes mirror what normal macOS
    windowed apps get by default:
      * the native close, minimize and fullscreen/zoom controls come back,
      * the (already transparent) title bar stays hidden and the content view
        keeps running full size underneath it,
      * the green control is a full-screen zoom, so fullscreen transitions
        (traffic lights hide, then auto-reveal at the top edge) are native.
    """
    for button_tag in (
        AppKit.NSWindowCloseButton,
        AppKit.NSWindowMiniaturizeButton,
        AppKit.NSWindowZoomButton,
    ):
        button = ns_window.standardWindowButton_(button_tag)
        if button is not None:
            button.setHidden_(False)

    ns_window.setTitlebarAppearsTransparent_(True)
    ns_window.setTitleVisibility_(AppKit.NSWindowTitleHidden)

    behavior = ns_window.collectionBehavior() | AppKit.NSWindowCollectionBehaviorFullScreenPrimary
    ns_window.setCollectionBehavior_(behavior)
    return True
