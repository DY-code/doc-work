"""Exercise scheduler progress across overlapping system and idle pauses."""

import datetime
import gettext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from safeeyes.activity import ActivityController, prepare_session
from safeeyes.configuration import Config
from safeeyes.context import Context
from safeeyes.core import SafeEyesCore
from safeeyes.model import BreakType, State


@pytest.fixture
def timer(monkeypatch, time_machine):
    time_machine.move_to("2026-11-04 12:00:00", tick=False)
    ctx = Context(Mock(), gettext.NullTranslations(), "test", {})
    cfg = Config(
        {
            "short_breaks": [{"name": "Short"}],
            "long_breaks": [{"name": "Long"}],
            "short_break_interval": 20,
            "long_break_interval": 60,
            "short_break_duration": 30,
            "long_break_duration": 120,
            "pre_break_warning_time": 10,
            "postpone_duration": 5,
            "postpone_unit": "minutes",
            "random_order": False,
        },
        {},
    )
    scheduler = SimpleNamespace(callback=None, duration=None)

    def add(seconds, callback):
        assert scheduler.callback is None
        scheduler.callback = callback
        scheduler.duration = seconds
        return 1

    def remove(source_id):
        assert scheduler.callback is not None
        scheduler.callback = None

    def fire():
        callback = scheduler.callback
        assert callback is not None
        scheduler.callback = None
        time_machine.shift(scheduler.duration)
        callback()

    monkeypatch.setattr("safeeyes.core.GLib.timeout_add_seconds", add)
    monkeypatch.setattr("safeeyes.core.GLib.source_remove", remove)
    core = SafeEyesCore(ctx)
    core.initialize(cfg)
    pause = Mock()
    resume = Mock()
    controller = ActivityController(core, lambda: 1200, pause, resume)
    return SimpleNamespace(
        core=core,
        ctx=ctx,
        cfg=cfg,
        controller=controller,
        scheduler=scheduler,
        fire=fire,
        shift=time_machine.shift,
        pause=pause,
        resume=resume,
    )


def test_same_day_sleep_retains_remaining_even_after_hours(timer):
    timer.core.start()
    timer.shift(300)
    timer.controller.set_reason("sleep", True)
    timer.shift(7200)
    timer.controller.set_reason("sleep", False)
    assert timer.ctx.state == State.WAITING
    assert timer.scheduler.duration == 900
    assert timer.core.running
    assert timer.core.get_break_time() == (
        datetime.datetime.now() + datetime.timedelta(seconds=900)
    )


@pytest.mark.parametrize("last_reason", ["sleep", "screen", "display", "idle"])
def test_all_reasons_must_clear_before_resuming(timer, last_reason):
    timer.core.start()
    timer.shift(300)
    reasons = ["sleep", "screen", "display", "idle"]
    for reason in reasons:
        timer.controller.set_reason(reason, True)
        timer.controller.set_reason(reason, True)
    timer.shift(7200)
    for reason in reasons:
        if reason != last_reason:
            timer.controller.set_reason(reason, False)
            assert not timer.core.running
    timer.controller.set_reason(last_reason, False)
    timer.controller.set_reason(last_reason, False)
    assert timer.core.running
    assert timer.scheduler.duration == 900
    timer.pause.assert_called_once()
    timer.resume.assert_called_once()


def test_idle_before_sleep_cannot_reset_same_day_interval(timer):
    timer.core.start()
    timer.shift(305)
    timer.controller.set_reason("idle", True, credit_seconds=5)
    timer.controller.set_reason("screen", True)
    timer.controller.set_reason("sleep", True)
    timer.shift(7200)
    timer.controller.set_reason("sleep", False)
    timer.controller.set_reason("screen", False)
    assert not timer.core.running
    timer.controller.set_reason("idle", False)
    assert timer.scheduler.duration == 900


@pytest.mark.parametrize("duration,remaining", [(10, 900), (1800, 1200)])
def test_pure_idle_keeps_existing_short_and_long_idle_rules(timer, duration, remaining):
    timer.core.start()
    timer.shift(305)
    timer.controller.set_reason("idle", True, credit_seconds=5)
    timer.shift(duration)
    timer.controller.set_reason("idle", False)
    assert timer.scheduler.duration == remaining


def test_manual_disable_prevents_automatic_resume(timer):
    timer.core.start()
    timer.controller.set_reason("screen", True)
    timer.controller.cancel()
    timer.core.stop()
    timer.controller.set_reason("screen", False)
    assert not timer.core.running
    assert timer.scheduler.callback is None
    timer.resume.assert_not_called()


def test_enabling_while_screen_is_locked_keeps_timer_paused(timer):
    timer.controller.set_reason("screen", True)
    timer.core.start()
    timer.controller.ensure_paused()
    assert not timer.core.running
    timer.shift(100)
    timer.controller.set_reason("screen", False)
    assert timer.scheduler.duration == 1200


def test_pre_break_warning_preserves_its_remaining_time(timer):
    timer.core.start()
    timer.fire()
    assert timer.ctx.state == State.PRE_BREAK
    timer.shift(3)
    timer.controller.set_reason("screen", True)
    timer.shift(600)
    timer.controller.set_reason("screen", False)
    assert timer.ctx.state == State.PRE_BREAK
    assert timer.scheduler.duration == 7


def start_break(timer):
    timer.core.start()
    timer.fire()
    timer.fire()
    assert timer.ctx.state == State.BREAK


def test_break_occurrence_survives_sleep_but_changes_for_next_break(timer):
    start_break(timer)
    break_obj = timer.core._taking_break
    token = break_obj.occurrence_id
    assert token
    timer.controller.set_reason("sleep", True)
    timer.shift(10)
    timer.controller.set_reason("sleep", False)
    assert timer.core._taking_break.occurrence_id == token
    for _ in range(20):
        timer.fire()
    timer.fire()
    timer.fire()
    timer.fire()
    assert timer.core._taking_break.occurrence_id != token


