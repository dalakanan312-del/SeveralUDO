"""SeveralUDO Historical Diseases -- an unofficial Healthcare Redux extension.

This module deliberately contains no Healthcare Redux assets or source code.  It
looks up public game tuning resources supplied by an installed copy of HCR and
asks HCR to handle the actual illness once a historical profile is selected.
"""

import sims4.commands
import sims4.resources
import services


VERSION = "0.2.0"

# Historical labels mapped to the closest HCR disease model.  The HCR model is
# shown in command output so the player always knows which mechanics apply.
DISEASES = {
    "plague": (0x9660AC7FA1B6BE87, "Meningitis", "deadly systemic disease"),
    "smallpox": (0xF3FED5977E405FEE, "Tuberculosis", "long severe disease"),
    "cholera": (0xBA72B91AA6291334, "Gastroenteritis", "acute enteric disease"),
    "typhus": (0x97C578AD63613C67, "Malaria", "severe fever disease"),
    "dysentery": (0xBA72B91AA6291334, "Gastroenteritis", "acute enteric disease"),
    "scarlet_fever": (0xFD2F68D2F98AFBE2, "Tonsillitis", "throat infection"),
}


def _output(connection):
    return sims4.commands.CheatOutput(connection)


def _active_sim(connection):
    client = services.client_manager().get(connection)
    if client is None:
        return None
    return client.active_sim


def _hcr_available():
    try:
        __import__("HealthcareRedux.hcr_loot")
        return True
    except Exception:
        return False


def _disease_tuning():
    manager = services.get_instance_manager(sims4.resources.Types.BUFF)
    return {key: manager.get(data[0]) for key, data in DISEASES.items()}


@sims4.commands.Command(
    "severaludo.historical_diseases",
    command_type=sims4.commands.CommandType.Live,
)
def list_historical_diseases(_connection=None):
    """List the historical profiles and the HCR mechanics backing each one."""
    out = _output(_connection)
    out("SeveralUDO Historical Diseases v{}".format(VERSION))
    out("Healthcare Redux detected: {}".format("yes" if _hcr_available() else "no"))
    tunings = _disease_tuning()
    for key in sorted(DISEASES):
        _, hcr_name, model = DISEASES[key]
        state = "ready" if tunings[key] is not None else "missing"
        out("  {} -> HCR {} ({}) [{}]".format(key, hcr_name, model, state))


@sims4.commands.Command("severaludo.hcr.status", command_type=sims4.commands.CommandType.Live)
def historical_disease_status(_connection=None):
    """Verify that the add-on, HCR module, and mapped disease tunings loaded."""
    list_historical_diseases(_connection=_connection)


@sims4.commands.Command(
    "severaludo.historical_disease",
    command_type=sims4.commands.CommandType.Live,
)
def apply_historical_disease(profile: str = "", _connection=None):
    """Apply a historical disease profile to the currently selected Sim."""
    out = _output(_connection)
    key = (profile or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key not in DISEASES:
        out("Unknown profile. Use: severaludo.historical_diseases")
        return False
    if not _hcr_available():
        out("Healthcare Redux is not loaded. Install its Core and Diseases module first.")
        return False

    sim = _active_sim(_connection)
    if sim is None:
        out("No active Sim is available.")
        return False

    buff_id, hcr_name, model = DISEASES[key]
    manager = services.get_instance_manager(sims4.resources.Types.BUFF)
    buff_type = manager.get(buff_id)
    if buff_type is None:
        out("The installed Healthcare Redux version does not contain the required {} tuning.".format(hcr_name))
        return False

    try:
        sim.add_buff(buff_type)
    except Exception as exc:
        out("Could not apply the profile: {}".format(exc))
        return False

    label = key.replace("_", " ").title()
    out("{} contracted {}. HCR will manage it as {} ({}).".format(sim.full_name, label, hcr_name, model))
    return True


@sims4.commands.Command("severaludo.hcr.apply", command_type=sims4.commands.CommandType.Live)
def apply_historical_disease_alias(profile: str = "", _connection=None):
    """Short alias for severaludo.historical_disease."""
    return apply_historical_disease(profile=profile, _connection=_connection)


@sims4.commands.Command(
    "severaludo.historical_disease.clear",
    command_type=sims4.commands.CommandType.Live,
)
def clear_historical_diseases(_connection=None):
    """Remove only the HCR disease buffs used by this extension."""
    out = _output(_connection)
    sim = _active_sim(_connection)
    if sim is None:
        out("No active Sim is available.")
        return False

    removed = 0
    manager = services.get_instance_manager(sims4.resources.Types.BUFF)
    for buff_id, _, _ in set(DISEASES.values()):
        buff_type = manager.get(buff_id)
        if buff_type is not None and sim.has_buff(buff_type):
            sim.remove_buff_by_type(buff_type)
            removed += 1
    out("Removed {} historical disease profile(s) from {}.".format(removed, sim.full_name))
    return True


@sims4.commands.Command("severaludo.hcr.clear", command_type=sims4.commands.CommandType.Live)
def clear_historical_diseases_alias(_connection=None):
    """Short alias for severaludo.historical_disease.clear."""
    return clear_historical_diseases(_connection=_connection)
