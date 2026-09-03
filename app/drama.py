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


MINIMUM_DECK_CARDS = 20


def _supporting_card(prefix: str, slug: str, title: str, category: str, opening: str,
                     *tags: str) -> dict[str, Any]:
    """Create a fully playable, deliberately non-mechanical scene card.

    The short hand-authored prompt lists below keep every deck broad enough
    for repeat play without turning the Drama Deck into an automated rules
    engine.  Each prompt still has the same two-step decision structure as
    the original hand-written cards.
    """
    card_tags = tuple(dict.fromkeys((category.casefold().replace(" ", "-"), *tags)))
    return _card(
        f"{prefix}-{slug}", title, category, opening,
        (
            _branch("meet", "Meet it directly", "{sim} chooses a visible response and accepts that the household will be remembered for it.", (
                _ending("allies", "Gather support", f"{title}: a shared path", "{sim} brings trusted people into the decision. The household carries the consequence together rather than in silence.", *card_tags, "alliance"),
                _ending("terms", "Set clear terms", f"{title}: terms made plain", "The household responds, but on its own terms. The decision gives the family a boundary to return to later.", *card_tags, "boundaries"),
            )),
            _branch("guard", "Take the quieter path", "{sim} protects the household's privacy while considering what can safely wait.", (
                _ending("wait", "Watch and wait", f"{title}: patience chosen", "The household gives the situation time to reveal more of itself. The pause is a choice, not an absence of one.", *card_tags, "restraint"),
                _ending("limit", "Draw a boundary", f"{title}: peace protected", "The family declines to let the matter grow larger than it needs to be. A clear limit preserves its peace for now.", *card_tags, "household"),
            )),
        ),
    )


def _supporting_cards(prefix: str, prompts: tuple[tuple[str, str, str, str, tuple[str, ...]], ...]) -> tuple[dict[str, Any], ...]:
    return tuple(_supporting_card(prefix, slug, title, category, opening, *tags)
                 for slug, title, category, opening, tags in prompts)


# Every selectable deck gets a full twenty-card set.  These prompts are kept
# as structured source data so a future rule pack can add a themed deck without
# needing new page or decision-tree code.
_COMMON_PROMPTS = (
    ("missing-key", "The missing key", "Secrets", "A key that should have been on the household ring is missing, and {sim} notices before anyone else does.", ("trust",)),
    ("borrowed-coin", "The borrowed coin", "Trust", "A small loan between relatives has become awkward just as {household} needs every favor to remain clear.", ("family", "debt")),
    ("unspoken-invitation", "The unspoken invitation", "Relationships", "An invitation arrives for everyone except one person at {household}, leaving {sim} to decide whether to name the slight.", ("relationship",)),
    ("closed-room", "The room kept closed", "Legacy", "A room at {household} has been left untouched for years, but a practical need now asks {sim} to open it.", ("memory", "legacy")),
    ("neighbours-favor", "A neighbour's favor", "Community", "A neighbour asks for help at an inconvenient moment, and {sim} knows the answer will be remembered.", ("community",)),
    ("cousins-claim", "A cousin's claim", "Legacy", "A distant cousin raises an old claim about something the family has long treated as its own.", ("inheritance",)),
    ("cracked-keepsake", "The cracked keepsake", "Memory", "A treasured family object is damaged in a small accident, exposing a disagreement about what it represents.", ("legacy", "grief")),
    ("young-request", "A young Sim's request", "Family", "Someone younger in the family asks {sim} for permission that will change how they are seen at {household}.", ("coming-of-age",)),
    ("late-call", "The late call", "Family", "News reaches {household} after dark, and {sim} must decide who should hear it before morning.", ("news", "family")),
    ("child-rumor", "The children's rumor", "Community", "A story among the children has reached the adults and now touches the standing of {household}.", ("reputation",)),
    ("disputed-chore", "The disputed chore", "Household", "An ordinary responsibility has become a symbol of who is expected to carry the household's invisible work.", ("fairness",)),
    ("promise-returned", "A promise returned", "Trust", "Someone calls in a promise {sim} made long ago, before the situation at {household} had changed.", ("promise",)),
    ("old-photograph", "The old photograph", "Memory", "An old photograph turns up with a face no one at {household} can immediately place.", ("history", "legacy")),
    ("birthday-omission", "The birthday omission", "Relationships", "A meaningful birthday passes without the gesture one person quietly expected from {household}.", ("hurt", "relationship")),
    ("shared-garden", "The shared garden", "Household", "A shared patch of land or garden needs more care than anyone planned to give it this season.", ("work", "community")),
    ("unexpected-gift", "The unexpected gift", "Secrets", "A costly gift arrives with no clear sender and no explanation for why it was sent to {sim}.", ("mystery",)),
    ("family-recipe", "The family recipe", "Legacy", "A family tradition is about to be changed, and the argument reveals whose memories are treated as important.", ("tradition",)),
    ("empty-chair", "The empty chair", "Grief", "At a familiar gathering, an empty chair makes a loss or absence impossible for {household} to ignore.", ("grief", "remembrance")),
)

