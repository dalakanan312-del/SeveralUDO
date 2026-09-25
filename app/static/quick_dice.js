(function () {
  'use strict';
  function valid(item) {
    return !!item && typeof item.id === 'string' && typeof item.notation === 'string'
      && typeof item.question === 'string' && typeof item.coin === 'string'
      && Number.isFinite(item.total) && Number.isFinite(item.modifier)
      && Array.isArray(item.faces) && item.faces.length > 0 && item.faces.length <= 100
      && item.faces.every(n => Number.isInteger(n) && n >= 1 && n <= 1000);
  }
  function addHistory(history, item) {
    const clean = Array.isArray(history) ? history.filter(valid) : [];
    return valid(item) ? [item, ...clean.filter(r => r.id !== item.id)].slice(0, 10) : clean.slice(0, 10);
  }
  function breakdown(item) {
    return item.notation + ' · ' + item.faces.join(' + ')
      + (item.modifier ? (item.modifier > 0 ? ' + ' : ' − ') + Math.abs(item.modifier) : '')
      + ' = ' + item.total;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {valid, addHistory, breakdown};
  if (typeof document === 'undefined') return;
  function init() {
    const root = document.getElementById('quick-dice');
    if (!root || root.dataset.ready) return;
    root.dataset.ready = 'true';
    const result = root.querySelector('#dice-result'), status = root.querySelector('#quick-dice-status');
    const list = root.querySelector('#quick-dice-history'), clear = root.querySelector('#clear-quick-dice');
    const key = root.dataset.historyKey;
    let history = [], busy = false, latestId = '';
    try { history = addHistory(JSON.parse(localStorage.getItem(key) || '[]'), null); } catch (_) {}
    function element(tag, text, className) {
      const el = document.createElement(tag); el.textContent = text;
      if (className) el.className = className;
      return el;
    }
    function show(item) {
      result.replaceChildren(element('p', item.question || item.notation, 'quick-dice-question'),
        element('strong', item.coin || String(item.total), 'quick-dice-total'), element('p', breakdown(item)));
    }
    function renderHistory() {
      list.replaceChildren(...history.map(item => {
        const li = element('li', (item.question ? item.question + ' — ' : '') + (item.coin || item.total));
        li.append(element('small', breakdown(item))); return li;
      }));
      clear.hidden = history.length === 0;
    }
    function remember(item) {
      latestId = item.id;
      history = addHistory(history, item); renderHistory();
      try { localStorage.setItem(key, JSON.stringify(history)); }
      catch (_) { root.querySelector('#quick-dice-history-note').textContent = 'Browser storage is unavailable. Recent throws will last until you leave this page.'; }
    }
    try {
      const initial = JSON.parse(root.querySelector('#quick-dice-initial').textContent);
      if (valid(initial)) {
        latestId = initial.id;
        let cleared = ''; try { cleared = localStorage.getItem(key + '-cleared') || ''; } catch (_) {}
        if (cleared !== initial.id) remember(initial);
      }
    } catch (_) {}
    renderHistory();
    clear.addEventListener('click', () => {
      history = []; renderHistory();
      try { localStorage.removeItem(key); localStorage.setItem(key + '-cleared', latestId); } catch (_) {}
    });
    root.querySelectorAll('[data-quick-dice-form]').forEach(form => form.addEventListener('submit', async event => {
      if (!window.fetch || !window.FormData) return; // Normal form POST remains usable.
      event.preventDefault();
      if (busy) return;
      const data = new FormData(form);
      if (event.submitter && event.submitter.name) data.set(event.submitter.name, event.submitter.value);
      if (!data.get('notation')) data.set('notation', form.querySelector('button[name="notation"]')?.value || 'd20');
      busy = true; root.setAttribute('aria-busy', 'true'); status.textContent = 'Rolling…';
      const buttons = [...root.querySelectorAll('button[type="submit"], [data-quick-dice-form] button:not([type])')];
      buttons.forEach(button => { button.disabled = true; });
      try {
        const response = await fetch(form.action, {method: 'POST', body: data, credentials: 'same-origin', headers: {'Accept': 'application/json'}});
        const item = await response.json();
        if (!response.ok) throw new Error(typeof item.detail === 'string' ? item.detail : 'Could not roll. Please try again.');
        if (!valid(item)) throw new Error('The result could not be read. Please try again.');
        show(item); remember(item); status.textContent = '';
        if (window.matchMedia('(max-width:760px)').matches) result.scrollIntoView({block: 'nearest'});
      } catch (error) { status.textContent = error.message || 'Connection lost. Please try again.'; }
      finally { busy = false; root.removeAttribute('aria-busy'); buttons.forEach(button => { button.disabled = false; }); }
    }));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
  document.addEventListener('htmx:afterSwap', init);
})();
