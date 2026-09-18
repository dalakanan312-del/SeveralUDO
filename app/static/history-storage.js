(() => {
  const start = document.getElementById('compact-all-history');
  if (!start || start.dataset.ready) return;
  start.dataset.ready = 'true';
  const stop = document.getElementById('stop-history-compaction');
  const status = document.getElementById('history-compaction-status');
  let cancelled = false;
  stop.addEventListener('click', () => { cancelled = true; stop.disabled = true; });
  start.addEventListener('click', async () => {
    start.disabled = true; stop.hidden = false; stop.disabled = false; cancelled = false;
    let cursor = 0, scanned = 0, compressed = 0;
    try {
      while (!cancelled && start.isConnected) {
        const response = await fetch('/api/health/compact-history', {
          method: 'POST', headers: { Accept: 'application/json' },
          body: new URLSearchParams({cursor, save_id: start.dataset.save})
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'Storage maintenance could not continue.');
        cursor = result.cursor; scanned += result.scanned; compressed += result.compressed;
        status.textContent = `Checked ${scanned.toLocaleString()} entries; compressed ${compressed.toLocaleString()}. All history is retained.`;
        if (!result.scanned) { status.textContent += ' Complete.'; break; }
        await new Promise(resolve => setTimeout(resolve, 200));
      }
      if (cancelled) status.textContent += ' Stopped safely. You can resume later.';
    } catch (error) { status.textContent = error.message + ' Completed batches are safe to keep; retry when ready.'; }
    finally { start.disabled = false; stop.hidden = true; }
  });
})();