_PRE_MODERN_PROMPTS = (
    ("tithe-dispute", "The tithe dispute", "Faith", "A dispute over a tithe or offering places {household} between conscience, custom, and a powerful neighbour.", ("faith", "reputation")),
    ("guild-fee", "The guild fee", "Trade", "A guild fee comes due sooner than expected, and {sim} must decide whether belonging is worth the immediate cost.", ("trade", "money")),
    ("midwifes-warning", "The midwife's warning", "Family", "A midwife offers a warning that the household cannot easily dismiss, though following it will inconvenience everyone.", ("care", "family")),
    ("chapel-request", "The chapel request", "Community", "The chapel asks {household} for a visible contribution at precisely the moment its stores are uncertain.", ("faith", "community")),
    ("market-debt", "The market debt", "Trade", "A market debt is remembered by someone with a loud voice and a very public stall.", ("debt", "market")),
    ("border-toll", "The border toll", "Travel", "A new toll or gatekeeper's demand changes a journey {sim} had promised to make.", ("travel", "authority")),
    ("winter-guest", "The winter guest", "Hospitality", "A traveler asks for shelter as winter closes in, asking {household} to measure generosity against risk.", ("hospitality", "winter")),
    ("cart-in-mud", "The cart in the mud", "Household", "A heavily laden cart is stuck on the road, and helping will cost {household} a scarce day of work.", ("labor", "community")),
    ("feast-day-favor", "A feast-day favor", "Community", "During a public feast, someone asks {sim} for a favor that cannot be granted without witnesses.", ("festival", "favor")),
    ("lords-messenger", "The lord's messenger", "Authority", "A messenger arrives with an instruction for {household} that is lawful, inconvenient, and not entirely fair.", ("authority", "duty")),
    ("harvest-blight", "The harvest blight", "Survival", "A worrying mark appears in the harvest, and {sim} must decide how widely to share the concern.", ("harvest", "survival")),
    ("missing-apprentice", "The missing apprentice", "Trade", "An apprentice fails to return at the expected hour, leaving {household} to decide who should be told first.", ("apprentice", "responsibility")),
    ("land-boundary", "The land boundary", "Legacy", "A marker stone has shifted—or someone says it has—and an old boundary becomes newly important.", ("land", "legacy")),
    ("river-crossing", "The river crossing", "Travel", "The river is unsafe, but a family commitment waits on the other side of it.", ("journey", "risk")),
    ("travelling-player", "The travelling player", "Culture", "A travelling performer offers entertainment and unsettling news from beyond the village.", ("news", "culture")),
    ("family-relic", "The family relic", "Legacy", "A family relic could solve a present problem if sold, but doing so would end a much older promise.", ("heirloom", "inheritance")),
    ("monastery-refuge", "The monastery refuge", "Faith", "A nearby refuge offers help with conditions that make {sim} uneasy.", ("faith", "shelter")),
    ("village-watch", "The village watch", "Community", "The village asks {household} to contribute a night to the watch when fatigue is already high.", ("duty", "community")),
)

_INDUSTRIAL_PROMPTS = (
    ("factory-whistle", "The factory whistle", "Work", "A new shift pattern promises wages but would change the household's entire rhythm.", ("work", "labor")),
    ("railway-ticket", "The railway ticket", "Migration", "A railway ticket arrives from someone who says there is room for {sim} in another town.", ("railway", "migration")),
    ("union-leaflet", "The union leaflet", "Work", "A leaflet is slipped under the door at {household}, asking a worker to take a public position.", ("union", "work")),
    ("boarding-room", "The boarding-house room", "Household", "A temporary room becomes available near work, but accepting it would divide the household across two addresses.", ("housing", "migration")),
    ("night-school-form", "The night-school form", "Education", "An evening class could open a new future for {sim}, but the household would need to rearrange care and work.", ("education", "ambition")),
    ("machine-injury", "The machine injury", "Work", "A workplace accident makes the household reconsider what safety, duty, and wages are worth.", ("work", "care")),
    ("foremans-favor", "The foreman's favor", "Ambition", "A supervisor offers {sim} a small advantage that may be resented by fellow workers.", ("career", "ethics")),
    ("remittance", "The remittance", "Migration", "Money arrives from a relative far away, along with a request that cannot be answered cheaply.", ("money", "family")),
    ("strike-fund", "The strike fund", "Community", "A strike fund needs support just as {household} is beginning to feel financially steady.", ("union", "solidarity")),
    ("newspaper-photograph", "The newspaper photograph", "Reputation", "A photograph or quotation places someone from {household} in the local paper without warning.", ("press", "reputation")),
    ("electric-light", "The electric light", "Modernity", "A chance to bring a new convenience into {household} becomes an argument about cost and change.", ("technology", "household")),
    ("telephone-call", "The telephone call", "Family", "A call brings urgent news from too far away to answer in person immediately.", ("communication", "family")),
    ("shopfront-lease", "The shopfront lease", "Ambition", "A small shopfront could be rented, but the lease would make the household's gamble very visible.", ("business", "risk")),
    ("dockside-rumor", "The dockside rumor", "Community", "A rumor from the docks or station reaches {household} before the person it concerns can explain it.", ("travel", "reputation")),
    ("city-cousin", "The cousin in the city", "Migration", "A city cousin asks {sim} to sponsor a move, bringing hope and responsibility together.", ("migration", "family")),
    ("motorcar-offer", "The motorcar offer", "Modernity", "An unfamiliar new machine promises independence but asks the household to trust something no one fully understands.", ("technology", "change")),
    ("mill-closure", "The mill closure", "Work", "Talk of a closure forces {household} to think beyond next week's wages.", ("work", "security")),
    ("library-card", "The library card", "Education", "Access to books and evening lectures offers {sim} a private route toward a larger life.", ("education", "aspiration")),
    ("civic-meeting", "The civic meeting", "Community", "A civic meeting asks ordinary households to take a position on a change close to home.", ("civic", "community")),
)

