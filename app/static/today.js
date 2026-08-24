function isBadRoll(actual, source) {
  const text = String(source || "").replace(/[–—�]/g, "-");
  let matched = false;
  const withoutRanges = text.replace(/(-?\d+)\s*-\s*(-?\d+)/g, (_, first, second) => {
    const low = Math.min(Number(first), Number(second));
    const high = Math.max(Number(first), Number(second));
    if (actual >= low && actual <= high) matched = true;
    return " ";
  });
  if (matched) return true;
  return (withoutRanges.match(/-?\d+/g) || []).some(value => Number(value) === actual);
}

document.addEventListener("input", event => {
  if (!event.target.matches(".manual-actual")) return;
  const form = event.target.closest(".manual-roll-form");
  const outcome = form && form.querySelector(".manual-outcome");
  const actual = Number(event.target.value);
  if (form && form.dataset.dynamic === "pregnancy-count") {
    if (outcome) outcome.value = "";
    return;
  }
  if (outcome && Number.isFinite(actual)) outcome.value = isBadRoll(actual, form.dataset.bad) ? "Failed" : "Passed";
});
