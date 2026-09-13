"""Family Fortunes: server-authoritative, saved negotiation boards.

The simulation does not spend game money or change marriages while playing.
Confirmed endings create explicit plans and an exact, player-chosen chronicle.
"""
from __future__ import annotations

import hashlib
import json
import math
import secrets
from . import domain, infinite_decades

KIND = 'family_fortune_game'
PAGE = 'family-fortunes'
MAX_ROUNDS = 3
SOURCE = 'Family Fortunes'
KINDS = {'sim', 'household', 'relationship', 'heirloom', 'estate_plan', 'dowry_plan', 'guardianship', KIND}
CLOSED = {'ended', 'divorced', 'annulled', 'widowed', 'cancelled', 'completed', 'paid'}

SCENARIOS = {
    'security': {
        'mode': 'matchmaking', 'title': 'A home before a wedding',
        'opening': 'The receiving family will consider the match, but refuses to send their child into an uncertain home. They ask for a modest cash dowry and a written promise of a separate residence. Your own household insists on keeping a quarter of its negotiation purse.',
        'twist': 'A dependent relative also needs a place to live. The family now asks the couple to support that relative for their first year together. This is a fictional complication, not a detected game event.',
        'promise': ('residence', 'Establish a separate residence', 'Set up the couple in their own home after the wedding.'),
        'extra': ('support', 'Support the dependent relative', 'House or support a dependent relative for one challenge year.'), 'fraction': .25,
    },
    'trade': {
        'mode': 'matchmaking', 'title': 'The workshop alliance',
        'opening': 'A marriage could join two family trades. The receiving family wants start-up money and a promise that their child can continue working. Your household wants enough left over to keep its own trade running.',
        'twist': 'The workshop has lost its apprentice. The family asks the couple to take on and train a replacement before the first anniversary. No apprentice is automatically added to the save.',
        'promise': ('trade', 'Protect the partner’s trade', 'Keep the partner in their chosen trade or occupation after marriage.'),
        'extra': ('apprentice', 'Train an apprentice', 'Arrange and play through training a young adult apprentice within one challenge year.'), 'fraction': .35,
    },
    'keepsake': {
        'mode': 'matchmaking', 'title': 'A pledge worth remembering',
        'opening': 'The receiving family values a public commitment more than a large dowry. Offer a smaller purse and a witnessed engagement. A promised family heirloom can strengthen trust, but will remain a claim against the estate until fulfilled.',
        'twist': 'A relative objects to the match. The couple is asked to hold a reconciliation gathering before the wedding, giving both families a chance to speak openly.',
        'promise': ('witness', 'Hold a witnessed engagement', 'Gather both families and announce the engagement in game.'),
        'extra': ('gathering', 'Arrange a reconciliation gathering', 'Invite the objecting relative and both families to a gathering before the wedding.'), 'fraction': .15,
    },
    'codicil': {
        'mode': 'inheritance', 'title': 'The sealed codicil',
        'opening': 'The household must name a principal heir while providing a fair share for the other beneficiaries. Earlier marriage promises still stand. Keep a household reserve if needed, but every exclusion will be recorded.',
        'twist': 'A sealed letter asks for one specific asset to go to a different beneficiary. It is a fictional claim within this game: honor it, or compensate the claimant and explain the choice.',
    },
    'creditor': {
        'mode': 'inheritance', 'title': 'An account left open',
        'opening': 'The estate can support the next generation, but its known debts and marriage promises must be considered first. Decide which heir protects its continuity and how much the others need.',
        'twist': 'A creditor presents a disputed bill worth one tenth of the negotiation purse. Reserve that amount for review or proceed with a contested settlement. It is a scenario claim, not a confirmed game debt.',
    },
    'disputed': {
        'mode': 'inheritance', 'title': 'Two claims to the family seat',
        'opening': 'One beneficiary is favored by the recorded succession order, while others expect meaningful provision. Allocate the principal asset, cash, and keepsakes without forgetting the household’s previous promises.',
        'twist': 'Another beneficiary contests the principal asset. Keeping it with the heir now requires a larger cash provision for that claimant; giving them the asset instead may leave the heir dissatisfied.',
    },
}


