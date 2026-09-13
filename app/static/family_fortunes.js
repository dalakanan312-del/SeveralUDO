/* Pointer dragging (mouse, pen and touch) plus keyboard select/place. */
(() => {
  'use strict';
  function moveCard(allocations, cards, id, target, targets) {
    const card = cards.find(c => c.id === id);
    if (!card || !targets.includes(target) || (target === 'debts' && card.kind !== 'cash')) return null;
    return {...allocations, [id]: target};
  }
  if (typeof module !== 'undefined') module.exports = {moveCard};
  if (typeof document === 'undefined') return;
  const root = document.getElementById('ff-data');
  if (!root) return;
  const config = JSON.parse(root.textContent), board = config.board;
  const error = document.getElementById('ff-error');
  let dirty = false, selected = null, busy = false;
  const announce = message => { const el = document.getElementById('ff-announcement'); if (el) el.textContent = message; };
  const fail = message => { error.textContent = message; error.hidden = false; error.scrollIntoView({block: 'center'}); };
  function markDirty() {
    dirty = true;
    const el = document.getElementById('ff-save-state'); if (el) el.textContent = 'Unsaved layout';
  }
  window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  async function send(url, data) {
    if (busy) return;
    busy = true; error.hidden = true;
    const states = [...document.querySelectorAll('.ff-shell button')].map(b => [b, b.disabled]);
    states.forEach(([b]) => { b.disabled = true; });
    try {
      const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json', 'X-Dynasty-Epoch': String(config.epoch[0] || '')},
        body: JSON.stringify({...data, save_id: config.save_id, epoch: config.epoch})});
      let result;
      try { result = await response.json(); } catch (_) { throw new Error('The tracker did not return a response. Your layout is still here; try again.'); }
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'The board could not be saved.');
      dirty = false;
      window.location.assign(result.url);
    } catch (err) {
      fail(err.message || 'Connection interrupted. Your layout is still here; try again.');
    } finally {
      busy = false; states.forEach(([b, old]) => { b.disabled = old; });
    }
  }
  const start = document.getElementById('ff-start');
  if (start) {
    const household = start.elements.household_id, budget = start.elements.budget;
    function homeChanged() {
      const home = config.homes[household.value];
      if (!home) return;
      budget.value = home.funds === null ? 1000 : Math.min(home.funds, 10000);
      document.getElementById('ff-funds-hint').textContent = home.funds === null ? 'No reported funds. Enter a purse you can actually afford.' : 'Last reported household funds: §' + home.funds.toLocaleString() + '. Nothing is spent by starting a board.';
      start.querySelectorAll('[name=beneficiary_ids]').forEach(c => { c.checked = c.closest('label').dataset.home === household.value; });
    }
    function modeChanged() { const inheritance = new FormData(start).get('mode') === 'inheritance'; start.querySelectorAll('[data-inheritance]').forEach(el => { el.hidden = !inheritance; }); }
    household.addEventListener('change', homeChanged);
    start.querySelectorAll('[name=mode]').forEach(el => el.addEventListener('change', modeChanged));
    start.querySelector('[data-search=beneficiaries]').addEventListener('input', e => {
      start.querySelectorAll('#ff-beneficiaries label').forEach(el => { el.hidden = !el.dataset.name.includes(e.target.value.trim().toLocaleLowerCase()); });
    });
    start.addEventListener('submit', e => {
      e.preventDefault(); const form = new FormData(start);
      send('/family-fortunes/start', {household_id: form.get('household_id'), mode: form.get('mode'), budget: Number(form.get('budget')),
        debts: Number(form.get('debts')), properties: form.get('properties'), beneficiary_ids: form.getAll('beneficiary_ids')});
    });
    homeChanged(); modeChanged();
  }
  if (!board) return;
  let layout = {allocations: {...board.allocations}, first_id: board.first_id, second_id: board.second_id, marriage_day: board.marriage_day};
  const people = new Map(config.people.map(p => [p.id, p]));
  const editable = board.status === 'playing' && !board.preview && board.round < 3;
  const targets = board.mode === 'matchmaking' ? ['reserve', 'offer'] : ['reserve', 'debts', ...board.beneficiary_ids];
  function node(tag, className, text) {
    const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined) el.textContent = text; return el;
  }
  function portrait(person) {
    if (person.photo) { const img = node('img', 'ff-portrait'); img.src = '/portraits/' + encodeURIComponent(person.id) + '/current'; img.alt = ''; img.loading = 'lazy'; img.addEventListener('error', () => img.replaceWith(node('span', 'ff-portrait', person.name.slice(0, 1)))); return img; }
    return node('span', 'ff-portrait', person.name.slice(0, 1));
  }
  function select(kind, id, label) {
    selected = {kind, id, label};
    document.querySelectorAll('.ff-card').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.item === id)));
    announce(label + ' selected. Choose a destination. Press Escape to cancel.');
  }
  function cardEvents(el, kind, id, label, enabled) {
    el.type = 'button'; el.dataset.item = id; el.setAttribute('aria-pressed', 'false'); el.draggable = false;
    el.disabled = !enabled;
    let pointer = null, ghost = null, moved = false, suppressClick = false;
    const clearDrag = () => {
      ghost?.remove(); ghost = null; pointer = null;
      document.querySelectorAll('.ff-drag-over').forEach(zone => zone.classList.remove('ff-drag-over'));
    };
    el.addEventListener('click', () => { if (suppressClick) { suppressClick = false; return; } select(kind, id, label); });
    el.addEventListener('pointerdown', e => {
      if (!enabled || e.button !== 0) return;
      pointer = {id:e.pointerId, x:e.clientX, y:e.clientY}; moved = false;
      el.setPointerCapture(e.pointerId);
    });
    el.addEventListener('pointermove', e => {
      if (!pointer || pointer.id !== e.pointerId) return;
      if (!moved && Math.hypot(e.clientX - pointer.x, e.clientY - pointer.y) < 8) return;
      e.preventDefault();
      if (!moved) {
        moved = true; select(kind, id, label);
        ghost = el.cloneNode(true); ghost.removeAttribute('data-item'); ghost.classList.add('ff-drag-ghost');
        ghost.style.width = Math.min(el.getBoundingClientRect().width,240) + 'px'; ghost.setAttribute('aria-hidden','true');
        document.body.append(ghost);
      }
      ghost.style.left = (e.clientX + 14) + 'px'; ghost.style.top = (e.clientY + 14) + 'px';
      const over = document.elementFromPoint(e.clientX,e.clientY)?.closest('.ff-zone');
      document.querySelectorAll('.ff-zone').forEach(zone => zone.classList.toggle('ff-drag-over',zone === over));
    });
    el.addEventListener('pointerup', e => {
      if (!pointer || pointer.id !== e.pointerId) return;
      const over = document.elementFromPoint(e.clientX,e.clientY)?.closest('.ff-zone');
      const dropped = moved; suppressClick = moved; clearDrag();
      if (dropped) { e.preventDefault(); if (over) place(over.dataset.target,over.dataset.type); else announce('Card not moved. Drop it inside a destination or use the place button.'); }
    });
    el.addEventListener('pointercancel', clearDrag);
    el.addEventListener('lostpointercapture', clearDrag);
  }
  function simCard(person) {
    const el = node('button', 'ff-card ff-person'); el.dataset.kind = 'sim';
    el.append(portrait(person), node('strong', '', person.name), node('small', '', config.homes[person.home]?.name || 'Household not recorded'));
    cardEvents(el, 'sim', person.id, person.name, editable && !board.round);
    return el;
  }
  function assetCard(card) {
    const el = node('button', 'ff-card'); el.dataset.kind = card.kind;
    el.append(node('small', '', card.kind === 'promise' ? 'PERSONAL COMMITMENT' : card.kind.toUpperCase()),
      node('strong', '', card.kind === 'cash' ? '§' + card.amount.toLocaleString() : card.label), node('small', '', card.kind === 'cash' ? card.label : card.detail));
    cardEvents(el, 'asset', card.id, card.kind === 'cash' ? card.label + ' (§' + card.amount.toLocaleString() + ')' : card.label, editable);
    return el;
  }
  function place(target, type) {
    if (!selected || !editable) { announce('Select a card first.'); return; }
    const item = selected;
    if (type === 'sim') {
      if (item.kind !== 'sim' || board.round) { announce('Choose an eligible Sim for a portrait space.'); return; }
      const person = people.get(item.id);
      if (target === 'first_id' && person.home !== board.household_id) { announce('The first partner must belong to ' + board.household_name + '.'); return; }
      const other = target === 'first_id' ? 'second_id' : 'first_id';
      if (layout[other] === item.id) layout[other] = '';
      layout[target] = item.id;
    } else {
      if (item.kind !== 'asset') { announce('Place Sims in the couple spaces, not among the assets.'); return; }
      const next = moveCard(layout.allocations, board.assets, item.id, target, targets);
      if (!next) { announce('Only cash parcels can be set aside for debts.'); return; }
      layout.allocations = next;
    }
    selected = null; markDirty(); draw();
    const focus = [...document.querySelectorAll('.ff-card')].find(el => el.dataset.item === item.id); if (focus) focus.focus({preventScroll: true});
    announce(item.label + ' placed. Save the layout or submit your offer.');
  }
  function zone(target, title, subtitle, type = 'asset', person = null) {
    const el = node('article', 'ff-zone'); el.dataset.target = target; el.dataset.type = type;
    const head = node('div', 'ff-zone-head'); if (person) head.append(portrait(person));
    const titleBox = node('div'); titleBox.append(node('h3', '', title), node('span', '', subtitle)); head.append(titleBox);
    el.append(head);
    const cards = node('div', 'ff-zone-cards'); el.append(cards);
    const button = node('button', 'ff-place', type === 'sim' ? 'Place selected Sim here' : 'Place selected card here'); button.type = 'button';
    button.setAttribute('aria-label', 'Place selection in ' + title); button.disabled = !editable || (type === 'sim' && board.round > 0);
    button.addEventListener('click', () => place(target, type)); el.append(button);
    el.addEventListener('dragover', e => { if (selected && editable) { e.preventDefault(); el.classList.add('ff-drag-over'); } });
    el.addEventListener('dragleave', e => { if (!el.contains(e.relatedTarget)) el.classList.remove('ff-drag-over'); });
    el.addEventListener('drop', e => { e.preventDefault(); el.classList.remove('ff-drag-over'); place(target, type); });
    return {el, cards};
  }
  function draw() {
    const reserve = document.getElementById('ff-reserve'); if (!reserve) return;
    const destinations = document.getElementById('ff-destinations'); reserve.replaceChildren(); destinations.replaceChildren();
    const boxes = new Map();
    for (const target of targets) {
      const person = people.get(target);
      const title = target === 'reserve' ? 'Household reserve' : target === 'offer' ? 'The proposed dowry' : target === 'debts' ? 'Set aside for debts' : person?.name || 'Unavailable beneficiary';
      const cards = board.assets.filter(a => (layout.allocations[a.id] || 'reserve') === target);
      const amount = cards.reduce((total, a) => total + (a.kind === 'cash' ? a.amount : 0), 0);
      const box = zone(target, title, '§' + amount.toLocaleString() + ' · ' + cards.length + ' cards', 'asset', person);
      cards.forEach(a => box.cards.append(assetCard(a))); boxes.set(target, box);
      (target === 'reserve' ? reserve : destinations).append(box.el);
    }
    const couple = document.getElementById('ff-couple');
    if (couple) {
      couple.replaceChildren();
      for (const key of ['first_id', 'second_id']) {
        const box = zone(key, key === 'first_id' ? 'Your household’s Sim' : 'Their prospective partner', board.round ? 'Couple fixed for these negotiations' : 'Drop a portrait here', 'sim');
        const person = people.get(layout[key]); if (person) box.cards.append(simCard(person)); couple.append(box.el);
      }
      const candidates = document.getElementById('ff-candidates');
      if (candidates) {
        candidates.replaceChildren();
        const search = (document.getElementById('ff-sim-search')?.value || '').trim().toLocaleLowerCase();
        config.eligible_ids.forEach(id => { const person = people.get(id); if (person && id !== layout.first_id && id !== layout.second_id && person.name.toLocaleLowerCase().includes(search)) candidates.append(simCard(person)); });
        if (!candidates.children.length) candidates.append(node('p', 'muted', 'No other eligible Sims match this search.'));
      }
    }
  }
  document.getElementById('ff-sim-search')?.addEventListener('input', draw);
  document.getElementById('ff-marriage-day')?.addEventListener('input', e => { layout.marriage_day = Number(e.target.value); markDirty(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') { selected = null; document.querySelectorAll('.ff-card').forEach(el => el.setAttribute('aria-pressed', 'false')); announce('Selection cleared.'); } });
  document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => send('/family-fortunes/' + config.game_id + '/action', {action: button.dataset.action, version: config.version, layout})));
  draw();
})();
