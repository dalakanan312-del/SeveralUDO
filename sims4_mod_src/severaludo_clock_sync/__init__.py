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


VERSION = "1.1.0"
LOGGER = sims4.log.Logger("SeveralUDOClockSync", default_owner="SeveralUDO")
_alarm_handle = None
_last_reported_day = None
_send_in_progress = False
_last_report_signature = None


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


def _game_clock():
    now = services.time_service().sim_now
    try:
        return int(now.hour()), int(now.minute())
    except Exception:
        text = str(now)
        hour = re.search(r"hour:(\d+)", text)
        minute = re.search(r"minute:(\d+)", text)
        return (int(hour.group(1)) if hour else 0, int(minute.group(1)) if minute else 0)


def _household_snapshot():
    household = services.active_household()
    if household is None:
        return "", []
    name = str(getattr(household, "name", "") or "")
    members = []
    for sim_info in household:
        age_text = str(getattr(sim_info, "age", "") or "").lower()
        baby_value = getattr(sim_info, "is_baby", False)
        if callable(baby_value):
            baby_value = baby_value()
        is_baby = bool(baby_value) or "baby" in age_text or "newborn" in age_text
        members.append({
            "game_sim_id": str(getattr(sim_info, "sim_id", "")),
            "first_name": str(getattr(sim_info, "first_name", "") or ""),
            "last_name": str(getattr(sim_info, "last_name", "") or ""),
            "sex": str(getattr(sim_info, "gender", "") or ""),
            "age_stage": str(getattr(sim_info, "age", "") or "Unknown"),
            "is_baby": is_baby,
        })
    return name, members


def _post_day(config, game_day, game_hour, game_minute, household_name, household_sims):
    global _last_reported_day, _last_report_signature, _send_in_progress
    try:
        payload = json.dumps({
            "game_day": game_day,
            "game_hour": game_hour,
            "game_minute": game_minute,
            "household_name": household_name,
            "household_sims": household_sims,
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
        _last_report_signature = None
        LOGGER.warn("Could not report in-game day {}: {}", game_day, error)
    finally:
        _send_in_progress = False


def _poll_clock(_handle=None):
    global _last_reported_day, _last_report_signature, _send_in_progress
    try:
        config = _load_config()
        if config is None:
            return
        game_day = _absolute_game_day()
        game_hour, game_minute = _game_clock()
        household_name, household_sims = _household_snapshot()
        member_ids = tuple(sorted(item["game_sim_id"] for item in household_sims))
        signature = (game_day, member_ids)
        if signature == _last_report_signature or _send_in_progress:
            return
        _last_reported_day = game_day
        _last_report_signature = signature
        _send_in_progress = True
        thread = threading.Thread(
            target=_post_day,
            args=(config, game_day, game_hour, game_minute, household_name, household_sims)
        )
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
        # Poll every Sim minute. Network traffic occurs only when the day or
        # household membership changes, so births are timestamped promptly.
        _alarm_handle = alarms.add_alarm(
            _start_clock_sync, clock.interval_in_sim_minutes(1),
            _poll_clock, repeating=True
        )
        LOGGER.info("SeveralUDO clock sync started")
    except Exception as error:
        LOGGER.exception("Clock sync could not start: {}", error)


sims4.callback_utils.add_callbacks(
    sims4.callback_utils.CallbackEvent.PROCESS_EVENTS_FOR_HOUSEHOLD_ENTER,
    _start_clock_sync,
)