_MODERN_PROMPTS = (
    ("group-chat", "The group chat", "Relationships", "A private message thread becomes tense when {sim} realizes a decision was made without them.", ("communication", "relationship")),
    ("viral-post", "The viral post", "Reputation", "Something linked to {household} begins circulating beyond its intended audience.", ("social", "reputation")),
    ("scholarship", "The scholarship", "Education", "A scholarship or training opportunity asks {sim} to decide whether a larger future is worth a difficult departure.", ("education", "ambition")),
    ("lease-renewal", "The lease renewal", "Household", "A housing decision puts comfort, stability, and the household budget in direct conversation.", ("home", "security")),
    ("protest-invitation", "The invitation to act", "Identity", "Someone asks {sim} to join a public cause that matters to the household in different ways.", ("civic", "identity")),
    ("reunion", "The reunion", "Family", "A reunion offers joy and old tension in equal measure, and {sim} must decide how much history to reopen.", ("family", "memory")),
    ("job-offer", "The competing job offer", "Ambition", "A new opportunity arrives just as {household} depends on the stability of the current one.", ("career", "work")),
    ("anonymous-review", "The anonymous review", "Reputation", "An anonymous comment stings someone at {household}, but responding might give it more power.", ("reputation", "privacy")),
    ("lost-phone", "The lost phone", "Secrets", "A lost device could expose conversations {sim} never intended to share.", ("privacy", "trust")),
    ("community-fund", "The community fundraiser", "Community", "A local effort needs more than a donation: it needs someone from {household} to be seen supporting it.", ("community", "service")),
    ("care-schedule", "The care schedule", "Family", "A loved one's care needs a new schedule, revealing which responsibilities the family treats as negotiable.", ("care", "family")),
    ("blended-holiday", "The blended holiday", "Family", "Two sets of traditions collide as {household} tries to plan a celebration that feels fair.", ("tradition", "family")),
    ("creative-opportunity", "The creative opportunity", "Ambition", "A creative project asks {sim} to risk being seen before they feel ready.", ("creative", "ambition")),
    ("appointment", "The difficult appointment", "Care", "An important appointment needs support, but everyone at {household} has a different idea of what support means.", ("care", "communication")),
    ("travel-plan", "The travel plan", "Travel", "A rare chance to travel conflicts with a commitment that cannot easily move.", ("travel", "choice")),
    ("online-discovery", "The online discovery", "Legacy", "A digital search uncovers a fragment of family history that someone may prefer to leave unexamined.", ("history", "legacy")),
    ("neighborhood-petition", "The neighborhood petition", "Community", "A petition asks {sim} to choose between preserving the familiar and welcoming a change.", ("civic", "home")),
    ("public-comment", "The public comment", "Identity", "A public conversation makes silence feel like a choice in itself for {sim}.", ("identity", "voice")),
    ("time-capsule", "The time capsule", "Legacy", "The household is asked what it wants a future generation to know about this exact moment.", ("legacy", "memory")),
)

