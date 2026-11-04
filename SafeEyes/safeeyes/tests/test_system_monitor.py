"""Verify system signal ordering and display polling without changing the desktop."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from gi.repository import GLib

from safeeyes.system_monitor import SystemStateMonitor


def test_wakeup_refreshes_screen_and_display_before_clearing_sleep():
    changes = []
    monitor = SystemStateMonitor(lambda *args: changes.append(args))
    monitor._refresh_screen = lambda: changes.append(("screen", True))
    monitor._poll_display = lambda: changes.append(("display", True))
    monitor._sleep_signal(None, None, "PrepareForSleep", GLib.Variant("(b)", (False,)))
    assert changes == [("screen", True), ("display", True), ("sleep", False)]


def test_unlock_checks_display_before_clearing_screen_reason():
    changes = []
    monitor = SystemStateMonitor(lambda *args: changes.append(args))
    monitor._poll_display = lambda: changes.append(("display", True))
    monitor._screen_signal(None, None, "ActiveChanged", GLib.Variant("(b)", (False,)))
    assert changes == [("display", True), ("screen", False)]


@pytest.mark.parametrize("level", [0, 1, 2, 3])
@pytest.mark.parametrize("enabled", [True, False])
def test_display_power_levels(level, enabled):
    on_state = Mock()
    monitor = SystemStateMonitor(on_state)
    monitor._display = Mock()
    monitor._display.dpms_info.return_value = SimpleNamespace(
        power_level=level, state=enabled
    )
    assert monitor._poll_display() == GLib.SOURCE_CONTINUE
    on_state.assert_called_once_with("display", enabled and level != 0)


def test_failed_display_connection_cannot_leave_timer_paused():
    on_state = Mock()
    monitor = SystemStateMonitor(on_state)
    display = Mock()
    display.dpms_info.side_effect = RuntimeError("disconnected")
    monitor._display = display
    assert monitor._poll_display() == GLib.SOURCE_REMOVE
    on_state.assert_called_once_with("display", False)
    display.close.assert_called_once()


def test_start_reads_existing_sleep_lock_and_display_states(monkeypatch):
    states = []
    monitor = SystemStateMonitor(lambda *args: states.append(args))
    sleep = Mock()
    sleep.get_cached_property.return_value = GLib.Variant("b", True)
    screen = Mock()
    screen.call_sync.return_value = GLib.Variant("(b)", (True,))
    monitor._connect = Mock(side_effect=[sleep, screen])
    monitor._acquire_sleep_delay = Mock()
    display = Mock()
    display.has_extension.return_value = True
    display.dpms_info.return_value = SimpleNamespace(power_level=3, state=True)
    monkeypatch.setattr("Xlib.display.Display", lambda: display)
    monkeypatch.setattr("safeeyes.system_monitor.GLib.timeout_add_seconds", Mock())
    monitor.start()
    assert states == [("sleep", True), ("screen", True), ("display", True)]


def test_missing_services_do_not_prevent_display_monitoring(monkeypatch):
    monitor = SystemStateMonitor(Mock())
    monitor._connect = Mock(side_effect=RuntimeError("service missing"))
    monitor._watch_display = Mock()
    monitor.start()
    monitor._watch_display.assert_called_once()


def test_stop_releases_sources_connections_and_display(monkeypatch):
    remove = Mock()
    monkeypatch.setattr("safeeyes.system_monitor.GLib.source_remove", remove)
    monitor = SystemStateMonitor(Mock())
    proxy = Mock()
    display = Mock()
    monitor._proxies = [(proxy, 15)]
    monitor._poll_id = 42
    monitor._display = display
    monitor.stop()
    remove.assert_called_once_with(42)
    proxy.disconnect.assert_called_once_with(15)
    display.close.assert_called_once()
    assert not monitor._proxies
    assert monitor._poll_id is None


def test_sleep_releases_delay_only_after_the_timer_is_paused(monkeypatch):
    events = []
    monitor = SystemStateMonitor(lambda *args: events.append(args))
    monitor._sleep_delay_fd = 123
    monkeypatch.setattr(
        "safeeyes.system_monitor.os.close", lambda fd: events.append(fd)
    )
    monitor._sleep_signal(None, None, "PrepareForSleep", GLib.Variant("(b)", (True,)))
    assert events == [("sleep", True), 123]
    assert monitor._sleep_delay_fd is None


def test_sleep_releases_delay_even_if_pause_callback_fails(monkeypatch):
    monitor = SystemStateMonitor(Mock(side_effect=RuntimeError("callback failed")))
    monitor._sleep_delay_fd = 123
    close = Mock()
    monkeypatch.setattr("safeeyes.system_monitor.os.close", close)
    with pytest.raises(RuntimeError, match="callback failed"):
        monitor._sleep_signal(
            None, None, "PrepareForSleep", GLib.Variant("(b)", (True,))
        )
    close.assert_called_once_with(123)
