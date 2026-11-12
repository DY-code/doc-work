"""Exercise threshold resets and independent recovery across actual boot identities."""

import json
from unittest.mock import Mock

import pytest

from safeeyes.activity import ActivityController
from safeeyes.model import BreakType, State
from safeeyes.progress import ProgressFile
from safeeyes.tests.test_activity import timer as timer_fixture, start_break

timer = timer_fixture


def checkpoint(timer, path, closed=False):
    result = ProgressFile(
        path, timer.controller, lambda: timer.cfg, boot=lambda: "boot-1"
    )
    result.save(True, closed)
    return result


def restart(timer, path, boot="boot-2", locked=False):
    timer.core.stop()
    timer.core.initialize(timer.cfg)
    timer.controller = ActivityController(
        timer.core, lambda: 1200, timer.pause, timer.resume
    )
    if locked:
        timer.controller.set_reason("screen", True)
    file = ProgressFile(path, timer.controller, lambda: timer.cfg, boot=lambda: boot)
    return file.restore()


@pytest.mark.parametrize("seconds,remaining", [(7199, 900), (7200, 1200), (7201, 1200)])
def test_threshold_boundary(timer, seconds, remaining):
    timer.core.start()
    timer.shift(300)
    timer.controller.set_reason("screen", True)
    timer.shift(seconds)
    timer.controller.set_reason("screen", False)
    assert timer.scheduler.duration == remaining
    assert timer.core._break_queue.get_break_with_type(BreakType.LONG_BREAK).time == 60


def test_threshold_is_customizable_and_keeps_all_pause_reasons(timer):
    timer.controller.reset_after = lambda: 60
    timer.core.start()
    timer.shift(300)
    timer.controller.set_reason("screen", True)
    timer.shift(30)
    timer.controller.set_reason("sleep", True)
    timer.shift(30)
    timer.controller.set_reason("sleep", False)
    assert not timer.core.running
    timer.controller.set_reason("screen", False)
    assert timer.scheduler.duration == 1200


def test_ordinary_idle_after_unlock_does_not_extend_system_away_duration(timer):
    timer.core.start()
    timer.shift(300)
    timer.controller.set_reason("idle", True)
    timer.controller.set_reason("screen", True)
    timer.shift(3600)
    timer.controller.set_reason("screen", False)
    timer.shift(7200)
    timer.controller.set_reason("idle", False)
    assert timer.scheduler.duration == 900


@pytest.mark.parametrize("seconds,remaining", [(3600, 900), (7200, 1200)])
def test_real_reboot_restores_or_resets_by_threshold(
    timer, tmp_path, seconds, remaining
):
    timer.core.start()
    timer.shift(300)
    path = tmp_path / "progress.json"
    checkpoint(timer, path, closed=True)
    timer.shift(seconds)
    assert restart(timer, path)
    assert timer.scheduler.duration == remaining


def test_reboot_still_locked_waits_until_actual_unlock(timer, tmp_path):
    timer.core.start()
    timer.shift(300)
    path = tmp_path / "progress.json"
    checkpoint(timer, path)
    timer.shift(3600)
    assert restart(timer, path, locked=True)
    assert not timer.core.running
    timer.shift(3600)
    timer.controller.set_reason("screen", False)
    assert timer.scheduler.duration == 1200


def test_lock_shutdown_and_boot_are_one_continuous_away_period(timer, tmp_path):
    timer.core.start()
    timer.shift(300)
    timer.controller.set_reason("screen", True)
    timer.shift(3600)
    timer.controller.set_reason("shutdown", True)
    path = tmp_path / "progress.json"
    checkpoint(timer, path)
    timer.shift(3600)
    assert restart(timer, path)
    assert timer.scheduler.duration == 1200


@pytest.mark.parametrize("offline,remaining", [(7199, 900), (7200, 1200)])
def test_shutdown_after_idle_saves_the_actual_system_departure_immediately(
    timer, tmp_path, offline, remaining
):
    from types import SimpleNamespace
    from safeeyes.safeeyes import SafeEyes

    timer.core.start()
    timer.shift(305)
    timer.controller.set_reason("idle", True, 5)
    path = tmp_path / "progress.json"
    file = checkpoint(timer, path)
    timer.shift(900)
    app = SimpleNamespace(activity=timer.controller, checkpoint=lambda: file.save(True))
    SafeEyes._on_system_state(app, "shutdown", True)
    data = json.loads(path.read_text())
    assert data["away_since"] == data["saved_at"]
    assert data["system_pause"] is True
    timer.shift(offline)
    assert restart(timer, path)
    assert timer.scheduler.duration == remaining


def test_same_boot_explicit_quit_is_not_mistaken_for_shutdown(timer, tmp_path):
    timer.core.start()
    path = tmp_path / "progress.json"
    checkpoint(timer, path, closed=True)
    timer.shift(7200)
    assert not restart(timer, path, boot="boot-1")