_SEVERALUDO_PROMPTS = (
    ("heirs-duty", "The heir's duty", "Legacy", "A family responsibility falls to the expected heir before they feel ready to carry it.", ("heir", "succession")),
    ("dowry-question", "The dowry question", "Marriage", "A proposed match brings a difficult conversation about what support, status, and fairness should mean.", ("marriage", "dowry")),
    ("household-claim", "The household claim", "Legacy", "A household member argues that the old succession plan no longer reflects present need.", ("succession", "family")),
    ("saved-letter", "The saved letter", "Legacy", "A saved letter could settle an inheritance question—or reopen a relationship everyone assumed was finished.", ("inheritance", "secret")),
    ("guardian-choice", "The guardian choice", "Family", "The family must name who would protect a vulnerable member if circumstances change suddenly.", ("guardianship", "care")),
    ("remarriage-whisper", "The remarriage whisper", "Marriage", "A possible remarriage is discussed before the person at its center has decided what they want.", ("remarriage", "reputation")),
    ("birthright-gift", "The birthright gift", "Legacy", "A traditional gift is ready to pass to the next generation, but the chosen recipient surprises {household}.", ("birthright", "heirloom")),
    ("family-plan", "The family plan", "Family", "A long-range family plan forces {sim} to name which hopes are private wishes and which are real commitments.", ("family-plan", "planning")),
    ("estate-repair", "The estate repair", "Household", "A needed repair competes with funds the household hoped to reserve for the next generation.", ("estate", "money")),
    ("kinship-request", "The kinship request", "Family", "A relative beyond the immediate household asks for help that could draw the family into a longer obligation.", ("kinship", "duty")),
    ("surname-decision", "The surname decision", "Identity", "A change of name brings up questions about belonging, marriage, and what the family wants to carry forward.", ("surname", "identity")),
    ("heirloom-sale", "The heirloom sale", "Legacy", "An heirloom could ease a present hardship, but selling it would make a symbolic loss permanent.", ("heirloom", "money")),
    ("birthplace-promise", "The birthplace promise", "Family", "A promise about where a child should begin life runs into the practical limits facing {household}.", ("birth", "location")),
    ("succession-council", "The succession council", "Succession", "Family members want a clearer account of the future, and {sim} must decide how much to reveal.", ("succession", "transparency")),
    ("memory-box", "The memory box", "Legacy", "A memory box holds evidence of earlier sacrifices the household has never properly discussed.", ("memory", "legacy")),
    ("family-portrait", "The family portrait", "Legacy", "A portrait session asks the household to decide who belongs in the picture—and what story it should tell.", ("portrait", "family")),
    ("separate-home", "The separate home", "Household", "A growing branch of the family wants a home of its own without breaking the bonds it depends upon.", ("household", "independence")),
    ("legacy-account", "The legacy account", "Legacy", "The family must decide whether to preserve a resource for descendants or use it to solve a current crisis.", ("legacy", "finance")),
    ("elder-request", "The elder's request", "Family", "An elder names a wish for the family that does not neatly fit the existing plan.", ("elder", "care")),
)

_MORBID_PROMPTS = (
    ("drought-field", "The drought field", "Survival", "A field or garden is failing early, and {household} must decide how much of its security to spend now.", ("drought", "survival")),
    ("tavern-debt", "The tavern debt", "Survival", "A debt comes due at a time when the household cannot afford a public loss of standing.", ("debt", "survival")),
    ("winter-stores", "The winter stores", "Survival", "The household's winter stores do not look as deep as they did last week.", ("winter", "rationing")),
    ("dangerous-road", "The dangerous road", "Risk", "A journey could bring essential supplies, but the road is known to be unsafe this season.", ("travel", "risk")),
    ("neighbours-hunger", "The hungry neighbours", "Community", "Another household is struggling visibly, and {sim} knows help may place their own family at risk.", ("hunger", "community")),
    ("empty-net", "The empty net", "Survival", "A familiar source of food or income comes up empty, asking {household} to revise its plans quickly.", ("food", "survival")),
    ("fever-rumor", "The fever rumor", "Health", "A troubling rumor causes neighbours to avoid one another before anyone knows the full truth.", ("health", "fear")),
    ("lost-livestock", "The lost livestock", "Survival", "Something valuable to the household has gone missing, and blame is beginning to settle too easily.", ("livestock", "loss")),
    ("shelter-request", "The shelter request", "Survival", "A displaced person needs a place to sleep when {household} has little spare room.", ("shelter", "compassion")),
    ("failed-venture", "The failed venture", "Risk", "A calculated risk has not paid off as hoped, and {sim} must decide what to protect next.", ("risk", "finance")),
    ("ration-book", "The ration book", "Survival", "The household must make its first careful accounting of what can last through the difficult season.", ("rationing", "planning")),
    ("work-exhaustion", "The work exhaustion", "Care", "Someone at {household} is carrying too much work, but there is no obvious way to redistribute it.", ("labor", "care")),
    ("burial-cost", "The burial cost", "Grief", "A loss leaves the household with both grief and a practical cost that must be met.", ("grief", "debt")),
    ("storm-damage", "The storm damage", "Survival", "A storm damages something {household} depends upon, and help will not arrive quickly.", ("weather", "repair")),
    ("distant-favor", "The distant favor", "Debt", "An old favor could solve today's problem, but it would bind the household to someone far away.", ("debt", "obligation")),
    ("shared-ration", "The shared ration", "Community", "A proposal to pool supplies promises fairness but gives up private control.", ("rationing", "community")),
    ("last-seed", "The last seed", "Survival", "The household has a small reserve that could be used now or saved for a future no one can guarantee.", ("seed", "future")),
    ("hard-bargain", "The hard bargain", "Survival", "A necessary exchange is offered on terms that feel cruel but may be the only available terms.", ("trade", "survival")),
    ("watch-at-night", "The watch at night", "Risk", "A new danger asks the household to lose sleep at the very moment exhaustion is most dangerous.", ("danger", "vigil")),
)

