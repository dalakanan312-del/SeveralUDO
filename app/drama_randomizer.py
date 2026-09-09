"""The player's 64 in-game prompts; separate from the decision-tree minigame."""
import hashlib
import secrets
from uuid import uuid4

from . import infinite_decades


# Keep the original d64 order. Similar prompts are intentionally separate results.
_PROMPTS = (
    ("Have a Baby", "👶", "Play through a pregnancy and delivery in the selected household."),
    ("Acquire a New Household Animal", "🐕🐈", "Add an animal that fits this household, era and the packs you own."),
    ("Get Married", "💍", "Choose an eligible couple and play their wedding."),
    ("Separate From Your Spouse / Seek an Annulment", "💔", "Choose a married couple and play out their separation or annulment."),
    ("Take In a Foster Child or Ward", "🧒", "Move a child into the household as a foster child or ward; decide who becomes their guardian."),
    ("Have a Matchmaker Find You a Suitor", "💌", "Choose a matchmaker and an eligible suitor, then arrange an introduction in game."),
    ("Become an Occult / Supernatural Sim", "🔮", "Turn a suitable Sim into a supernatural type available in your game and ruleset."),
    ("Kill a Random Sim", "☠️", "Randomly choose a living Sim from the cast you are playing and carry out this fictional death in game."),
    ("Fight or Duel Someone", "⚔️", "Choose an opponent and play a fight or an era-appropriate duel; record who won."),
    ("Kidnap Someone", "🏇", "Play a fictional captivity storyline and decide which household takes the captive."),
    ("Have a Neighbor’s Baby", "🤱", "Choose an eligible neighbor as the other parent and play the pregnancy and birth."),
    ("Buy or Open an Inn, Tavern, or Alehouse", "🍺", "Choose a suitable lot and set up the household's new inn, tavern or alehouse."),
    ("Suffer a Disfiguring Accident or Illness", "🩹", "Play an accident or illness and reflect its lasting effects in the Sim's appearance or story."),
    ("Renovate or Expand the Family Home", "🏠", "Build an extension or renovate a room using the household's available funds."),
    ("Kill a Family Member", "☠️", "Choose a living relative and play out this fictional family death in game."),
    ("Change Your Trade or Occupation", "🛠️", "Leave the current work behind and begin a different trade or occupation."),
    ("One of Your Children Must Die", "👼", "Choose a child in this fictional family and play out the loss in game."),
    ("Cast Someone Out of the Household and Give Them a Large Share of the Family Wealth", "💰", "Choose who leaves, decide their share of the fortune and move them out with it."),
    ("Move to a New Home or Estate", "🏰", "Choose a new residence and move the household there."),
    ("Lose Half of Your Household Wealth", "💸", "Work out half of the household's wealth and remove that amount in game."),
    ("Move to a Random Available Lot", "🌳", "Randomly choose from suitable available lots and move the household."),
    ("Adopt an Infant", "🍼", "Bring an infant into the household through an adoption storyline."),
    ("Adopt or Take In a Child", "🧒", "Choose a child to join the family and establish their guardian or adoptive parents."),
    ("Take In a Teenage Ward or Apprentice", "🧑", "Bring a teen into the household and choose a guardian, mentor or trade for them."),
    ("Go on a Pilgrimage or Long Journey", "🐎", "Choose a destination, companions and how long the household will be away."),
    ("Begin Collecting Relics, Herbs, Gems, Coins, or Curiosities", "💎", "Choose a collection and obtain its first item in game."),
    ("Completely Redesign a Sim’s Appearance and Wardrobe", "👗", "Give a Sim a complete appearance and wardrobe change in Create-a-Sim."),
    ("Master a Skill", "📜", "Choose a skill and work toward its highest available level."),
    ("Abandon Your Current Trade or Position", "✖️", "Have a Sim leave their job, trade or position without immediately replacing it."),
    ("Give a Sim a Historically Appropriate Makeover", "🪡", "Update a Sim's hair and clothing to suit the current era and their social position."),
    ("Neglect a Child", "👨‍👦", "Play an in-game parenting crisis and record what happens to the child and their care."),
    ("Take a New Lover", "❤️", "Choose an eligible new romantic partner and develop the relationship in game."),
    ("Abandon One of Your Children", "😈", "Play a fictional abandonment storyline and decide where the child goes afterward."),
    ("Meet and Seduce Someone at a Feast, Market, Tavern, or Festival", "🍻", "Visit a suitable gathering, meet an eligible Sim and pursue a romance."),
    ("WooHoo Somewhere Scandalously Public", "😳", "Choose consenting adult Sims and a public setting supported by your game."),
    ("Travel Into the Wilderness or an Unfamiliar Region", "🌲", "Choose an unfamiliar destination and take your Sim or household there."),
    ("Travel to the Coast or Seaside", "⚓", "Take the household to an available coastal destination or seaside lot."),
    ("Give Away Most of Your Available Money", "💰", "Choose the recipients and amount, then transfer or remove most of the household's available money."),
    ("Lose All Household Money and Sell Most of the Furniture", "😭", "Choose what few possessions remain and play the household's financial collapse."),
    ("Found a Guild, Society, Brotherhood, or Household Faction", "🛡️", "Choose a purpose, founding members and a meeting place for the new group."),
    ("Start a Family Trade or Business", "🏪", "Choose a trade, assign family roles and establish a working space or business lot."),
    ("Renounce Your Occult Status", "🔥", "Choose a supernatural Sim and play their return to ordinary life using the options available in your game."),
    ("Pursue Formal Education, Apprenticeship, or Religious Study", "📚", "Choose a course of study, teacher or institution and begin the Sim's training."),
    ("Arrange the Suspicious Death of Your Spouse", "☠️", "Play a fictional suspicious-death storyline involving a spouse and record the household's account of it."),
    ("House Fire", "🔥", "Play a household fire in game, then record damage, survivors and any losses."),
    ("Find a Lover at a Tavern, Market, Training Yard, Festival, or Church Gathering", "💘", "Choose a suitable gathering and introduce your Sim to an eligible romantic partner."),
    ("Try for a Baby With a Random Eligible Sim", "🤰", "Randomly choose an eligible adult partner and attempt a pregnancy in game."),
    ("Try to Have Children With Every Eligible Sim Your Sim Knows", "😈", "Use the Sim's known eligible adult partners for this in-game family challenge; record the actual pregnancies and births."),
    ("Become Obsessed With a Particular Food, Craft, or Household Tradition", "🍞", "Choose the obsession and make it part of the household's routine."),
    ("Abandon Modern Conveniences and Live as Simply as Possible", "🕯️", "Remove conveniences from the home and change the household's daily routine to match."),
    ("Gain Access to New Technology or Luxuries Appropriate to the Era", "⚙️", "Choose a newly available era-appropriate object or luxury and acquire it for the household."),
    ("Steal Someone Else’s Spouse", "💞", "Pursue an eligible married adult Sim and play out the consequences for both households."),
    ("Hold a Great Feast or Celebration", "🎉", "Choose the occasion, guests and venue, then hold the celebration in game."),
    ("Kidnap or Illegally Claim Someone Else’s Baby", "👶", "Play a fictional disputed-custody or stolen-heir storyline and record who raises the baby."),
    ("Death in the Family", "⚰️", "Choose the family loss to play out and record the deceased Sim and the family's response."),
    ("Breed Your Dogs, Cats, Horses, or Livestock", "🐎", "Choose suitable household animals and attempt to breed them using your available game features."),
    ("Create or Acquire the Most Advanced Invention Available for the Era", "⚙️", "Choose the era's most advanced available invention and build or obtain it."),
    ("Complete a Major Life Goal or Aspiration", "🙌", "Choose a major aspiration or life goal and play until it is achieved."),
    ("Have a Sim Run Away From Home", "🏃", "Choose who runs away, move them out and decide where they take refuge."),
    ("Move to the Largest Town or City Available", "🏙️", "Choose your game's largest suitable settlement and relocate the household."),
    ("Your Sim Is Kidnapped or Taken Captive", "⛓️", "Play your focus Sim's fictional captivity and decide who holds them and where."),
    ("Become a Thief, Bandit, Smuggler, Pirate, or Other Criminal", "🗡️", "Choose an era-appropriate criminal role and play it through careers, activities or household storytelling."),
    ("Found or Join a Religious Sect, Secret Society, or Heretical Movement", "🕯️", "Choose the group's beliefs or purpose, its members and how your Sim joins or founds it."),
    ("Kill Someone and Keep Their Ghost in the Household", "👻", "Play a fictional Sim death, then bring their ghost into the household if your game supports it."),
)
PROMPTS = tuple({'number': i, 'title': title, 'emoji': emoji, 'action': action}
                for i, (title, emoji, action) in enumerate(_PROMPTS, 1))


