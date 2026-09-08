/* Keep old tabs from applying an action to a different dynasty branch. */
(() => {
  const epoch = () => document.querySelector('main')?.dataset.dynastyEpoch || '';
  function markForms() {
    document.querySelectorAll('form').forEach(form => {
      if ((form.method || 'get').toLowerCase() === 'get') return;
      const value = form.dataset.dynastyEpoch ?? epoch();
      const url = new URL(form.getAttribute('action') || location.href, location.href);
      if (url.origin !== location.origin) return;
      if (value) url.searchParams.set('_dynasty_epoch', value);
      else url.searchParams.delete('_dynasty_epoch');
      form.action = url.pathname + url.search + url.hash;
    });
  }
  markForms();
  document.addEventListener('submit', markForms, true);
  document.addEventListener('htmx:afterSwap', markForms);
  document.addEventListener('htmx:configRequest', event => {
    if (epoch()) event.detail.headers['X-Dynasty-Epoch'] = epoch();
  });
  const originalFetch = window.fetch;
  window.fetch = function(input, init) {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (epoch() && url.origin === location.origin && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined));
      headers.set('X-Dynasty-Epoch', epoch());
      init = {...init, headers};
    }
    return originalFetch.call(this, input, init);
  };
})();
