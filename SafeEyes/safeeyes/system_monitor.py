"""Observe sleep, GNOME screen state, and X11 display power independently."""

import logging
import os
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib


class SystemStateMonitor:
    """Report independent pause reasons, including states already active at startup."""

    def __init__(self, on_state: Callable[[str, bool], None], is_wayland=False):
        self.on_state = on_state
        self.is_wayland = is_wayland
        self._proxies: list[tuple[Gio.DBusProxy, int]] = []
        self._screen_proxy = None
        self._sleep_proxy = None
        self._sleep_delay_fd = None
        self._display = None
        self._poll_id = None

    def start(self) -> None:
        """Start listeners; unavailable desktop services must not prevent startup."""
        self._watch_sleep()
        self._watch_screen()
        if not self.is_wayland:
            self._watch_display()

    def _connect(self, bus, name, path, interface, signal, callback):
        proxy = Gio.DBusProxy.new_for_bus_sync(
            bus,
            Gio.DBusProxyFlags.DO_NOT_AUTO_START,
            None,
            name,
            path,
            interface,
            None,
        )
        if proxy.get_name_owner() is None:
            raise RuntimeError(f"{name} is unavailable")
        handler = proxy.connect(signal, callback)
        self._proxies.append((proxy, handler))
        return proxy

    def _watch_sleep(self) -> None:
        try:
            proxy = self._connect(
                Gio.BusType.SYSTEM,
                "org.freedesktop.login1",
                "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager",
                "g-signal",
                self._sleep_signal,
            )
            self._sleep_proxy = proxy
            self._acquire_sleep_delay()
            sleeping = proxy.get_cached_property("PreparingForSleep")
            if sleeping is not None:
                self.on_state("sleep", sleeping.unpack())
                if sleeping.unpack():
                    self._release_sleep_delay()
        except (GLib.Error, RuntimeError) as error:
            logging.warning("Cannot monitor system sleep: %s", error)

    def _sleep_signal(self, proxy, sender, signal, parameters) -> None:
        if signal == "PrepareForShutdown":
            shutting_down = parameters.unpack()[0]
            if shutting_down:
                try:
                    self.on_state("shutdown", True)
                finally:
                    self._release_sleep_delay()
            else:
                self.on_state("shutdown", False)
                self._acquire_sleep_delay()
            return
        if signal == "PrepareForSleep":
            (sleeping,) = parameters.unpack()
            if sleeping:
                try:
                    self.on_state("sleep", True)
                finally:
                    # Let logind suspend once the timer has actually been frozen.
                    self._release_sleep_delay()
            else:
                # Discover locks/blanking before removing the sleep reason.
                self._refresh_screen()
                self._poll_display()
                self.on_state("sleep", False)
                self._acquire_sleep_delay()

    def _acquire_sleep_delay(self) -> None:
        """Ask logind to deliver the sleep signal before freezing this process."""
        if self._sleep_proxy is None or self._sleep_delay_fd is not None:
            return
        try:
            result, descriptors = self._sleep_proxy.call_with_unix_fd_list_sync(
                "Inhibit",
                GLib.Variant(
                    "(ssss)",
                    ("sleep:shutdown", "Safe Eyes", "Save the break timer", "delay"),
                ),
                Gio.DBusCallFlags.NONE,
                1000,
                None,
                None,
            )
            self._sleep_delay_fd = descriptors.get(result.unpack()[0])
        except GLib.Error as error:
            logging.warning("Cannot delay sleep while saving timer: %s", error)

    def _release_sleep_delay(self) -> None:
        if self._sleep_delay_fd is not None:
            os.close(self._sleep_delay_fd)
            self._sleep_delay_fd = None

    def _watch_screen(self) -> None:
        try:
            self._screen_proxy = self._connect(
                Gio.BusType.SESSION,
                "org.gnome.ScreenSaver",
                "/org/gnome/ScreenSaver",
                "org.gnome.ScreenSaver",
                "g-signal",
                self._screen_signal,
            )
            self._refresh_screen()
        except (GLib.Error, RuntimeError) as error:
            logging.warning("Cannot monitor GNOME screen state: %s", error)

    def _refresh_screen(self) -> None:
        if self._screen_proxy is None:
            return
        try:
            result = self._screen_proxy.call_sync(
                "GetActive", None, Gio.DBusCallFlags.NONE, 1000, None
            )
            self.on_state("screen", result.unpack()[0])
        except GLib.Error as error:
            logging.warning("Cannot read GNOME screen state: %s", error)

    def _screen_signal(self, proxy, sender, signal, parameters) -> None:
        if signal == "ActiveChanged":
            (active,) = parameters.unpack()
            if not active:
                self._poll_display()
            self.on_state("screen", active)

    def _watch_display(self) -> None:
        try:
            from Xlib.display import Display

            self._display = Display()
            if not self._display.has_extension("DPMS"):
                self._close_display()
                logging.warning(
                    "X11 DPMS is unavailable; screen power is not monitored"
                )
                return
            if self._poll_display():
                self._poll_id = GLib.timeout_add_seconds(1, self._poll_display)
        except Exception as error:
            self._close_display()
            logging.warning("Cannot monitor X11 display power: %s", error)

    def _poll_display(self) -> bool:
        if self._display is None:
            return GLib.SOURCE_REMOVE
        try:
            from Xlib.ext import dpms

            info = self._display.dpms_info()
            blanked = bool(info.state and info.power_level != dpms.DPMSModeOn)
            self.on_state("display", blanked)
            return GLib.SOURCE_CONTINUE
        except Exception as error:
            logging.warning("Cannot read X11 display power: %s", error)
            self._close_display()
            self._poll_id = None
            # A failed monitor cannot hold the application paused forever.
            self.on_state("display", False)
            return GLib.SOURCE_REMOVE

    def _close_display(self) -> None:
        if self._display is not None:
            try:
                self._display.close()
            except Exception:
                logging.debug("X11 display connection was already closed")
            self._display = None

    def stop(self) -> None:
        """Release callbacks and the dedicated X11 connection on exit."""
        if self._poll_id is not None:
            GLib.source_remove(self._poll_id)
            self._poll_id = None
        for proxy, handler in self._proxies:
            proxy.disconnect(handler)
        self._proxies.clear()
        self._screen_proxy = None
        self._sleep_proxy = None
        self._release_sleep_delay()
        self._close_display()
