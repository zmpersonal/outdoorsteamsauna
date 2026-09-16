document.addEventListener('DOMContentLoaded', () => {
  const search = document.querySelector('#citySearch');
  if (search) {
    search.addEventListener('input', () => {
      const query = search.value.toLowerCase().trim();
      document.querySelectorAll('[data-city-row]').forEach((row) => {
        row.style.display = row.dataset.cityRow.includes(query) ? '' : 'none';
      });
    });
  }

  const costForm = document.querySelector('#costForm');
  if (costForm) {
    const calculate = () => {
      const kw = Math.max(0, Number(costForm.kw.value) || 0);
      const rate = Math.max(0, Number(costForm.rate.value) || 0);
      const warm = Math.max(0, Number(costForm.warm.value) || 0);
      const session = Math.max(0, Number(costForm.session.value) || 0);
      const frequency = Math.max(0, Number(costForm.freq.value) || 0);
      const kwh = kw * ((warm / 60) + (session / 60) * 0.6);
      const cost = kwh * rate / 100;
      document.querySelector('#costResult').innerHTML = `<strong>$${cost.toFixed(2)}</strong><br>estimated electricity per session · ${kwh.toFixed(1)} kWh<br><span class="muted">≈ $${(cost * frequency * 52).toFixed(0)}/year at ${frequency} sessions/week</span>`;
    };
    costForm.addEventListener('input', calculate);
    calculate();
  }

  const heaterForm = document.querySelector('#heaterForm');
  if (heaterForm) {
    const calculate = () => {
      const length = Math.max(0, Number(heaterForm.length.value) || 0);
      const width = Math.max(0, Number(heaterForm.width.value) || 0);
      const height = Math.max(0, Number(heaterForm.height.value) || 0);
      const glass = Math.max(0, Number(heaterForm.glass.value) || 0);
      const raw = length * width * height;
      const effective = raw + glass * 3.3;
      const matches = (window.HEATERS || []).filter((item) => effective >= item.min_ft3 && effective <= item.max_ft3);
      const matchText = matches.length
        ? `<b>Published reference ranges containing this volume:</b><br>${matches.map((item) => `${item.brand} ${item.model} · ${item.kw} kW · ${item.min_ft3}–${item.max_ft3} ft³`).join('<br>')}`
        : 'No heater in the small reference sample spans this effective volume. Use the manufacturer’s current sizing tool.';
      document.querySelector('#heaterResult').innerHTML = `<strong>${effective.toFixed(0)} ft³</strong><br>effective planning volume <span class="muted">(${raw.toFixed(0)} ft³ room + ${(glass * 3.3).toFixed(0)} ft³ surface adjustment)</span><br><br>${matchText}`;
    };
    heaterForm.addEventListener('input', calculate);
    calculate();
  }

  document.querySelectorAll('[data-copy]').forEach((button) => {
    button.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copy);
        const original = button.textContent;
        button.textContent = 'Copied';
        setTimeout(() => { button.textContent = original; }, 1600);
      } catch (_) {
        button.textContent = 'Select and copy manually';
      }
    });
  });
});