def test_short_sleep_credits_time_to_current_break(timer):
    shown = Mock(return_value=True)
    stopped = Mock(return_value=True)
    timer.core.start_break += shown
    timer.core.on_stop_break += stopped
    start_break(timer)
    timer.controller.set_reason("sleep", True)
    timer.shift(10)
    timer.controller.set_reason("sleep", False)
    assert timer.ctx.state == State.BREAK
    # The immediate countdown update displays 20, then stores 19.
    assert timer.core._countdown == 19
    assert shown.call_count == 2
    stopped.assert_not_called()


def test_long_sleep_finishes_break_once_and_then_waits_for_unlock(timer):
    stopped = Mock(return_value=True)
    timer.core.on_stop_break += stopped
    start_break(timer)
    timer.controller.set_reason("sleep", True)
    timer.controller.set_reason("screen", True)
    timer.shift(600)
    timer.controller.set_reason("sleep", False)
    stopped.assert_not_called()
    timer.controller.set_reason("screen", False)
    stopped.assert_called_once()
    timer.fire()  # schedule the next interval after completing the break
    assert timer.scheduler.duration == 1200


def test_new_day_resets_interval_and_long_break_progress(timer):
    start_break(timer)
    for _ in range(30):
        timer.fire()  # finish the first short break
    timer.fire()  # next interval, the long break is now closer
    timer.shift(300)
    timer.controller.set_reason("screen", True)
    timer.shift(86400)
    timer.controller.set_reason("screen", False)
    assert timer.scheduler.duration == 1200
    assert timer.core._break_queue.get_break_with_type().time == 20
    assert timer.core._break_queue.get_break_with_type(BreakType.LONG_BREAK).time == 60


def test_new_day_discards_incomplete_yesterday_break(timer):
    stopped = Mock(return_value=True)
    timer.core.on_stop_break += stopped
    start_break(timer)
    timer.controller.set_reason("sleep", True)
    timer.shift(86400)
    timer.controller.set_reason("sleep", False)
    assert timer.ctx.state == State.WAITING
    assert timer.scheduler.duration == 1200
    stopped.assert_called_once()


def test_continuous_use_at_midnight_does_not_reset(timer, time_machine):
    time_machine.move_to("2026-11-04 23:55:00", tick=False)
    timer.core.start()
    timer.shift(600)
    assert timer.core.get_break_time() == datetime.datetime(2026, 11, 5, 0, 15)
    assert not timer.controller.paused
    timer.pause.assert_not_called()


@pytest.mark.parametrize("same_day", [True, False])
def test_startup_session_only_restores_todays_queue(timer, same_day):
    date = datetime.date.today()
    if not same_day:
        date -= datetime.timedelta(days=1)
    session = {"last_active_date": date.isoformat(), "break": "Long", "plugin": {}}
    prepare_session(session)
    assert ("break" in session) == same_day
    assert session["last_active_date"] == datetime.date.today().isoformat()


def test_disable_during_a_frozen_break_advances_queue_once(timer):
    stopped = Mock(return_value=True)
    timer.core.on_stop_break += stopped
    start_break(timer)
    timer.controller.set_reason("screen", True)
    timer.controller.cancel()
    timer.core.stop()
    timer.controller.set_reason("screen", False)
    stopped.assert_called_once()
    assert not timer.core.running
    assert timer.core._break_queue.get_break_with_type(BreakType.LONG_BREAK).time == 40


def test_pause_just_after_break_finishes_does_not_finish_it_twice(timer):
    stopped = Mock(return_value=True)
    timer.core.on_stop_break += stopped
    start_break(timer)
    for _ in range(30):
        timer.fire()
    stopped.assert_called_once()
    timer.controller.set_reason("screen", True)
    timer.shift(600)
    timer.controller.set_reason("screen", False)
    stopped.assert_called_once()
    assert timer.ctx.state == State.WAITING
    timer.fire()
    assert timer.scheduler.duration == 1200


def test_idle_threshold_straddling_midnight_still_resets_on_first_use(
    timer, time_machine
):
    time_machine.move_to("2026-11-04 23:55:00", tick=False)
    timer.core.start()
    timer.shift(302)  # Idle started at 23:59:57, detected at 00:00:02.
    timer.controller.set_reason("idle", True, credit_seconds=5)
    timer.shift(5)
    timer.controller.set_reason("idle", False)
    assert timer.scheduler.duration == 1200


def test_queued_idle_event_cannot_override_manual_disable(timer):
    from safeeyes.safeeyes import SafeEyes

    app = SimpleNamespace(active=False, activity=timer.controller)
    SafeEyes.set_idle(app, True, 5)
    assert not timer.controller.reasons


@pytest.mark.parametrize("same_day", [True, False])
def test_manual_enable_applies_daily_reset_without_starting_while_locked(
    timer, same_day
):
    from safeeyes.safeeyes import SafeEyes

    date = datetime.date.today()
    if not same_day:
        date -= datetime.timedelta(days=1)
    timer.ctx.session["last_active_date"] = date.isoformat()
    app = SimpleNamespace(
        active=False,
        required_plugin_dialog_active=False,
        context=timer.ctx,
        activity=timer.controller,
        safe_eyes_core=timer.core,
        plugins_manager=Mock(),
    )
    timer.controller.set_reason("screen", True)
    SafeEyes.enable_safeeyes(app, datetime.datetime.now().timestamp() + 300)
    assert not timer.core.running
    timer.controller.set_reason("screen", False)
    assert timer.scheduler.duration == (300 if same_day else 1200)