_CLASSIC_PROMPTS = (
    ("new-appliance", "The new appliance", "Modern life", "A new household convenience becomes a decision about debt, comfort, and what counts as progress.", ("technology", "home")),
    ("school-counsel", "The school counsel", "Education", "A teacher or adviser suggests a path for {sim} that the household had not previously considered.", ("education", "future")),
    ("career-restriction", "The career restriction", "Work", "A desired job clashes with the current decade's expectations and the family's practical needs.", ("career", "rules")),
    ("radio-message", "The radio message", "Community", "News or a broadcast changes the conversation at {household} before anyone has time to process it privately.", ("radio", "history")),
    ("neighbourhood-watch", "The neighbourhood watch", "Community", "A local concern asks the household to be publicly involved when it would rather stay private.", ("community", "civic")),
    ("war-letter", "The war letter", "Family", "A letter from afar brings uncertainty that cannot be resolved by waiting beside the mailbox.", ("war", "family")),
    ("victory-garden", "The victory garden", "Household", "A household project becomes a test of who has time, skill, and patience to keep it going.", ("garden", "war-effort")),
    ("household-budget", "The household budget", "Finance", "A changing economy forces the household to name what it can no longer treat as automatic.", ("money", "budget")),
    ("newspaper-editorial", "The newspaper editorial", "Identity", "An editorial stirs debate at {household} about whether private disagreement should become public action.", ("press", "identity")),
    ("television-evening", "The television evening", "Modern life", "A new form of entertainment changes the household's shared time and someone feels left out of the choice.", ("television", "family")),
    ("holiday-custom", "The holiday custom", "Tradition", "A changing holiday tradition raises a quiet question about which customs still belong to the family.", ("holiday", "tradition")),
    ("housing-loan", "The housing loan", "Finance", "A loan could make a stable home possible, but it would make the household's future feel less certain.", ("housing", "debt")),
    ("community-dance", "The community dance", "Relationships", "A public social event offers a welcome opening—and the possibility of a public misunderstanding.", ("social", "relationship")),
    ("new-car", "The new car", "Modern life", "A new form of travel promises freedom but rearranges the household budget and daily habits.", ("travel", "technology")),
    ("civil-rights-conversation", "The difficult conversation", "Identity", "A changing public world reaches the household table and makes neutrality feel less simple.", ("identity", "history")),
    ("environmental-choice", "The environmental choice", "Community", "A local change asks the household to balance convenience with the world it wants future generations to inherit.", ("environment", "legacy")),
    ("computer-at-home", "The computer at home", "Modern life", "A new device opens unexpected opportunities and new questions about time, privacy, and access.", ("computer", "technology")),
    ("millennium-plan", "The millennium plan", "Legacy", "A calendar milestone makes {household} reflect on what it wants to carry into the next era.", ("future", "legacy")),
    ("pandemic-distance", "The season apart", "Community", "A period of distance changes how the household maintains care, work, and connection.", ("community", "care")),
)

_HARRY_POTTER_PROMPTS = (
    ("wand-repair", "The wand repair", "Wizarding world", "A damaged wand or magical tool requires help that may reveal more than {sim} wishes to share.", ("magic", "secrecy")),
    ("platform-goodbye", "The platform goodbye", "Wizarding world", "A journey to school or a distant magical obligation asks the household to say goodbye before it feels ready.", ("hogwarts", "family")),
    ("library-restriction", "The restricted book", "Wizarding world", "A magical text offers useful knowledge, but following it could cross a boundary {sim} has never tested.", ("knowledge", "magic")),
    ("ministry-letter", "The Ministry letter", "Wizarding world", "An official letter asks {household} to explain something it would rather keep within the family.", ("ministry", "authority")),
    ("muggle-question", "The Muggle question", "Wizarding world", "A question from the non-magical world makes secrecy feel both necessary and painful.", ("muggle", "secrecy")),
    ("house-rivalry", "The house rivalry", "Hogwarts", "A school rivalry follows {sim} home in the form of a friendship that others distrust.", ("hogwarts", "friendship")),
    ("missing-potion", "The missing potion", "Wizarding world", "A missing potion ingredient invites quick blame, but the truth may be more complicated.", ("potion", "trust")),
    ("family-owl", "The family owl", "Wizarding world", "An owl carries a message meant for someone else at {household}, and {sim} must decide what to do with it.", ("owl", "message")),
    ("quiet-spell", "The quiet spell", "Magic", "A small spell solves an everyday problem but risks attracting the wrong kind of attention.", ("magic", "risk")),
    ("pureblood-expectation", "The family expectation", "Identity", "An inherited expectation about blood, status, or belonging does not fit the life {sim} wants to lead.", ("identity", "blood-status")),
    ("diagon-offer", "The Diagon Alley offer", "Wizarding world", "A shopkeeper offers an unusual opportunity that could bring the household closer to a wider magical world.", ("diagon-alley", "opportunity")),
    ("patronus-practice", "The Patronus practice", "Magic", "A private magical practice reveals something about what {sim} is willing to protect.", ("patronus", "courage")),
    ("hidden-room", "The hidden room", "Wizarding world", "A concealed room or passage is discovered near the household, and no one agrees about who should know.", ("secret", "magic")),
    ("spellcaster-child", "The first sign of magic", "Family", "A young family member shows an unexpected sign of magic, bringing pride and new responsibility.", ("child", "magic")),
    ("quidditch-choice", "The Quidditch choice", "Hogwarts", "A chance to play or support a public school activity conflicts with a quieter household commitment.", ("quidditch", "school")),
    ("old-order-token", "The old token", "Legacy", "An old magical token links the family to a history it has never fully explained.", ("legacy", "wizarding")),
    ("protective-charm", "The protective charm", "Magic", "A charm or ward needs renewal, asking {household} to decide who is truly inside its circle of trust.", ("protection", "magic")),
    ("forbidden-forest", "The edge of the forest", "Risk", "A tempting shortcut crosses a boundary everyone has been taught not to test.", ("risk", "hogwarts")),
    ("wizarding-gossip", "Wizarding gossip", "Reputation", "A magical rumor spreads faster than an owl can correct it, and {sim} must decide whether to answer.", ("reputation", "wizarding")),
)