def draw(save, sim=None, household=None):
    return {
        'save_id': save.id, 'epoch': infinite_decades.state(save).get('epoch', ''),
        'draw_id': uuid4().hex, 'number': secrets.randbelow(len(PROMPTS)) + 1,
        'drawn_global_day': save.global_day,
        'sim_id': sim.id if sim else None, 'sim_name': sim.label if sim else '',
        'household_id': household.id if household else None,
        'household_name': household.label if household else '',
    }


def resolve(save, state):
    if not isinstance(state, dict) or state.get('save_id') != save.id:
        raise ValueError('Draw a prompt in this save first.')
    if state.get('epoch', '') != infinite_decades.state(save).get('epoch', ''):
        raise ValueError('The dynasty branch changed. Draw a new prompt for this branch.')
    number = state.get('number')
    if type(number) is not int or not 1 <= number <= len(PROMPTS):
        raise ValueError('This prompt is no longer available. Draw again.')
    draw_id = state.get('draw_id', '')
    if not isinstance(draw_id, str) or len(draw_id) != 32 or any(c not in '0123456789abcdef' for c in draw_id):
        raise ValueError('This draw is invalid. Draw again.')
    return {**state, 'prompt': PROMPTS[number - 1]}


def record_id(state):
    # One stored outcome per draw, including retries from another tab or device.
    return hashlib.sha256(f"drama-randomizer:{state['save_id']}:{state['draw_id']}".encode()).hexdigest()[:32]


def outcome_data(state, notes):
    prompt = state['prompt']
    cast = ' · '.join(value for value in (state.get('sim_name'), state.get('household_name')) if value)
    return {
        'source': 'Drama Randomizer', 'category': 'In-game drama',
        'card_id': f"in-game-{prompt['number']}", 'card_title': prompt['title'],
        'prompt_number': prompt['number'], 'die': 'd64', 'actual': prompt['number'],
        'draw_id': state['draw_id'], 'drawn_global_day': state['drawn_global_day'],
        'sim_id': state.get('sim_id'), 'sim_name': state.get('sim_name', ''),
        'household_id': state.get('household_id'), 'household_name': state.get('household_name', ''),
        'played_in_game': True, 'outcome_notes': notes,
        'body': (f"{cast}: " if cast else '') + notes,
        'player_decision': True, 'tags': ['drama-randomizer', 'played-in-game'],
        'mechanical_effects': 'Player-confirmed in-game action; no automatic changes to other tracker records.',
    }
