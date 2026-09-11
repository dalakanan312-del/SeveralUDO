(() => {
  const form = document.querySelector('[data-portrait-generate]');
  if (form) {
    const dates = JSON.parse(form.dataset.stageDates);
    form.elements.stage.addEventListener('change', () => {
      const info = dates[form.elements.stage.value];
      form.elements.year.value = info.year ?? '';
      form.querySelector('[data-stage-date-note]').textContent = `${info.basis} · ${info.detail}`;
    });
    form.elements.source_id.addEventListener('change', () => {
      const option = form.elements.source_id.selectedOptions[0];
      document.getElementById('studio-reference-image').src = option.dataset.photoUrl;
    });
    form.addEventListener('submit', event => {
      if (form.dataset.submitted) { event.preventDefault(); return; }
      form.dataset.submitted = '1';
      const button = form.querySelector('button[type=submit]');
      button.disabled = true;
      button.textContent = 'Starting portrait…';
      form.querySelector('[data-generation-message]').textContent = 'Opening the gallery. Keep using the tracker while your portrait is generated.';
    });
  }
  const cards = [...document.querySelectorAll('[data-portrait-job]')];
  async function check() {
    let pending = false;
    for (const card of cards) {
      if (!card.dataset.portraitJob) continue;
      try {
        const response = await fetch(`/portrait-studio/jobs/${card.dataset.portraitJob}`, {cache:'no-store'});
        if (!response.ok) { pending = true; continue; }
        const data = await response.json();
        if (data.status === 'complete') {
          // Finishing one portrait must not discard art direction the player
          // is entering for their next one, or move the page's scroll position.
          const job = card.dataset.portraitJob;
          const url = `/portraits/${job}/generated`;
          const link = document.createElement('a');
          link.href = url; link.target = '_blank'; link.rel = 'noopener';
          const image = document.createElement('img');
          image.src = url; image.alt = 'Completed AI historical portrait';
          link.append(image);
          card.querySelector('.studio-pending').replaceWith(link);
          const download = document.createElement('a');
          download.className = 'button'; download.href = url;
          download.download = `portrait-${job}.webp`; download.textContent = 'Download';
          card.querySelector('.button-row').append(download);
          card.querySelector('[data-archive-portrait]').disabled = false;
          delete card.dataset.portraitJob;
          continue;
        }
        if (data.status === 'failed' || data.status === 'interrupted') {
          card.querySelector('[data-job-status]').textContent = data.status === 'failed' ? 'Not generated' : 'Generation interrupted';
          card.querySelector('[data-job-error]').textContent = data.error || 'The tracker may have restarted. This job will not automatically retry or charge again. Check provider usage before making a new request.';
          card.querySelector('[data-archive-portrait]').disabled = false;
          delete card.dataset.portraitJob;
        } else pending = true;
      } catch (_) { pending = true; }
    }
    if (pending) setTimeout(check, 5000);
  }
  if (cards.length) setTimeout(check, 2000);
})();