_AVATAR_PROMPTS = (
    ("village-lesson", "The village lesson", "Four Nations", "A local teacher offers {sim} a lesson that may change how the community sees their place in it.", ("learning", "community")),
    ("bending-display", "The public display", "Bending", "A chance to demonstrate bending or skill in public brings pride and unwanted attention together.", ("bending", "reputation")),
    ("border-pass", "The border pass", "Travel", "A pass or permit could open a route for {household}, but accepting it means owing someone a favor.", ("travel", "authority")),
    ("spirit-offering", "The spirit offering", "Spirits", "A customary offering is neglected, and small signs suggest the balance around {household} has shifted.", ("spirit", "balance")),
    ("nation-guest", "The guest from another nation", "Four Nations", "A visitor from another nation brings news that exposes old assumptions inside {household}.", ("nation", "hospitality")),
    ("lost-scroll", "The lost scroll", "Legacy", "A lost scroll or map turns up with knowledge that could help one person more than the whole household.", ("scroll", "legacy")),
    ("harbour-choice", "The harbour choice", "Community", "A decision at the harbour or market will change who benefits from a local improvement.", ("trade", "community")),
    ("animal-companion", "The animal companion", "Care", "An animal companion needs care at a time when the household is already stretched thin.", ("animal", "care")),
    ("old-master", "The old master's request", "Bending", "A former teacher asks {sim} to carry forward a lesson in a way that may feel too public.", ("master", "bending")),
    ("festival-lantern", "The festival lantern", "Culture", "A festival custom becomes an opportunity to repair a relationship or make a new divide visible.", ("festival", "culture")),
    ("refugee-road", "The refugee road", "Community", "People displaced by a larger conflict reach the community and ask what welcome really means.", ("refugee", "compassion")),
    ("healing-water", "The healing water", "Care", "A limited healing resource is needed by more than one person, and {sim} is drawn into the decision.", ("healing", "care")),
    ("trade-caravan", "The trade caravan", "Travel", "A caravan promises supplies and stories from far away, but its terms feel uneven.", ("caravan", "trade")),
    ("council-seat", "The council seat", "Community", "A community role is offered to {sim}, asking whether service is worth the attention it brings.", ("council", "service")),
    ("family-technique", "The family technique", "Legacy", "A family technique or tradition could be shared more widely, but not everyone agrees it should be.", ("legacy", "bending")),
    ("unbalanced-place", "The unbalanced place", "Spirits", "A place near {household} feels wrong in a way that no ordinary explanation quite resolves.", ("spirit", "mystery")),
    ("quiet-journey", "The quiet journey", "Travel", "A short journey becomes a test of whether {sim} can leave responsibility behind for even a little while.", ("journey", "family")),
    ("nation-rumor", "The nation rumor", "Reputation", "A rumor about loyalty or heritage begins to shape how neighbours speak to the household.", ("nation", "reputation")),
    ("shared-meal", "The shared meal", "Community", "A shared meal could bridge a divide, but only if someone is willing to invite the first guest.", ("community", "peace")),
)

