(() => {
  const root = document.querySelector('#dynasty-register');
  if (!root) return;
  const groups = [...root.querySelectorAll('[data-register-group]')];
  const search = root.querySelector('[data-register-search]');
  function filter() {
    const query = search.value.trim().toLocaleLowerCase();
    let count = 0, visibleGroups = 0;
    for (const group of groups) {
      const groupMatch = group.dataset.search.toLocaleLowerCase().includes(query);
      let visible = 0;
      for (const row of group.querySelectorAll('[data-register-person]')) {
        row.hidden = !groupMatch && !row.dataset.search.toLocaleLowerCase().includes(query);
        if (!row.hidden) { visible++; count++; }
      }
      group.hidden = Boolean(query) && !groupMatch && !visible;
      if (!group.hidden) visibleGroups++;
      if (query && !group.hidden) group.open = true;
    }
    root.querySelector('[data-register-count]').textContent = `${count} Sims shown`;
    root.querySelector('[data-register-empty]').hidden = visibleGroups > 0;
  }
  search.addEventListener('input', filter);
  root.querySelector('[data-register-expand]').addEventListener('click', () => groups.forEach(g => g.open = true));
  root.querySelector('[data-register-collapse]').addEventListener('click', () => groups.forEach(g => g.open = false));
})();
