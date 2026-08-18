"""SeveralUDO Historical Diseases -- an unofficial Healthcare Redux extension.

This module deliberately contains no Healthcare Redux assets or source code.  It
looks up public game tuning resources supplied by an installed copy of HCR and
asks HCR to handle the actual illness once a historical profile is selected.
"""

import sims4.commands
import sims4.resources
import services
from interactions.base.immediate_interaction import ImmediateSuperInteraction
from sims4.localization import LocalizationHelperTuning


VERSION = "0.4.0"

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

HISTORICAL_MOODLETS = {
    "plague": 0xED0927480477A9CA,
    "smallpox": 0xF7A64864C26434C6,
    "cholera": 0xC331FDB990F87CDE,
    "typhus": 0x6BB3B4BD6227451B,
    "dysentery": 0xF27E27596C6931B5,
    "scarlet_fever": 0xF18C41F70F4A4E48,
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
    return {
        key: (manager.get(data[0]), manager.get(HISTORICAL_MOODLETS[key]))
        for key, data in DISEASES.items()
    }


def _apply_profile_to_sim(sim, key):
    """Apply one mapped HCR disease to a specific Sim object."""
    if sim is None or key not in DISEASES or not _hcr_available():
        return False
    buff_id, _, _ = DISEASES[key]
    buff_type = services.get_instance_manager(sims4.resources.Types.BUFF).get(buff_id)
    if buff_type is None:
        return False
    sim.add_buff(buff_type)
    moodlet_type = services.get_instance_manager(sims4.resources.Types.BUFF).get(
        HISTORICAL_MOODLETS[key]
    )
    if moodlet_type is not None:
        sim.add_buff(moodlet_type)
    return True


class _HistoricalDiseasePieInteraction(ImmediateSuperInteraction):
    """Base for lightweight Sim pie-menu testing actions."""

    profile = None
    menu_label = "SeveralUDO Historical Diseases"

    @classmethod
    def _get_name(cls, inst, target, context, **interaction_parameters):
        return LocalizationHelperTuning.get_raw_text(cls.menu_label)

    def _run_interaction_gen(self, timeline):
        if False:
            yield None
        return _apply_profile_to_sim(self.target, self.profile)


class ApplyPlagueInteraction(_HistoricalDiseasePieInteraction):
    profile = "plague"
    menu_label = "SeveralUDO: Apply Plague"


class ApplySmallpoxInteraction(_HistoricalDiseasePieInteraction):
    profile = "smallpox"
    menu_label = "SeveralUDO: Apply Smallpox"


class ApplyCholeraInteraction(_HistoricalDiseasePieInteraction):
    profile = "cholera"
    menu_label = "SeveralUDO: Apply Cholera"


class ApplyTyphusInteraction(_HistoricalDiseasePieInteraction):
    profile = "typhus"
    menu_label = "SeveralUDO: Apply Typhus"


class ApplyDysenteryInteraction(_HistoricalDiseasePieInteraction):
    profile = "dysentery"
    menu_label = "SeveralUDO: Apply Dysentery"


class ApplyScarletFeverInteraction(_HistoricalDiseasePieInteraction):
    profile = "scarlet_fever"
    menu_label = "SeveralUDO: Apply Scarlet Fever"


class ClearHistoricalDiseasesInteraction(_HistoricalDiseasePieInteraction):
    menu_label = "SeveralUDO: Clear Historical Diseases"

    def _run_interaction_gen(self, timeline):
        if False:
            yield None
        sim = self.target
        if sim is None:
            return False
        manager = services.get_instance_manager(sims4.resources.Types.BUFF)
        for buff_id, _, _ in set(DISEASES.values()):
            buff_type = manager.get(buff_id)
            if buff_type is not None and sim.has_buff(buff_type):
                sim.remove_buff_by_type(buff_type)
        for buff_id in set(HISTORICAL_MOODLETS.values()):
            buff_type = manager.get(buff_id)
            if buff_type is not None and sim.has_buff(buff_type):
                sim.remove_buff_by_type(buff_type)
        return True


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
        hcr_tuning, moodlet_tuning = tunings[key]
        state = "ready" if hcr_tuning is not None and moodlet_tuning is not None else "missing"
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
        _apply_profile_to_sim(sim, key)
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
    for buff_id in set(HISTORICAL_MOODLETS.values()):
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
