"""Tests for the macOS-native window chrome (desktop_macos)."""

import sys
from types import SimpleNamespace

from spotm3u import desktop_macos


class _FakeNSButton:
    def __init__(self, window) -> None:
        self.window = window
        self.hidden = True

    def setHidden_(self, value) -> None:
        self.hidden = bool(value)
        self.window.calls.append(("button.hidden", value))


class _FakeNSWindow:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.buttons: dict[int, _FakeNSButton] = {}
        self.titlebar_transparent = False
        self.title_visibility = None
        self.behavior = 0

    def standardWindowButton_(self, tag):
        self.calls.append(("standardWindowButton", tag))
        button = self.buttons.setdefault(tag, _FakeNSButton(self))
        return button

    def setTitlebarAppearsTransparent_(self, value) -> None:
        self.calls.append(("titlebarTransparent", value))
        self.titlebar_transparent = bool(value)

    def setTitleVisibility_(self, value) -> None:
        self.calls.append(("titleVisibility", value))
        self.title_visibility = value

    def collectionBehavior(self) -> int:
        self.calls.append(("collectionBehavior",))
        return self.behavior

    def setCollectionBehavior_(self, value) -> None:
        self.calls.append(("collectionBehavior", value))
        self.behavior = value


class _FakeAppKit:
    NSWindowCloseButton = 1
    NSWindowMiniaturizeButton = 2
    NSWindowZoomButton = 3
    NSWindowTitleHidden = "hidden"
    NSWindowCollectionBehaviorFullScreenPrimary = 256


def test_macos_chrome_restores_native_traffic_lights(monkeypatch) -> None:
    monkeypatch.setattr(desktop_macos.sys, "platform", "darwin")
    # Run the AppKit work synchronously instead of through AppKit's main-thread
    # scheduler, so the assertions below read the resulting state directly.
    monkeypatch.setattr(desktop_macos, "_call_after", False)
    fake_appkit = _FakeAppKit()
    monkeypatch.setitem(sys.modules, "AppKit", fake_appkit)
    ns_window = _FakeNSWindow()
    window = SimpleNamespace(native=ns_window)

    assert desktop_macos.configure_native_chrome(window) is True

    # All three traffic lights are back and visible.
    assert sorted(ns_window.buttons) == [
        fake_appkit.NSWindowCloseButton,
        fake_appkit.NSWindowMiniaturizeButton,
        fake_appkit.NSWindowZoomButton,
    ]
    assert all(button.hidden is False for button in ns_window.buttons.values())
    # The title bar stays transparent/hidden with the content running under it.
    assert ns_window.titlebar_transparent is True
    assert ns_window.title_visibility == fake_appkit.NSWindowTitleHidden
    # The green control is a full-screen zoom: fullscreen behaviour (hiding and
    # auto-revealing the traffic lights) stays native AppKit.
    assert ns_window.behavior & fake_appkit.NSWindowCollectionBehaviorFullScreenPrimary


def test_macos_chrome_is_a_noop_off_macos(monkeypatch) -> None:
    monkeypatch.setattr(desktop_macos.sys, "platform", "linux")

    assert desktop_macos.configure_native_chrome(SimpleNamespace(native=object())) is False


def test_macos_chrome_tolerates_a_missing_native_window(monkeypatch) -> None:
    monkeypatch.setattr(desktop_macos.sys, "platform", "darwin")

    assert desktop_macos.configure_native_chrome(SimpleNamespace()) is False