_GOT_PROMPTS = (
    ("wardens-invitation", "The warden's invitation", "Westeros", "An invitation from a powerful house offers notice, but also asks {household} to be seen choosing a side.", ("court", "allegiance")),
    ("bannermans-debt", "The bannerman's debt", "Westeros", "A sworn household asks for help at an inconvenient moment, testing the difference between duty and ruin.", ("banner", "debt")),
    ("maesters-warning", "The maester's warning", "Westeros", "A maester offers counsel that is sensible, unwelcome, and difficult to ignore.", ("maester", "advice")),
    ("market-tax", "The market tax", "Smallfolk", "A new demand from above shifts the burden onto ordinary households and makes silence costly.", ("tax", "smallfolk")),
    ("tourney-token", "The tourney token", "Court", "A token or favor offered before a tourney carries more political meaning than it first appears.", ("tourney", "court")),
    ("secret-vow", "The secret vow", "Legacy", "A promise made in private could change the family story if the wrong person learns of it.", ("vow", "secret")),
    ("winter-stock", "The winter stock", "Survival", "Preparations for a hard season reveal that the household has less security than it has claimed.", ("winter", "survival")),
    ("rival-house", "The rival house", "Court", "A rival offers a courtesy that may be a genuine bridge—or an invitation to make a mistake.", ("rivalry", "diplomacy")),
    ("marriage-contract", "The marriage contract", "Marriage", "A proposed match appears promising until the details make clear what each side expects to gain.", ("marriage", "alliance")),
    ("soldiers-passage", "The soldiers' passage", "War", "Armed people passing nearby force {household} to choose between visibility, hospitality, and safety.", ("war", "safety")),
    ("castle-servant", "The castle servant", "Secrets", "A servant brings a piece of information that could help the family—or destroy someone more vulnerable.", ("servant", "secret")),
    ("old-sigils", "The old sigils", "Legacy", "An older version of the family story is found in a seal, banner, or account book.", ("sigil", "legacy")),
    ("disputed-oath", "The disputed oath", "Court", "Two people remember the same promise differently, and both expect {sim} to take a position.", ("oath", "honor")),
    ("harbour-tidings", "The harbour tidings", "Westeros", "News from the harbour shifts the household's sense of who is safe, powerful, and far away.", ("news", "travel")),
    ("heir-at-court", "The heir at court", "Succession", "An heir is invited into a world of influence before the family is certain they are prepared for it.", ("heir", "court")),
    ("council-favor", "The council favor", "Court", "A small favor for a council member could bring access today and obligation tomorrow.", ("council", "favor")),
    ("blackmail-whisper", "The blackmail whisper", "Secrets", "A whisper about a damaging truth arrives before anyone can tell whether it is real.", ("blackmail", "secret")),
    ("household-hostage", "The unwanted guest", "Westeros", "A politically important guest needs shelter at {household}, making neutrality nearly impossible.", ("hostage", "hospitality")),
    ("raven-misread", "The raven misread", "Communication", "A message is ambiguous enough that acting quickly and waiting both carry serious risks.", ("raven", "communication")),
)

# Add the nineteen or eighteen supplemental cards required to bring the original
# hand-written decks to a consistent, clearly advertised minimum of twenty.
COMMON_CARDS += _supporting_cards("common", _COMMON_PROMPTS)
ERA_CARDS = {
    "pre-modern": ERA_CARDS["pre-modern"] + _supporting_cards("premodern", _PRE_MODERN_PROMPTS),
    "industrial": ERA_CARDS["industrial"] + _supporting_cards("industrial", _INDUSTRIAL_PROMPTS),
    "modern": ERA_CARDS["modern"] + _supporting_cards("modern", _MODERN_PROMPTS),
}
RULESET_CARDS = {
    core_rulesets.SEVERALUDO: RULESET_CARDS[core_rulesets.SEVERALUDO] + _supporting_cards("severaludo", _SEVERALUDO_PROMPTS),
    core_rulesets.MORBID: RULESET_CARDS[core_rulesets.MORBID] + _supporting_cards("morbid", _MORBID_PROMPTS),
    core_rulesets.CLASSIC_2023: RULESET_CARDS[core_rulesets.CLASSIC_2023] + _supporting_cards("classic", _CLASSIC_PROMPTS),
}
ADDON_CARDS = {
    "harry_potter_decades": ADDON_CARDS["harry_potter_decades"] + _supporting_cards("hp", _HARRY_POTTER_PROMPTS),
    "avatar_decades": ADDON_CARDS["avatar_decades"] + _supporting_cards("avatar", _AVATAR_PROMPTS),
    "game_of_thrones_decades": ADDON_CARDS["game_of_thrones_decades"] + _supporting_cards("got", _GOT_PROMPTS),
}

for _deck_name, _cards in (
    (("Household drama"), COMMON_CARDS),
    *ERA_CARDS.items(),
    *RULESET_CARDS.items(),
    *ADDON_CARDS.items(),
):
    if len(_cards) < MINIMUM_DECK_CARDS:
        raise RuntimeError(f"Drama deck {_deck_name!r} needs at least {MINIMUM_DECK_CARDS} cards.")


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


