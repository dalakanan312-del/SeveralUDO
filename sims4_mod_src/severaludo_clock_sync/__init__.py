"""SeveralUDO in-game day bridge for The Sims 4 (Python 3.7)."""

import json
import os
import re
import threading
import urllib.request

import alarms
import clock
import services
import sims4.callback_utils
import sims4.log


VERSION = "1.0.0"
LOGGER = sims4.log.Logger("SeveralUDOClockSync", default_owner="SeveralUDO")
_alarm_handle = None
_last_reported_day = None
_send_in_progress = False


def _config_path():
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(
        profile, "Documents", "Electronic Arts", "The Sims 4", "Mods",
        "SeveralUDOClockSync", "config.json"
    )


def _load_config():
    path = _config_path()
    try:
        with open(path, "r") as handle:
            data = json.load(handle)
        if not data.get("enabled", True):
            return None
        if not data.get("receiver_url") or not data.get("sync_token"):
            return None
        return data
    except Exception as error:
        LOGGER.warn("Clock sync configuration is unavailable: {}", error)
        return None


def _absolute_game_day():
    now = services.time_service().sim_now
    # DateAndTime's stable text form includes `day:N week:N` across current
    # game builds. It avoids relying on private tick constants.
    text = str(now)
    match = re.search(r"day:(\d+)\s+week:(\d+)", text)
    if match:
        return int(match.group(2)) * 7 + int(match.group(1))
    # Fallback for builds where the order changes.
    day = re.search(r"day:(\d+)", text)
    week = re.search(r"week:(\d+)", text)
    if day and week:
        return int(week.group(1)) * 7 + int(day.group(1))
    raise ValueError("Could not read the in-game date: {}".format(text))


def _post_day(config, game_day):
    global _last_reported_day, _send_in_progress
    try:
        payload = json.dumps({
            "game_day": game_day,
            "mod_version": VERSION,
        }).encode("utf-8")
        request = urllib.request.Request(
            config["receiver_url"], data=payload, method="POST",
            headers={
                "Authorization": "Bearer " + config["sync_token"],
                "Content-Type": "application/json",
                "User-Agent": "SeveralUDOClockSync/" + VERSION,
            },
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            response.read()
        LOGGER.info("Reported in-game day {} to SeveralUDO", game_day)
    except Exception as error:
        # The next poll retries; game simulation is never interrupted.
        _last_reported_day = None
        LOGGER.warn("Could not report in-game day {}: {}", game_day, error)
    finally:
        _send_in_progress = False


def _poll_clock(_handle=None):
    global _last_reported_day, _send_in_progress
    try:
        config = _load_config()
        if config is None:
            return
        game_day = _absolute_game_day()
        if game_day == _last_reported_day or _send_in_progress:
            return
        _last_reported_day = game_day
        _send_in_progress = True
        thread = threading.Thread(target=_post_day, args=(config, game_day))
        thread.daemon = True
        thread.start()
    except Exception as error:
        LOGGER.exception("Clock polling failed: {}", error)


def _start_clock_sync(*_args, **_kwargs):
    global _alarm_handle
    try:
        if _alarm_handle is not None:
            alarms.cancel_alarm(_alarm_handle)
        _poll_clock()
        # Poll every 30 Sim minutes. Only a changed calendar day sends data.
        _alarm_handle = alarms.add_alarm(
            _start_clock_sync, clock.interval_in_sim_minutes(30),
            _poll_clock, repeating=True
        )
        LOGGER.info("SeveralUDO clock sync started")
    except Exception as error:
        LOGGER.exception("Clock sync could not start: {}", error)


sims4.callback_utils.add_callbacks(
    sims4.callback_utils.CallbackEvent.PROCESS_EVENTS_FOR_HOUSEHOLD_ENTER,
    _start_clock_sync,
)