def integer(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return default


def epoch(save):
    return [infinite_decades.state(save).get('epoch', ''), (save.settings or {}).get('clock_recovery_epoch', '')]


def home_id(sim):
    return str(sim.data.get('current_household_id') or sim.data.get('household_id') or '')


def available_funds(home):
    for key in ('last_game_funds', 'household_funds', 'funds'):
        if home.data.get(key) not in (None, ''):
            return max(0, integer(home.data[key]))
    return None


def active(rows):
    return [r for r in rows if not r.deleted and not r.data.get('infinite_frozen')]


def living(sim, save):
    from .main import _living_sim
    return _living_sim(sim, save)


def eligible(rows, save):
    minimum = domain.age_setting_days(save, 'marriage_min_age_days', 72)
    taken = set()
    by_id = {r.id: r for r in rows if r.kind == 'sim'}
    for r in rows:
        if r.kind != 'relationship' or str(r.data.get('status') or 'Active').casefold() in CLOSED:
            continue
        d = r.data
        if not (d.get('legally_married') or any(word in str(d.get('type', '')).casefold() for word in ('marriage', 'married', 'courtship', 'betrothal', 'engagement'))):
            continue
        ids = [str(d.get('partner1_id') or ''), str(d.get('partner2_id') or '')]
        if any(sid in by_id and not living(by_id[sid], save) for sid in ids):
            continue
        taken.update(ids)
    return [r for r in rows if r.kind == 'sim' and living(r, save) and r.id not in taken
            and r.data.get('birth_global_day') is not None
            and save.global_day - integer(r.data['birth_global_day']) >= minimum]


def outstanding(rows, home):
    """Actual, unpaid recorded dowries; none are invented from family names."""
    return [r for r in rows if r.kind == 'dowry_plan'
            and str(r.data.get('payer_household_id') or r.data.get('household_id') or '') == home.id
            and str(r.data.get('status') or 'Planned').casefold() not in CLOSED
            and not r.data.get('paid')]


def heirlooms(rows, home):
    members = {r.id for r in rows if r.kind == 'sim' and home_id(r) == home.id}
    return [r for r in rows if r.kind == 'heirloom' and
            (r.data.get('household_id') == home.id or r.data.get('current_holder_sim_id') in members)]


def create(rows, save, home, mode, budget, debts, property_text, beneficiaries, scenario=None):
    if mode not in {'matchmaking', 'inheritance'}:
        raise ValueError('Choose matchmaking or inheritance.')
    if not 0 <= budget <= 1_000_000_000 or not 0 <= debts <= budget:
        raise ValueError('Use a non-negative purse, with known debts no larger than that purse.')
    funds = available_funds(home)
    if funds is not None and budget > funds:
        raise ValueError('The purse exceeds this household’s last reported funds. Correct the household record first if it is out of date.')
    people = {r.id: r for r in rows if r.kind == 'sim' and living(r, save)}
    beneficiaries = list(dict.fromkeys(beneficiaries))
    if mode == 'inheritance' and (not 1 <= len(beneficiaries) <= 10 or any(i not in people for i in beneficiaries)):
        raise ValueError('Choose between one and ten living beneficiaries from this save.')
    if mode == 'matchmaking' and not any(home_id(s) == home.id for s in eligible(rows, save)):
        raise ValueError('This household has no unpromised Sim old enough under your marriage settings.')
    choices = [key for key, value in SCENARIOS.items() if value['mode'] == mode]
    scenario = scenario if scenario in choices else secrets.choice(choices)
    assets = []
    # Ten distinct cash parcels conserve the exact purse, including remainders.
    for n in range(min(10, budget)):
        amount = budget // min(10, budget) + (n < budget % min(10, budget))
        assets.append({'id': f'cash-{n}', 'kind': 'cash', 'label': f'Purse {n + 1}', 'amount': amount, 'detail': f'§{amount:,} from the negotiation purse'})
    pledged = {sid for r in outstanding(rows, home) for sid in r.data.get('promised_heirloom_ids', [])}
    for r in heirlooms(rows, home):
        if mode == 'matchmaking' and r.id in pledged:
            continue
        assets.append({'id': 'heirloom-' + r.id, 'kind': 'heirloom', 'label': r.label, 'amount': 0,
                       'record_id': r.id, 'detail': 'Recorded heirloom' + (' · already promised' if r.id in pledged else '')})
    lines = [s.strip() for s in property_text.splitlines() if s.strip()]
    if len(lines) > 8 or len(property_text) > 1600:
        raise ValueError('Use at most eight property or business cards, up to 1,600 characters.')
    for n, line in enumerate(lines):
        assets.append({'id': f'property-{n}', 'kind': 'property', 'label': line[:160], 'amount': 0,
                       'detail': 'Player-entered property · not detected in game'})
    if not assets:
        raise ValueError('Add a cash purse, a recorded heirloom, or a property card to play.')
    if len(assets) > 70:
        raise ValueError('This household has too many heirlooms for one board. Archive unrelated heirlooms or choose a smaller household.')
    if mode == 'matchmaking':
        for key in ('promise',):
            code, label, detail = SCENARIOS[scenario][key]
            assets.append({'id': code, 'kind': 'promise', 'label': label, 'amount': 0, 'detail': detail})
    return {'source': SOURCE, 'mode': mode, 'household_id': home.id, 'household_name': home.label,
            'budget': budget, 'known_debts': debts if mode == 'inheritance' else 0,
            'scenario': scenario, 'assets': assets, 'beneficiary_ids': beneficiaries if mode == 'inheritance' else [],
            'allocations': {}, 'first_id': '', 'second_id': '', 'round': 0, 'history': [],
            'status': 'playing', 'epoch': epoch(save), 'started_global_day': save.global_day,
            'funds_source': 'last reported game funds' if funds is not None else 'player-entered purse',
            'marriage_day': save.global_day + max(1, save.days_per_year), 'preview': None}


def validate(data, rows, save):
    if data.get('epoch') != epoch(save) or save.global_day < data['started_global_day']:
        raise ValueError('The save returned to an earlier checkpoint. Start a new board; this one remains in history.')
    by_id = {r.id: r for r in rows}
    home = by_id.get(data['household_id'])
    if not home or home.kind != 'household':
        raise ValueError('This household is no longer in the active save.')
    funds = available_funds(home)
    if funds is not None and data['budget'] > funds:
        raise ValueError('Household funds have fallen below this board’s purse. Start a new board with an affordable purse.')
    owned = {r.id for r in heirlooms(rows, home)}
    if any(a.get('record_id') not in owned for a in data['assets'] if a['kind'] == 'heirloom'):
        raise ValueError('A recorded heirloom moved or was archived. Start a fresh board with the current estate.')
    if data['mode'] == 'inheritance':
        if any(i not in by_id or not living(by_id[i], save) for i in data['beneficiary_ids']):
            raise ValueError('A beneficiary is no longer available. Start a fresh estate board.')
    elif data.get('first_id') and data.get('second_id'):
        allowed = {r.id: r for r in eligible(rows, save)}
        first, second = allowed.get(data['first_id']), allowed.get(data['second_id'])
        if not first or not second or first.id == second.id or home_id(first) != home.id:
            raise ValueError('Choose two different, unpromised Sims who meet the current marriage age; the first must belong to this household.')
        from .main import kinship_warning
        depth = max(1, min(8, integer(save.settings.get('kinship_detection_generations'), 3)))
        warning = kinship_warning(first.id, second.id, [r for r in rows if r.kind == 'sim'], depth)
        if warning:
            raise ValueError('This match is blocked by the current kinship setting: ' + warning + '.')
    return by_id, home


def layout(data, payload, rows, save):
    result = {**data, 'preview': None}
    targets = {'reserve', 'offer'} if data['mode'] == 'matchmaking' else {'reserve', 'debts', *data['beneficiary_ids']}
    assignments = payload.get('allocations', {})
    if not isinstance(assignments, dict) or len(assignments) > 75:
        raise ValueError('Invalid board layout.')
    cards = {a['id']: a for a in data['assets']}
    if any(key not in cards or not isinstance(value, str) or value not in targets for key, value in assignments.items()):
        raise ValueError('Use only the cards and destinations on this board.')
    if any(value == 'debts' and cards[key]['kind'] != 'cash' for key, value in assignments.items()):
        raise ValueError('Only cash parcels can be reserved for debt payments.')
    result['allocations'] = dict(assignments)
    if data['mode'] == 'matchmaking':
        for key in ('first_id', 'second_id'):
            chosen = str(payload.get(key) or '')
            if data['round'] and chosen != data[key]:
                raise ValueError('Negotiations have begun with this couple. Start a new board to change partners.')
            result[key] = chosen
        result['marriage_day'] = integer(payload.get('marriage_day'), data['marriage_day'])
        if not save.global_day <= result['marriage_day'] <= save.global_day + 100 * max(1, save.days_per_year):
            raise ValueError('Choose a marriage date from today through the next 100 challenge years.')
    validate(result, rows, save)
    return result


def evaluate(data, rows, save):
    from .main import succession_ranking
    by_id, home = validate(data, rows, save)
    cards = data['assets']; assignments = data['allocations']; scenario = SCENARIOS[data['scenario']]
    def allocated(target, kind=None):
        return [a for a in cards if assignments.get(a['id'], 'reserve') == target and (kind is None or a['kind'] == kind)]
    def cash(target):
        return sum(a['amount'] for a in allocated(target, 'cash'))
    checks = []
    def check(label, ok, explanation):
        checks.append({'label': label, 'met': bool(ok), 'detail': explanation})
    pledges = outstanding(rows, home)
    twist = data['round'] >= 1
    if data['mode'] == 'matchmaking':
        if not data.get('first_id') or not data.get('second_id'):
            raise ValueError('Drag one household Sim and one eligible partner onto the two portrait spaces.')
        first, second = by_id[data['first_id']], by_id[data['second_id']]
        offer = cash('offer'); reserve = data['budget'] - offer
        outstanding_cash = sum(max(0, integer(r.data.get('amount'))) for r in pledges)
        funds = available_funds(home)
        if funds is not None and offer + outstanding_cash > funds:
            raise ValueError('This offer plus existing unpaid dowries exceeds the household’s reported funds.')
        other_home = by_id.get(home_id(second))
        other_funds = available_funds(other_home) if other_home else None
        wealthy = funds is not None and other_funds is not None and other_funds > funds * 2
        friendship = 0
        for rel in first.data.get('game_relationships') or []:
            if not isinstance(rel, dict):
                continue
            if str(rel.get('other_game_sim_id')) == str(second.data.get('game_sim_id')):
                friendship = integer(rel.get('friendship_score')); break
        keepsake = data['scenario'] == 'keepsake' and bool(allocated('offer', 'heirloom'))
        fraction = scenario['fraction'] + (.1 if wealthy else 0) + (.1 if friendship < 0 else 0) - (.05 if keepsake else 0)
        target = math.ceil(data['budget'] * fraction)
        check('Receiving family: cash provision', offer >= target, f'{second.label}’s family requests at least §{target:,}; offered §{offer:,}.' + (' Their household has over twice the reported funds of yours.' if wealthy else '') + (' A reported negative friendship raises their demand.' if friendship < 0 else ''))
        check('Your household: keep a reserve', reserve >= math.ceil(data['budget'] * .25), f'Keep at least §{math.ceil(data["budget"] * .25):,}; §{reserve:,} remains.')
        check(scenario['promise'][1], assignments.get(scenario['promise'][0]) == 'offer', scenario['promise'][2])
        if twist:
            check(scenario['extra'][1], assignments.get(scenario['extra'][0]) == 'offer', scenario['extra'][2])
        promised_ids = {sid for r in pledges for sid in r.data.get('promised_heirloom_ids', [])}
        if any(a.get('record_id') in promised_ids for a in allocated('offer', 'heirloom')):
            raise ValueError('One of these heirlooms was promised in another agreement. Remove it or resolve that earlier promise first.')
        summary = [{'name': second.label, 'sim_id': second.id, 'cash': offer, 'assets': allocated('offer')}]
        heir_id = None
        risks = [c['detail'] for c in checks if not c['met']]
    else:
        beneficiaries = [by_id[i] for i in data['beneficiary_ids']]
        # Keep ancestors in the graph even if they are not receiving a share.
        ranking = [r for r in succession_ranking([s for s in rows if s.kind == 'sim'], save)
                   if r['sim'].id in data['beneficiary_ids']]
        heir_id = ranking[0]['sim'].id if ranking else None
        principal = next((a for a in cards if a['kind'] in {'property', 'heirloom'}), cards[0])
        claimant = next((s for s in reversed(beneficiaries) if s.id != heir_id), beneficiaries[0])
        equal_floor = data['budget'] // max(1, len(beneficiaries) * 2)
        debt = data['known_debts'] + (math.ceil(data['budget'] * .1) if twist and data['scenario'] == 'creditor' else 0)
        check('Set aside known debts' + (' and the disputed bill' if debt > data['known_debts'] else ''), cash('debts') >= debt, f'Needed §{debt:,}; set aside §{cash("debts"):,}.')
        if heir_id:
            check('Protect the principal heir', assignments.get(principal['id']) == heir_id,
                  f'{by_id[heir_id].label} is first among these beneficiaries under {save.settings.get("succession_system") or "Absolute primogeniture"}; they expect {principal["label"]}.')
        else:
            check('Choose an eligible principal heir', False, 'None of these beneficiaries meets the saved succession restrictions. Change the selection or deliberately record a contested settlement.')
        for person in beneficiaries:
            check('Provision for ' + person.label, cash(person.id) >= equal_floor or any(a['kind'] != 'cash' for a in allocated(person.id)),
                  f'{person.label} expects at least §{equal_floor:,} or a named asset; cash provision is §{cash(person.id):,}.')
        claims = {}
        for pledge in pledges:
            pid = pledge.data.get('recipient_sim_id') or pledge.data.get('sim_id') or ''
            claim = claims.setdefault(pid, {'amount': 0, 'heirlooms': set(), 'properties': set(), 'labels': []})
            claim['amount'] += max(0, integer(pledge.data.get('amount')))
            claim['heirlooms'].update(pledge.data.get('promised_heirloom_ids') or [])
            claim['properties'].update(a['label'] for a in pledge.data.get('promises', []) if a.get('kind') == 'property')
            claim['labels'].append(pledge.label)
        for pid, claim in claims.items():
            property_labels = {a['label'] for a in allocated(pid, 'property')}
            check('Earlier marriage promises: ' + ', '.join(claim['labels']),
                  pid in data['beneficiary_ids'] and cash(pid) >= claim['amount']
                  and all(assignments.get('heirloom-' + sid) == pid for sid in claim['heirlooms'])
                  and claim['properties'] <= property_labels,
                  f'Provide §{claim["amount"]:,}' + (' and the promised heirlooms' if claim['heirlooms'] else '')
                  + (' plus ' + ', '.join(sorted(claim['properties'])) if claim['properties'] else '')
                  + f' to {by_id[pid].label if pid in by_id else "the recorded recipient (not selected or unknown)"}. This does not mark the dowries paid.')
        if twist and data['scenario'] == 'codicil':
            requested = next((a for a in reversed(cards) if a['kind'] != 'cash'), cards[-1])
            check('Answer the sealed letter', assignments.get(requested['id']) == claimant.id or cash(claimant.id) >= max(1, math.ceil(data['budget'] * .3)),
                  f'{claimant.label} claims {requested["label"]}; offer that asset or at least §{max(1, math.ceil(data["budget"] * .3)):,}.')
        if twist and data['scenario'] == 'disputed':
            check('Settle the competing claim', assignments.get(principal['id']) == claimant.id or cash(claimant.id) >= max(1, math.ceil(data['budget'] * .4)),
                  f'{claimant.label} asks for {principal["label"]} or §{max(1, math.ceil(data["budget"] * .4)):,} in cash.')
        summary = [{'sim_id': s.id, 'name': s.label, 'cash': cash(s.id), 'assets': allocated(s.id)} for s in beneficiaries]
        risks = [c['detail'] for c in checks if not c['met']]
    met = sum(c['met'] for c in checks)
    return {'checks': checks, 'met': met, 'total': len(checks), 'score': round(100 * met / max(1, len(checks))),
            'agreed': not risks, 'risks': risks, 'summary': summary, 'heir_id': heir_id,
            'reserve': cash('reserve'), 'debts': cash('debts'), 'twist_revealed': twist}


def negotiate(data, rows, save):
    if data['round'] >= MAX_ROUNDS:
        raise ValueError('All three rounds have been used. Review this outcome or start a new game.')
    opening = evaluate(data, rows, save)
    result = {**data, 'round': data['round'] + 1, 'preview': None}
    if data['round'] == 0 and data['mode'] == 'matchmaking':
        code, label, detail = SCENARIOS[data['scenario']]['extra']
        result['assets'] = [*data['assets'], {'id': code, 'kind': 'promise', 'label': label, 'amount': 0, 'detail': detail}]
    result['assessed_layout'] = {k: data[k] for k in ('allocations', 'first_id', 'second_id', 'marriage_day')}
    assessed = evaluate(result, rows, save)
    transcript = ' '.join(f'{r["name"]}: ' + (', '.join(a['label'] + (f' (§{a["amount"]:,})' if a['kind'] == 'cash' else '') for a in r['assets']) or 'nothing allocated') + '.' for r in opening['summary'])
    result['history'] = [*data['history'], {'round': result['round'], 'offer': transcript, 'score': assessed['score'],
        'feedback': assessed['risks'] or ['Both sides’ stated requirements are met.'],
        'revelation': SCENARIOS[data['scenario']]['twist'] if data['round'] == 0 else ''}]
    return result


def review(data, rows, save):
    if data['round'] < 2:
        raise ValueError('Play an opening round and respond to the complication before reviewing an ending.')
    if data.get('assessed_layout') != {k: data[k] for k in ('allocations', 'first_id', 'second_id', 'marriage_day')}:
        raise ValueError('Your layout changed since the last round. Submit that offer before reviewing the ending.')
    evaluation = evaluate(data, rows, save)
    if data['mode'] == 'inheritance' and not any(r['assets'] for r in evaluation['summary']):
        raise ValueError('Allocate something to a beneficiary before recording a settlement.')
    by_id = {r.id: r for r in rows}
    offered = evaluation['summary'][0]['assets'] if data['mode'] == 'matchmaking' else []
    changes = ['Add the exact negotiation rounds, allocations, promises, and ending to Storyline.']
    if data['mode'] == 'matchmaking' and evaluation['agreed']:
        changes += [f'Create a betrothal for {by_id[data["first_id"]].label} and {by_id[data["second_id"]].label}, with marriage planned for GD {data["marriage_day"]}.',
                    f'Create an unpaid dowry plan for §{evaluation["summary"][0]["cash"]:,}, with {sum(a["kind"] != "cash" for a in offered)} asset or personal promises.']
    elif data['mode'] == 'matchmaking':
        changes += ['Record that the talks ended without agreement. No relationship or dowry is created.']
    else:
        changes += ['Record these allocations in the household’s estate plan, preserving its existing notes.',
                    'Leave dowries unpaid and asset ownership unchanged until you actually carry out the settlement.']
        if evaluation['heir_id']:
            existing_plans = sorted((r for r in rows if r.kind == 'estate_plan' and r.data.get('household_id') == data['household_id']), key=lambda r: (r.created_at, r.id))
            old_id = existing_plans[-1].data.get('heir_sim_id') if existing_plans else None
            old_name = by_id[old_id].label if old_id in by_id else 'not recorded'
            changes.append(f'Planned principal heir: {old_name} → {by_id[evaluation["heir_id"]].label}.')
        else:
            changes.append('No selected beneficiary meets the succession restrictions; leave any existing principal-heir selection unchanged.')
    changes += ['Add an in-game follow-up task. No game money is spent, no marriage is completed, and no Sims are killed.']
    # Only dependencies that could change THIS outcome invalidate confirmation.
    # A clock heartbeat, new skill, or unrelated record does not invalidate it.
    plans = sorted((r.id, r.version) for r in rows if r.kind == 'estate_plan' and r.data.get('household_id') == data['household_id']) if data['mode'] == 'inheritance' else []
    named = {sid: by_id[sid].label for sid in {data.get('first_id'), data.get('second_id'), *data['beneficiary_ids']} if sid in by_id}
    material = {'evaluation': evaluation, 'changes': changes, 'estate_plans': plans, 'named': named,
                'day': data['marriage_day'] if data['mode'] == 'matchmaking' else None}
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {'fingerprint': digest, 'evaluation': evaluation, 'changes': changes,
            'ending': ('Agreement reached' if evaluation['agreed'] else 'Talks ended without agreement') if data['mode'] == 'matchmaking' else ('Family settlement' if evaluation['agreed'] else 'Contested settlement')}


def story(data, preview):
    result = preview['evaluation']
    paragraphs = [f'{data["household_name"]} played “{SCENARIOS[data["scenario"]]["title"]}” in Family Fortunes. {preview["ending"]}.']
    paragraphs += [f'Round {r["round"]}: {r["offer"]} ' + ' '.join(r['feedback']) + (' ' + r['revelation'] if r['revelation'] else '') for r in data['history']]
    paragraphs += [f'Final provision for {s["name"]}: §{s["cash"]:,}; ' + '; '.join(a['label'] + (' — ' + a['detail'] if a['kind'] == 'promise' else '') for a in s['assets'] if a['kind'] != 'cash') + '.' for s in result['summary']]
    paragraphs += [f'Household cash retained: §{result["reserve"]:,}. Cash set aside for debt review: §{result["debts"]:,}.']
    if result['risks']:
        paragraphs.append('Unresolved objections: ' + ' '.join(result['risks']))
    paragraphs.append('These are player-approved plans and fictional negotiations, not confirmation that the actions happened in game.')
    return '\n\n'.join(paragraphs)