def test_cross_day_reboot_resets_even_below_threshold(timer, tmp_path, time_machine):
    time_machine.move_to("2026-11-12 23:50:00", tick=False)
    timer.core.start()
    timer.shift(300)
    path = tmp_path / "progress.json"
    checkpoint(timer, path)
    timer.shift(600)
    assert restart(timer, path)
    assert timer.scheduler.duration == 1200


def test_checkpoint_preserves_long_progress_and_queue_order(timer, tmp_path):
    start_break(timer)
    for _ in range(30):
        timer.fire()
    timer.fire()
    timer.shift(300)
    queue = timer.core._break_queue.export_progress()
    assert timer.core._break_queue.get_break_with_type(BreakType.LONG_BREAK).time == 40
    path = tmp_path / "progress.json"
    checkpoint(timer, path)
    timer.shift(3600)
    assert restart(timer, path)
    assert timer.core._break_queue.export_progress() == queue
    assert timer.scheduler.duration == 900
    timer.controller.set_reason("screen", True)
    timer.shift(7200)
    timer.controller.set_reason("screen", False)
    assert timer.core._break_queue.get_break_with_type(BreakType.LONG_BREAK).time == 60


def test_random_queue_order_survives_reboot_with_session_saving_disabled(
    timer, tmp_path
):
    timer.cfg.set("random_order", True)
    timer.cfg.set("persist_state", False)
    timer.cfg.set("short_breaks", [{"name": name} for name in ("A", "B", "C")])
    timer.core.initialize(timer.cfg)
    timer.core.start()
    timer.shift(300)
    expected = timer.core._break_queue.export_progress()
    path = tmp_path / "progress.json"
    checkpoint(timer, path)
    timer.shift(300)
    assert restart(timer, path)
    assert timer.core._break_queue.export_progress() == expected
    assert timer.scheduler.duration == 900


def test_two_separate_short_locks_do_not_add_up_to_threshold(timer):
    timer.core.start()
    timer.shift(300)
    for _ in range(2):
        timer.controller.set_reason("screen", True)
        timer.shift(3600)
        timer.controller.set_reason("screen", False)
        assert timer.scheduler.duration == 900


def test_shutdown_delay_is_released_after_checkpoint_callback(monkeypatch):
    from gi.repository import GLib
    from safeeyes.system_monitor import SystemStateMonitor

    events = []
    monitor = SystemStateMonitor(lambda *args: events.append(args))
    monitor._sleep_delay_fd = 123
    monkeypatch.setattr(
        "safeeyes.system_monitor.os.close", lambda fd: events.append(fd)
    )
    monitor._sleep_signal(
        None, None, "PrepareForShutdown", GLib.Variant("(b)", (True,))
    )
    assert events == [("shutdown", True), 123]


def test_reboot_during_break_credits_offline_time(timer, tmp_path):
    start_break(timer)
    token = timer.core._taking_break.occurrence_id
    path = tmp_path / "progress.json"
    checkpoint(timer, path)
    timer.shift(10)
    assert restart(timer, path)
    assert timer.ctx.state == State.BREAK
    assert timer.core._countdown == 19
    assert timer.core._taking_break.occurrence_id == token


def test_reboot_during_warning_preserves_remaining_warning(timer, tmp_path):
    timer.core.start()
    timer.fire()
    timer.shift(3)
    path = tmp_path / "progress.json"
    checkpoint(timer, path)
    timer.shift(600)
    assert restart(timer, path)
    assert timer.ctx.state == State.PRE_BREAK
    assert timer.scheduler.duration == 7


def test_changed_configuration_and_corrupt_file_fall_back(timer, tmp_path):
    timer.core.start()
    path = tmp_path / "progress.json"
    checkpoint(timer, path)
    timer.cfg.set("short_break_interval", 25)
    assert not restart(timer, path)
    path.write_text("broken")
    assert not restart(timer, path)


def test_disabled_timer_cannot_be_resurrected_from_checkpoint(timer, tmp_path):
    timer.core.start()
    path = tmp_path / "progress.json"
    file = checkpoint(timer, path)
    timer.controller.cancel()
    timer.core.stop()
    file.save(False)
    assert json.loads(path.read_text())["enabled"] is False
    assert not restart(timer, path)


def test_shutdown_signal_uses_same_coordinator():
    from gi.repository import GLib
    from safeeyes.system_monitor import SystemStateMonitor

    notify = Mock()
    monitor = SystemStateMonitor(notify)
    monitor._sleep_signal(
        None, None, "PrepareForShutdown", GLib.Variant("(b)", (True,))
    )
    notify.assert_called_once_with("shutdown", True)
