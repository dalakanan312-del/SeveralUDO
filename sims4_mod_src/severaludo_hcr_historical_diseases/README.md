# SeveralUDO Historical Diseases for Healthcare Redux 0.4.0

Unofficial, version-bound extension for **a.deep.indigo's Healthcare Redux**.
It does not include or modify Healthcare Redux files. Healthcare Redux Core and
the Diseases module must remain installed.

## Included historical profiles

| Profile | Healthcare Redux mechanics used |
| --- | --- |
| Plague | Meningitis / deadly systemic disease |
| Smallpox | Tuberculosis / long severe disease |
| Cholera | Gastroenteritis / acute enteric disease |
| Typhus | Malaria / severe fever disease |
| Dysentery | Gastroenteritis / acute enteric disease |
| Scarlet fever | Tonsillitis / throat infection |

The historical label is a role-play profile; Healthcare Redux remains in charge
of symptoms, stages, diagnosis, treatment, recovery, immunity, and mortality.
The backing model is always disclosed in game and in this table.

## Installation

1. Install the current Healthcare Redux Core and Diseases module.
2. Put both `SeveralUDO_HCR_Historical_Diseases.ts4script` and
   `SeveralUDO_HCR_Historical_Diseases.package` no more than one folder deep
   inside the Sims 4 `Mods` folder.
3. Install XML Injector v4.2. Do not distribute or rename its script file.
4. Enable **Script Mods Allowed**, then restart the game.

## Sim pie menu

Click any Sim and choose one of the `SeveralUDO:` actions to apply Plague,
Smallpox, Cholera, Typhus, Dysentery, or Scarlet Fever to that specific Sim.
`SeveralUDO: Clear Historical Diseases` removes the mapped disease buffs. These
actions are intended for easy testing and are never selected autonomously.

Each action also adds a visible **Uncomfortable +2** historical symptom
moodlet with disease-specific wording. Healthcare Redux continues to control
the underlying symptoms, diagnosis, treatment, progression, and mortality.
The clear action removes both the HCR disease profile and its historical
moodlet.

## Commands

Open the cheat console with `Ctrl+Shift+C`. First run `severaludo.hcr.status`.
It should show version 0.4.0, confirm Healthcare Redux was detected, and mark
each disease `ready`. If the cheat produces no output, verify that both **Script
Mods Allowed** and **Custom Content and Mods** are enabled, then restart the
game.

- `severaludo.historical_diseases` — list profiles and verify HCR was detected.
- `severaludo.historical_disease plague` — apply a profile to the selected Sim.
- `severaludo.historical_disease scarlet_fever` — underscores or spaces work.
- `severaludo.historical_disease.clear` — remove profiles applied by the add-on.
- `severaludo.hcr.apply plague` — shorter apply command.
- `severaludo.hcr.clear` — shorter clear command.

This is the compatibility-first test release. A later release can add a native
pie-menu chooser and optional automatic outbreaks after the installed HCR build
has been verified in game.

## Compatibility and safety

- Built against Healthcare Redux installed in August 2026.
- Does not alter saves, databases, or original mod packages.
- Remove this add-on before updating HCR, then reinstall after compatibility is
  confirmed.
- Back up important Sims 4 saves before testing any gameplay mod.

Healthcare Redux is owned by its creator. This add-on is not endorsed by or
affiliated with a.deep.indigo.
