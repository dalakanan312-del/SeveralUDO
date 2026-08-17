"""SeveralUDO in-game day bridge for The Sims 4 (Python 3.7)."""

import json
import os
import re
import threading

import alarms
import clock
import services
import sims4.callback_utils
import sims4.commands
import sims4.log


VERSION = "1.4.0"
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
    handle = None
    try:
        # Sims 4's embedded file object does not consistently implement the
        # context-manager protocol, so close it explicitly.
        handle = open(path, "r")
        data = json.load(handle)
        if not data.get("enabled", True):
            return None
        if not data.get("receiver_url") or not data.get("sync_token"):
            return None
        return data
    except Exception as error:
        LOGGER.warn("Clock sync configuration is unavailable: {}", error)
        return None
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


def _absolute_game_day():
    now = services.time_service().sim_now
    # Current game builds expose absolute_days(). Prefer that stable public
    # accessor; the text fallbacks retain compatibility with older builds.
    try:
        return int(now.absolute_days())
    except Exception:
        pass
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


def _report_payload(config):
    game_day = _absolute_game_day()
    game_hour, game_minute = _game_clock()
    household_name, household_sims = _household_snapshot()
    payload = json.dumps({
        "game_day": game_day,
        "game_hour": game_hour,
        "game_minute": game_minute,
        "household_name": household_name,
        "household_sims": household_sims,
        "mod_version": VERSION,
    }).encode("utf-8")
    return game_day, game_hour, game_minute, household_name, household_sims, payload


def _send_payload(config, payload):
    # The game mod never opens a network connection. It only writes this local
    # queue; the separately installed Windows relay performs the HTTPS request.
    folder = os.path.dirname(_config_path())
    pending = os.path.join(folder, "pending_report.json")
    envelope = json.dumps({
        "receiver_url": config["receiver_url"],
        "sync_token": config["sync_token"],
        "payload": json.loads(payload.decode("utf-8")),
    })
    # Write only JSON. ModGuard intentionally blocks temporary-file creation
    # patterns commonly used by executable downloaders.
    handle = open(pending, "w")
    if handle is None:
        raise IOError("ModGuard blocked the local JSON report queue")
    try:
        handle.write(envelope)
    finally:
        handle.close()
    return 202


def _game_clock():
    now = services.time_service().sim_now
    try:
        return int(now.hour()), int(now.minute())
    except Exception:
        text = str(now)
        hour = re.search(r"hour:(\d+)", text)
        minute = re.search(r"minute:(\d+)", text)
        return (int(hour.group(1)) if hour else 0, int(minute.group(1)) if minute else 0)


def _value_or_call(owner, name, default=None):
    """Read a Sims API property that may be exposed as a zero-argument method."""
    try:
        value = getattr(owner, name, default)
        return value() if callable(value) else value
    except Exception:
        return default


def _pregnancy_snapshot(sim_info):
    tracker = getattr(sim_info, "pregnancy_tracker", None)
    if tracker is None:
        return {"is_pregnant": False}
    is_pregnant = bool(_value_or_call(tracker, "is_pregnant", False))
    if not is_pregnant:
        return {"is_pregnant": False}

    progress = _value_or_call(sim_info, "pregnancy_progress", None)
    if progress is None:
        progress = _value_or_call(tracker, "pregnancy_progress", None)
    try:
        progress = max(0.0, min(1.0, float(progress)))
    except Exception:
        progress = None

    expected = _value_or_call(tracker, "offspring_count", None)
    try:
        expected = max(1, int(expected))
    except Exception:
        expected = None

    partner = None
    parents = _value_or_call(tracker, "get_parents", None)
    try:
        for parent in parents or ():
            if str(getattr(parent, "sim_id", "")) != str(getattr(sim_info, "sim_id", "")):
                partner = parent
                break
    except Exception:
        partner = None
    if partner is None:
        partner = _value_or_call(tracker, "get_partner", None)

    return {
        "is_pregnant": True,
        "pregnancy_progress": progress,
        "pregnancy_offspring_count": expected,
        "pregnancy_partner_game_sim_id": str(getattr(partner, "sim_id", "") or ""),
        "pregnancy_partner_first_name": str(getattr(partner, "first_name", "") or ""),
        "pregnancy_partner_last_name": str(getattr(partner, "last_name", "") or ""),
    }


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
        member = {
            "game_sim_id": str(getattr(sim_info, "sim_id", "")),
            "first_name": str(getattr(sim_info, "first_name", "") or ""),
            "last_name": str(getattr(sim_info, "last_name", "") or ""),
            "sex": str(getattr(sim_info, "gender", "") or ""),
            "age_stage": str(getattr(sim_info, "age", "") or "Unknown"),
            "is_baby": is_baby,
        }
        member.update(_pregnancy_snapshot(sim_info))
        members.append(member)
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
        _send_payload(config, payload)
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
        member_states = tuple(sorted(
            (item["game_sim_id"], bool(item.get("is_pregnant")))
            for item in household_sims
        ))
        signature = (game_day, member_states)
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
        # Poll every Sim minute. A report is queued only when the day,
        # household membership, or a household pregnancy state changes.
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


@sims4.commands.Command(
    "severaludo.clock.status",
    command_type=sims4.commands.CommandType.Live,
)
def clock_status(_connection=None):
    """Show a safe local diagnostic without revealing the private token."""
    out = sims4.commands.CheatOutput(_connection)
    config = _load_config()
    if config is None:
        out("Clock Sync is not configured or is disabled.")
        return False
    try:
        game_day = _absolute_game_day()
        hour, minute = _game_clock()
        household_name, household_sims = _household_snapshot()
        pregnancy_count = sum(1 for member in household_sims if member.get("is_pregnant"))
        out("Clock Sync v{} is ready.".format(VERSION))
        out("Game day {}, time {:02d}:{:02d}; household '{}'; {} Sim(s); {} pregnancy/pregnancies.".format(
            game_day, hour, minute, household_name or "None", len(household_sims), pregnancy_count
        ))
        return True
    except Exception as error:
        out("Clock Sync cannot read the game state: {}".format(error))
        return False


@sims4.commands.Command(
    "severaludo.clock.report",
    command_type=sims4.commands.CommandType.Live,
)
def force_clock_report(_connection=None):
    """Send a report immediately and show the receiver result in the console."""
    global _last_reported_day, _last_report_signature
    out = sims4.commands.CheatOutput(_connection)
    config = _load_config()
    if config is None:
        out("Clock Sync is not configured or is disabled.")
        return False
    try:
        game_day, hour, minute, household_name, household_sims, payload = _report_payload(config)
        status = _send_payload(config, payload)
        member_ids = tuple(sorted(item["game_sim_id"] for item in household_sims))
        _last_reported_day = game_day
        _last_report_signature = (game_day, member_ids)
        if status == 202:
            out("Report queued for the secure Windows relay. Game day {}, {:02d}:{:02d}, household '{}'.".format(
                game_day, hour, minute, household_name or "None"))
        else:
            out("Report accepted (HTTP {}). Game day {}, {:02d}:{:02d}, household '{}'.".format(
                status, game_day, hour, minute, household_name or "None"))
        return True
    except Exception as error:
        out("Report failed: {}".format(error))
        LOGGER.exception("Manual Clock Sync report failed: {}", error)
        return False
