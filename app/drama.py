"""Small, optional decision-card scenes grounded in an existing save.

The deck is deliberately a writing/play aid: it never changes a Sim, a
relationship, or a rule on its own.  A completed branch becomes a single,
editable chronicle record only when the player explicitly records it.
"""

from __future__ import annotations

import random
from typing import Any

from . import core_rulesets
from .models import ChronicleSave, Record


def _card(card_id: str, title: str, category: str, opening: str,
          branches: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    return {"id": card_id, "title": title, "category": category,
            "opening": opening, "branches": branches}


def _branch(branch_id: str, label: str, beat: str,
            endings: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    return {"id": branch_id, "label": label, "beat": beat, "endings": endings}


def _ending(ending_id: str, label: str, title: str, text: str,
            *tags: str) -> dict[str, Any]:
    return {"id": ending_id, "label": label, "title": title, "text": text,
            "tags": list(tags)}


COMMON_CARDS = (
    _card(
        "sealed-letter", "The sealed letter", "Secrets",
        "A sealed letter reaches {sim} at {household}, carrying a request that cannot be answered in public.",
        (
            _branch("confide", "Confide in someone trusted", "A confidence is offered, and with it a chance for a deeper alliance.", (
                _ending("guard", "Keep the confidence", "A quiet alliance", "{sim} and a trusted ally keep the matter private. Their loyalty becomes a thread worth remembering.", "secret", "trust"),
                _ending("act", "Act on the request", "An alliance tested", "The household chooses action over caution. The request is answered, but its cost may return in another season.", "secret", "consequence"),
            )),
            _branch("hide", "Hide the letter for now", "Silence preserves peace today, but leaves the question unresolved.", (
                _ending("burn", "Destroy it", "The matter ends in ash", "{sim} burns the letter and refuses its claim on the household. Only the decision remains in the chronicle.", "secret", "closure"),
                _ending("keep", "Keep it for a later day", "A promise deferred", "The letter is hidden away. A future generation may discover that the household once hesitated.", "secret", "legacy"),
            )),
        ),
    ),
    _card(
        "strained-visit", "An unexpected visit", "Relationships",
        "An old acquaintance appears at {household} when the household has little privacy to spare. The visit places {sim} at the center of a difficult conversation.",
        (
            _branch("welcome", "Offer hospitality", "The door is opened and the household chooses grace over suspicion.", (
                _ending("reconcile", "Seek reconciliation", "A bridge rebuilt", "The visit ends with a cautious reconciliation. No promise is made, but a door once closed is no longer locked.", "relationship", "reconciliation"),
                _ending("boundaries", "Set clear boundaries", "Courtesy with limits", "The household remains civil while making its limits plain. The relationship survives, but on altered terms.", "relationship", "boundaries"),
            )),
            _branch("refuse", "Refuse the visit", "The threshold becomes a line the household is unwilling to cross.", (
                _ending("message", "Send a measured message", "Distance with dignity", "A message replaces a meeting. The household protects its peace while leaving a narrow path for a future reply.", "relationship", "distance"),
                _ending("silence", "Give no answer", "An unanswered knock", "The visitor leaves without an answer. The silence becomes its own kind of declaration.", "relationship", "estrangement"),
            )),
        ),
    ),
)


ERA_CARDS = {
    "pre-modern": (
        _card("village-rumor", "A village rumor", "Community",
              "A rumor about {sim} travels through the market before it reaches {household}. It could affect the family’s standing in the year {year}.", (
                  _branch("answer", "Answer it openly", "The household meets gossip with a public answer.", (
                      _ending("service", "Offer a visible service", "Reputation repaired", "A useful act gives the community a different story to repeat about {sim}.", "reputation", "community"),
                      _ending("witness", "Ask for a witness", "A name speaks in defense", "A respected witness speaks for {sim}; the family’s reputation is steadier, if not untouched.", "reputation", "alliance"),
                  )),
                  _branch("endure", "Let the rumor pass", "The household chooses patience and watches who repeats the tale.", (
                      _ending("fade", "Wait for it to fade", "A short-lived scandal", "The rumor loses force without a public confrontation. The household records the lesson, not the insult.", "reputation", "restraint"),
                      _ending("remember", "Remember the source", "A debt remembered", "The family takes no immediate action, but makes a private note of who was eager to spread the story.", "reputation", "grudge"),
                  )),
              )),
        _card("harvest-bargain", "The harvest bargain", "Household",
              "A neighbor offers {household} a difficult bargain at harvest time, and {sim} must decide how much uncertainty the family can accept.", (
                  _branch("share", "Share the risk", "The household ties its fortune to a neighbor’s promise.", (
                      _ending("gain", "Accept a modest gain", "A fair exchange", "The bargain brings a modest benefit and a relationship built on practical trust.", "household", "fortune"),
                      _ending("mercy", "Offer mercy instead", "A remembered kindness", "The household gives more than it receives. The choice leaves a reputation that may matter later.", "household", "generosity"),
                  )),
                  _branch("protect", "Protect the household stores", "The family chooses caution in an uncertain season.", (
                      _ending("reserve", "Keep a reserve", "Stores for winter", "The household preserves its stores. It is a quiet victory, though a neighbor may feel the refusal.", "household", "security"),
                      _ending("counteroffer", "Make a counteroffer", "Terms renegotiated", "{sim} sets new terms. The bargain survives only because the household defines its own limits.", "household", "negotiation"),
                  )),
              )),
    ),
    "industrial": (
        _card("workshop-offer", "The workshop offer", "Ambition",
              "An offer of work or training reaches {sim}. It could improve the household’s prospects, but it would change the rhythm of {household}.", (
                  _branch("pursue", "Pursue the opportunity", "Ambition asks the household to make room for a different future.", (
                      _ending("apprentice", "Begin as an apprentice", "A new trade", "{sim} begins learning a new trade. The first wage is small, but the future has widened.", "career", "ambition"),
                      _ending("relocate", "Move nearer to the work", "A changed address", "The household rearranges its life around the opportunity. The move becomes a turning point in its history.", "career", "migration"),
                  )),
                  _branch("decline", "Keep the current life", "The household values continuity over an uncertain promise.", (
                      _ending("home", "Invest at home", "A local future", "The family strengthens what it already has, choosing a familiar path over a distant opportunity.", "household", "continuity"),
                      _ending("network", "Ask for another introduction", "A door left open", "The offer is declined without closing every door. A better-fitting opportunity may still come.", "career", "network"),
                  )),
              )),
    ),
    "modern": (
        _card("public-choice", "A public choice", "Identity",
              "A public choice asks {sim} to say what they value. The decision could bring support, disagreement, or both to {household}.", (
                  _branch("speak", "Speak plainly", "{sim} chooses a clear public position.", (
                      _ending("community", "Build a coalition", "A circle of support", "The decision connects {sim} with others who share the same concern. The household gains a wider support network.", "identity", "community"),
                      _ending("cost", "Accept the social cost", "A principled stand", "The household accepts that honesty has a cost. The choice remains part of its story even if others disapprove.", "identity", "reputation"),
                  )),
                  _branch("private", "Keep the matter private", "The household protects its privacy and waits for a safer moment.", (
                      _ending("prepare", "Prepare quietly", "A careful plan", "{sim} prepares out of public view. The decision is deliberate rather than fearful.", "identity", "planning"),
                      _ending("support", "Support someone else", "Solidarity in private", "The household offers practical support without seeking attention for itself.", "identity", "support"),
                  )),
              )),
    ),
}


RULESET_CARDS = {
    core_rulesets.SEVERALUDO: (
        _card("inheritance-question", "The inheritance question", "Legacy",
              "A question of inheritance unsettles {household}. {sim} is asked to decide whether tradition or present need should guide the family.", (
                  _branch("tradition", "Follow the established custom", "The household honors a familiar rule, even where it creates hurt feelings.", (
                      _ending("formalize", "Write the terms down", "The old order recorded", "The household formalizes the decision so future arguments have a clear record.", "inheritance", "tradition"),
                      _ending("gift", "Offer a private gift", "Mercy beside tradition", "Custom remains intact, but {sim} offers quiet help to soften its consequences.", "inheritance", "mercy"),
                  )),
                  _branch("need", "Answer the present need", "The household accepts that an old custom may not solve a new problem.", (
                      _ending("reform", "Change the arrangement", "A new precedent", "The family adopts a new arrangement. Its fairness will be discussed for generations.", "inheritance", "reform"),
                      _ending("delay", "Delay the decision", "A legacy postponed", "The decision is postponed until more can be known. The uncertainty remains part of the family’s tension.", "inheritance", "uncertainty"),
                  )),
              )),
    ),
    core_rulesets.MORBID: (
        _card("lean-season", "The lean season", "Survival",
              "Resources are thin at {household}. {sim} must choose how the family will carry a difficult season without pretending the pressure is not real.", (
                  _branch("share", "Share the burden", "The household spreads the hardship so no one person carries it alone.", (
                      _ending("ration", "Set a careful ration", "A hard but shared season", "The family adopts a careful plan and makes the hardship visible rather than isolating anyone in it.", "survival", "household"),
                      _ending("help", "Ask for help", "A necessary favor", "{sim} asks for help. The household survives the season with a favor that may need to be repaid.", "survival", "debt"),
                  )),
                  _branch("risk", "Take a calculated risk", "The household stakes a little security on a possible reprieve.", (
                      _ending("venture", "Try the venture", "A risky chance", "The family attempts the risky path. Record what is gained or lost when play decides the outcome.", "survival", "risk"),
                      _ending("retreat", "Step back in time", "Caution wins", "At the last moment, the household chooses the safer course and preserves what it can.", "survival", "caution"),
                  )),
              )),
    ),
    core_rulesets.CLASSIC_2023: (
        _card("changing-decade", "A changing decade", "Modern life",
              "A new decade changes what is possible for {household}. {sim} must decide which new freedom, comfort, or expectation the family will embrace first.", (
                  _branch("embrace", "Embrace the change", "The household makes room for a new way of living.", (
                      _ending("comfort", "Choose comfort", "A more comfortable home", "The family adopts a new convenience, marking the quiet material progress of its decade.", "modernity", "household"),
                      _ending("opportunity", "Choose opportunity", "A wider horizon", "{sim} uses the change to pursue education, work, or connection beyond the household.", "modernity", "ambition"),
                  )),
                  _branch("preserve", "Keep familiar customs", "The household decides that not every new possibility is an improvement.", (
                      _ending("ritual", "Keep a family ritual", "Continuity chosen", "A family ritual is deliberately preserved, giving the household a steady point in a changing world.", "modernity", "tradition"),
                      _ending("compromise", "Adopt one small change", "A measured compromise", "The family allows one carefully chosen change without giving up the shape of its daily life.", "modernity", "compromise"),
                  )),
              )),
    ),
}


ADDON_CARDS = {
    "harry_potter_decades": (
        _card("owl-at-dusk", "The owl at dusk", "Wizarding world",
              "An owl arrives at {household} with news that asks {sim} to choose between secrecy and a wider magical obligation.", (
                  _branch("secrecy", "Protect the secret", "The household closes ranks and makes discretion its first duty.", (
                      _ending("quiet", "Handle it quietly", "A secret kept", "The matter is handled with care and little spectacle. The household remains unseen, for now.", "wizarding", "secrecy"),
                      _ending("memory", "Seek discreet help", "A careful intervention", "{sim} seeks discreet magical help, accepting the burden of a favor owed.", "wizarding", "secrecy", "debt"),
                  )),
                  _branch("seek-help", "Seek magical help", "The household chooses the support of its wider community.", (
                      _ending("allies", "Trust trusted allies", "The circle widens", "A trusted circle learns the truth and offers its protection.", "wizarding", "alliance"),
                      _ending("authority", "Notify an authority", "Under watchful eyes", "The household brings the matter to an authority and accepts the scrutiny that follows.", "wizarding", "authority"),
                  )),
              )),
    ),
    "avatar_decades": (
        _card("spirit-shrine", "The spirit shrine", "Four Nations",
              "A disturbance near a spirit shrine reaches {household}. {sim} must decide whether to seek balance quietly or ask the community to act.", (
                  _branch("listen", "Listen before acting", "The household chooses patience and seeks understanding first.", (
                      _ending("ritual", "Make a respectful offering", "A gesture of balance", "A respectful offering eases tension and reminds the household that power is not the only answer.", "spirit", "balance"),
                      _ending("guide", "Find a guide", "A wiser voice", "{sim} seeks someone with deeper knowledge before making a choice that cannot be undone.", "spirit", "guidance"),
                  )),
                  _branch("rally", "Rally the community", "The disturbance becomes a shared concern rather than a private burden.", (
                      _ending("guard", "Organize a watch", "Neighbors stand together", "The community organizes a careful watch, protecting one another without escalating the conflict.", "nation", "community"),
                      _ending("journey", "Begin a journey", "A road toward balance", "{sim} sets out to learn what has disturbed the place. The journey becomes a new story thread.", "spirit", "journey"),
                  )),
              )),
    ),
    "game_of_thrones_decades": (
        _card("raven-from-court", "A raven from court", "Westeros",
              "A raven reaches {household} with an invitation that could raise the family’s standing—or place it in someone else’s quarrel.", (
                  _branch("accept", "Accept the invitation", "The household steps closer to court and its dangers.", (
                      _ending("oath", "Offer a cautious oath", "A measured allegiance", "{sim} offers support with clear limits. The family gains notice without surrendering every choice.", "court", "allegiance"),
                      _ending("marriage", "Seek a family alliance", "An alliance proposed", "The invitation becomes an opening for a family alliance. Record any courtship or marriage only if you choose to play it out.", "court", "marriage"),
                  )),
                  _branch("decline", "Decline with courtesy", "The household refuses to be drawn into a distant struggle.", (
                      _ending("gift", "Send a diplomatic gift", "A respectful refusal", "A gift softens the refusal and keeps the household’s name from becoming an insult.", "court", "diplomacy"),
                      _ending("prepare", "Prepare the household", "A wary household", "The family stays home but quietly prepares for the consequences of being noticed.", "court", "preparedness"),
                  )),
              )),
    ),
}


ERA_DETAILS = {
    "pre-modern": ("Old World", "Court, faith, harvest, inheritance and village reputation."),
    "industrial": ("Age of Industry", "Work, migration, trade, ambition and changing households."),
    "modern": ("Modern Lives", "Public identity, privacy, opportunity and changing customs."),
}


def historical_year(save: ChronicleSave) -> int:
    return save.start_year + (max(1, int(save.global_day)) - 1) // max(1, int(save.days_per_year))


def era_key(save: ChronicleSave) -> str:
    year = historical_year(save)
    if year < 1750:
        return "pre-modern"
    if year < 1900:
        return "industrial"
    return "modern"


def deck_options(save: ChronicleSave) -> list[dict[str, Any]]:
    """Return the active generic, historical, core-rule and add-on decks."""
    current_era = era_key(save)
    core_id = core_rulesets.selected_core(save)
    selected = set((save.settings or {}).get("selected_rule_packs") or [])
    options = [
        {"id": "auto", "name": "Active deck", "description": "A draw from every theme active in this save.", "count": 0},
        {"id": "common", "name": "Household drama", "description": "Secrets, loyalties and everyday relationship pressure.", "count": len(COMMON_CARDS)},
        {"id": current_era, "name": ERA_DETAILS[current_era][0], "description": ERA_DETAILS[current_era][1], "count": len(ERA_CARDS[current_era])},
    ]
    core_entry = core_rulesets.current_catalog_entry(save)
    if core_id in RULESET_CARDS:
        options.append({"id": core_id, "name": core_entry["name"], "description": "Scenes shaped by the selected core challenge rules.", "count": len(RULESET_CARDS[core_id])})
    addon_names = {
        "harry_potter_decades": "Harry Potter Decades",
        "avatar_decades": "Avatar: The Last Airbender Decades",
        "game_of_thrones_decades": "Game of Thrones Decades",
    }
    for pack_id, name in addon_names.items():
        if pack_id in selected:
            options.append({"id": pack_id, "name": name, "description": "Optional scenes from this enabled add-on.", "count": len(ADDON_CARDS[pack_id])})
    all_cards = cards_for(save, "auto")
    options[0]["count"] = len(all_cards)
    return options


def cards_for(save: ChronicleSave, deck_id: str = "auto") -> tuple[dict[str, Any], ...]:
    current_era = era_key(save)
    core_id = core_rulesets.selected_core(save)
    selected = set((save.settings or {}).get("selected_rule_packs") or [])
    mapping: dict[str, tuple[dict[str, Any], ...]] = {
        "common": COMMON_CARDS,
        current_era: ERA_CARDS[current_era],
        core_id: RULESET_CARDS.get(core_id, ()),
    }
    mapping.update({pack_id: cards for pack_id, cards in ADDON_CARDS.items() if pack_id in selected})
    if deck_id == "auto":
        values = []
        for cards in mapping.values():
            values.extend(cards)
        return tuple(values)
    return mapping.get(deck_id, ())


def _find_card(save: ChronicleSave, card_id: str) -> dict[str, Any] | None:
    return next((card for card in cards_for(save, "auto") if card["id"] == card_id), None)


def _find_branch(card: dict[str, Any] | None, branch_id: str) -> dict[str, Any] | None:
    return next((branch for branch in (card or {}).get("branches", ()) if branch["id"] == branch_id), None)


def _find_ending(branch: dict[str, Any] | None, ending_id: str) -> dict[str, Any] | None:
    return next((ending for ending in (branch or {}).get("endings", ()) if ending["id"] == ending_id), None)


def draw_state(save: ChronicleSave, deck_id: str, sim_id: str = "", household_id: str = "",
               card_id: str = "") -> dict[str, str]:
    cards = cards_for(save, deck_id)
    if not cards:
        raise ValueError("That deck is not active for this save.")
    card = next((item for item in cards if item["id"] == card_id), None) if card_id else None
    card = card or random.SystemRandom().choice(cards)
    return {"deck_id": deck_id, "card_id": card["id"], "sim_id": str(sim_id or ""),
            "household_id": str(household_id or "")}


def choose_branch(save: ChronicleSave, state: dict[str, Any], branch_id: str) -> dict[str, str]:
    card = _find_card(save, str(state.get("card_id") or ""))
    if not _find_branch(card, branch_id):
        raise ValueError("That first decision is not available for this card.")
    return {**{key: str(value or "") for key, value in state.items() if key in {"deck_id", "card_id", "sim_id", "household_id"}}, "branch_id": branch_id}


def choose_ending(save: ChronicleSave, state: dict[str, Any], ending_id: str) -> dict[str, str]:
    card = _find_card(save, str(state.get("card_id") or ""))
    branch = _find_branch(card, str(state.get("branch_id") or ""))
    if not _find_ending(branch, ending_id):
        raise ValueError("That conclusion is not available for this decision.")
    return {**{key: str(value or "") for key, value in state.items() if key in {"deck_id", "card_id", "sim_id", "household_id", "branch_id"}}, "ending_id": ending_id}


def _text(value: str, facts: dict[str, str]) -> str:
    try:
        return str(value or "").format(**facts)
    except (KeyError, ValueError):
        return str(value or "")


def build_state(save: ChronicleSave, sims: list[Record], households: list[Record],
                state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resolve compact session state into safe, display-ready card copy."""
    if not isinstance(state, dict):
        return None
    card = _find_card(save, str(state.get("card_id") or ""))
    if not card:
        return None
    sim_by_id = {item.id: item for item in sims}
    household_by_id = {item.id: item for item in households}
    sim = sim_by_id.get(str(state.get("sim_id") or ""))
    household = household_by_id.get(str(state.get("household_id") or ""))
    if household is None and sim:
        household = household_by_id.get(str((sim.data or {}).get("current_household_id") or ""))
    facts = {
        "sim": sim.label if sim else "someone in the household",
        "household": household.label if household else "the household",
        "year": str(historical_year(save)),
    }
    branch = _find_branch(card, str(state.get("branch_id") or ""))
    ending = _find_ending(branch, str(state.get("ending_id") or ""))
    return {
        "state": {key: str(value or "") for key, value in state.items()},
        "card": {**card, "opening": _text(card["opening"], facts)},
        "branch": ({**branch, "beat": _text(branch["beat"], facts)} if branch else None),
        "ending": ({**ending, "text": _text(ending["text"], facts)} if ending else None),
        "sim": sim, "household": household, "facts": facts,
    }


def scene_data(resolved: dict[str, Any]) -> dict[str, Any]:
    """Create the deliberately non-mechanical record payload for a conclusion."""
    card, branch, ending = resolved["card"], resolved["branch"], resolved["ending"]
    if not branch or not ending:
        raise ValueError("Finish both choices before recording the scene.")
    pieces = (card["opening"], branch["beat"], ending["text"])
    return {
        "source": "Drama Deck",
        "category": card["category"],
        "deck_id": resolved["state"].get("deck_id", "auto"),
        "card_id": card["id"], "card_title": card["title"],
        "branch_id": branch["id"], "branch_label": branch["label"],
        "ending_id": ending["id"], "ending_label": ending["label"],
        "sim_id": resolved["sim"].id if resolved["sim"] else None,
        "sim_name": resolved["sim"].label if resolved["sim"] else "",
        "household_id": resolved["household"].id if resolved["household"] else None,
        "household_name": resolved["household"].label if resolved["household"] else "",
        "body": " ".join(piece for piece in pieces if piece),
        "tags": list(ending.get("tags") or []),
        "player_decision": True,
        "mechanical_effects": "None — this scene is a voluntary chronicle decision.",
    }