_SCENE_GUIDANCE = {
    "secrets": ("Trust, privacy, and the cost of letting the right person in are all on the line.", "You decide who started the pressure and what they stand to gain from an answer."),
    "relationships": ("The immediate choice can change how safe, respected, or distant a relationship feels.", "Choose whether the other person is family, a friend, a former partner, or someone new to the household."),
    "community": ("The household's standing in its neighborhood matters as much as the practical result.", "Pick the neighbor, group, or local institution whose memory of this choice will matter later."),
    "household": ("Time, labor, comfort, and fairness inside the home are competing priorities.", "Decide which household member is quietly carrying more of the burden than everyone else notices."),
    "legacy": ("A present need is pressing against what the family hopes to preserve for the next generation.", "Choose which object, promise, name, or tradition gives the question its emotional weight."),
    "survival": ("Safety and resources are limited, so every compassionate or risky choice has a visible cost.", "Choose exactly what is scarce: food, shelter, money, time, transport, or the strength to keep working."),
    "work": ("Security, ambition, and solidarity can pull the household in different directions.", "Decide who benefits from the opportunity and who may be left carrying the cost at home."),
    "ambition": ("The opportunity could widen a future, but it asks the household to risk stability today.", "Choose the concrete opportunity—a job, training place, project, patron, or move—and why it arrived now."),
    "identity": ("The choice concerns how openly someone can live by their values without losing connection or safety.", "Decide whose approval feels important and what support would make a public or private path feel possible."),
    "reputation": ("A story about the household is beginning to travel beyond its control.", "Choose who heard it first and what piece of the story is true, exaggerated, or completely false."),
    "family": ("Care, loyalty, and individual wishes are colliding inside a relationship that cannot simply be walked away from.", "Choose the family member whose needs are least visible in the immediate argument."),
    "marriage": ("Affection, family expectation, status, and practical security are entangled in the proposed match.", "Decide what the couple privately wants before relatives, contracts, or public opinion complicate the matter."),
    "succession": ("The question is not only who receives responsibility, but who feels seen, protected, and trusted by the family.", "Choose the earlier promise or family custom that makes an easy answer impossible."),
    "finance": ("A resource can solve the present problem or preserve a future option, but rarely both.", "Set the scale of the decision: a small household budget, a life-changing debt, or something valuable that cannot be replaced."),
    "care": ("Someone needs practical or emotional support, and the household must decide how to share that responsibility.", "Choose what kind of care is needed and which person has quietly been doing most of it already."),
    "education": ("Learning could create independence and opportunity, but it changes who is available to the household now.", "Choose the skill, school, mentor, or course and what sacrifice it asks from daily life."),
    "travel": ("A journey promises information, freedom, or reunion, but distance always makes a household vulnerable.", "Decide the destination, how long the traveler may be gone, and who is worried about the departure."),
    "faith": ("Belief, belonging, and public expectation are all part of the decision.", "Choose whether the pressure comes from sincere conviction, a respected leader, or the household's place in the community."),
    "trade": ("A practical exchange can create a lasting obligation as easily as it creates a benefit.", "Choose what each side believes it is owed and what would make the bargain feel fair."),
    "magic": ("A magical solution may work quickly, but secrecy, consent, and unintended attention still matter.", "Decide who knows about the magic, who does not, and what would happen if that boundary failed."),
    "wizarding world": ("Magical custom and ordinary family loyalties are pulling in different directions.", "Choose whether the outside pressure comes from school, the Ministry, a shopkeeper, or another magical household."),
    "hogwarts": ("School identity, friendship, and family expectation all make the choice feel larger than one moment.", "Choose the student, house, or mentor whose opinion makes this matter most."),
    "four nations": ("Community balance, cultural expectations, and the wider world all shape what the household can safely do.", "Choose which nation, local leader, or tradition gives the decision its historical context."),
    "bending": ("Skill can bring responsibility, admiration, and pressure to use it in ways the Sim did not choose.", "Decide whether the concern is training, public attention, family heritage, or the fear of causing harm."),
    "spirits": ("The situation asks the household to balance action with respect for forces it cannot fully control.", "Choose the sign, place, or story that tells the family the balance has been disturbed."),
    "westeros": ("Courtesy, safety, and political allegiance are rarely separate in this world.", "Choose which house, sworn duty, or local power makes a simple answer dangerous."),
    "court": ("Every visible choice can be read as an alliance, insult, or invitation by people outside the household.", "Choose who is watching and what they hope to gain from the family's response."),
    "war": ("Safety and duty are both urgent, and the household may have no choice that feels clean.", "Decide how close the danger is and which person has the least freedom to refuse it."),
}


def _scene_briefing(card: dict[str, Any], facts: dict[str, str]) -> tuple[dict[str, str], ...]:
    category = str(card.get("category") or "").casefold()
    stakes, unknown = _SCENE_GUIDANCE.get(category, (
        "The household must balance a present need against relationships and consequences that may outlast the moment.",
        "Choose the outside person or pressure that makes this more than an ordinary household decision.",
    ))
    sim, household = facts["sim"], facts["household"]
    return (
        {"label": "Who is involved", "text": f"{sim} is expected to make the first call, but the outcome will be felt across {household}. You may choose the other person or group already present in your save."},
        {"label": "What is at stake", "text": stakes},
        {"label": "What you decide", "text": unknown},
    )


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
    resolved_card = {**card, "opening": _text(card["opening"], facts)}
    return {
        "state": {key: str(value or "") for key, value in state.items()},
        "card": resolved_card,
        "briefing": _scene_briefing(resolved_card, facts),
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
        "opening": card["opening"],
        "branch_id": branch["id"], "branch_label": branch["label"],
        "branch_beat": branch["beat"],
        "ending_id": ending["id"], "ending_label": ending["label"],
        "ending_title": ending["title"], "ending_text": ending["text"],
        "sim_id": resolved["sim"].id if resolved["sim"] else None,
        "sim_name": resolved["sim"].label if resolved["sim"] else "",
        "household_id": resolved["household"].id if resolved["household"] else None,
        "household_name": resolved["household"].label if resolved["household"] else "",
        "body": " ".join(piece for piece in pieces if piece),
        "tags": list(ending.get("tags") or []),
        "player_decision": True,
        "mechanical_effects": "None — this scene is a voluntary chronicle decision.",
    }
