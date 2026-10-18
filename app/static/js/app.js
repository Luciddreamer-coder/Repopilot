(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const sidebar = $('#sidebar');
  const closeSidebar = () => sidebar?.classList.remove('is-open');
  $('[data-sidebar-toggle]')?.addEventListener('click', () => sidebar?.classList.toggle('is-open'));
  $$('[data-sidebar-close]').forEach((button) => button.addEventListener('click', closeSidebar));

  const filterInput = $('[data-file-filter]');
  const rows = $$('[data-file-row]');
  filterInput?.addEventListener('input', (event) => {
    const query = event.target.value.trim().toLowerCase();
    rows.forEach((row) => {
      row.hidden = query && !row.dataset.fileName.includes(query);
    });
  });

  $$('[data-scroll-target]').forEach((link) => {
    link.addEventListener('click', (event) => {
      const target = document.getElementById(link.dataset.scrollTarget);
      if (target) {
        event.preventDefault();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });

  const pathInput = $('#repo-path');
  $('[data-demo-repo]')?.addEventListener('click', () => {
    if (pathInput) {
      pathInput.value = 'demo';
      pathInput.focus();
    }
    $('[data-demo-repo]')?.classList.add('is-selected');
  });
  $('.clear-input')?.addEventListener('click', () => {
    if (pathInput) {
      pathInput.value = '';
      pathInput.focus();
    }
    $('[data-demo-repo]')?.classList.remove('is-selected');
  });

  const scanForm = $('[data-scan-form]');
  const overlay = $('[data-scan-overlay]');
  scanForm?.addEventListener('submit', () => {
    if (!overlay) return;
    overlay.hidden = false;
    const progress = $('[data-scan-progress]', overlay);
    const percent = $('[data-scan-percent]', overlay);
    const status = $('[data-scan-status]', overlay);
    const messages = ['Discovering source files…', 'Mapping import relationships…', 'Scoring engineering risk…', 'Preparing your radar…'];
    let value = 0;
    const timer = window.setInterval(() => {
      value = Math.min(value + Math.floor(Math.random() * 17) + 8, 92);
      if (progress) progress.style.width = `${value}%`;
      if (percent) percent.textContent = `${value}%`;
      if (status) status.textContent = messages[Math.min(Math.floor(value / 25), messages.length - 1)];
      if (value >= 92) window.clearInterval(timer);
    }, 300);
  });

  $$('[data-copy-code]').forEach((button) => {
    button.addEventListener('click', async () => {
      const source = document.getElementById(button.dataset.copyTarget);
      if (!source) return;
      try {
        await navigator.clipboard.writeText(source.innerText);
        const original = button.innerHTML;
        button.textContent = 'Copied';
        window.setTimeout(() => { button.innerHTML = original; }, 1500);
      } catch (_error) {
        button.textContent = 'Select to copy';
      }
    });
  });

  let shortcutWaiting = false;
  document.addEventListener('keydown', (event) => {
    if (event.key === '/' && document.activeElement?.tagName !== 'INPUT') {
      event.preventDefault();
      filterInput?.focus();
    }
    if (event.key.toLowerCase() === 'g' && !event.metaKey && !event.ctrlKey && !shortcutWaiting) {
      shortcutWaiting = true;
      const finishShortcut = (keyEvent) => {
        if (keyEvent.key.toLowerCase() === 'o') window.location.href = '/dashboard';
        if (keyEvent.key.toLowerCase() === 's') window.location.href = '/scan';
        document.removeEventListener('keydown', finishShortcut);
        shortcutWaiting = false;
      };
      document.addEventListener('keydown', finishShortcut);
      window.setTimeout(() => {
        document.removeEventListener('keydown', finishShortcut);
        shortcutWaiting = false;
      }, 900);
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      openCommandPalette();
    }
  });

  const commandPalette = $('[data-command-palette]');
  const commandSearch = $('[data-command-search]');
  const commandItems = $$('[data-command-item]');
  const openCommandPalette = () => {
    if (!commandPalette) return;
    commandPalette.hidden = false;
    commandSearch?.focus();
  };
  const closeCommandPalette = () => {
    if (commandPalette) commandPalette.hidden = true;
  };
  $('[data-command-open]')?.addEventListener('click', openCommandPalette);
  $('[data-command-close]')?.addEventListener('click', closeCommandPalette);
  commandSearch?.addEventListener('input', (event) => {
    const query = event.target.value.toLowerCase().trim();
    commandItems.forEach((item) => { item.hidden = query && !item.innerText.toLowerCase().includes(query); });
  });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeCommandPalette(); });

  $$('[data-weight-range]').forEach((range) => {
    const output = range.closest('.weight-row')?.querySelector('output');
    const formulaTarget = document.querySelector(`[data-formula-${range.name === 'change_frequency' ? 'change' : range.name}]`);
    range.addEventListener('input', () => {
      if (output) output.textContent = `${range.value}%`;
      if (formulaTarget) formulaTarget.textContent = `${range.value}%`;
    });
  });
  $('[data-weight-form]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const status = $('[data-weight-status]');
    try {
      const response = await fetch(form.action, { method: 'POST', body: new FormData(form), headers: { Accept: 'application/json' } });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || 'Could not update the model.');
      if (status) status.textContent = 'Model updated. New scans will use these weights.';
    } catch (error) {
      if (status) status.textContent = error.message;
    }
  });

  const compareForm = $('[data-compare-form]');
  const compareStatus = $('[data-compare-status]');
  compareForm?.addEventListener('submit', async (event) => {
    const baseRef = compareForm.elements.base_ref?.value.trim() || '';
    const targetRef = compareForm.elements.target_ref?.value.trim() || '';
    if (!baseRef && !targetRef) return;
    event.preventDefault();
    if (!baseRef || !targetRef) {
      if (compareStatus) {
        compareStatus.textContent = 'Enter both a base and target Git ref, or clear both fields to compare saved snapshots.';
        compareStatus.hidden = false;
      }
      return;
    }
    if (baseRef === targetRef) {
      if (compareStatus) {
        compareStatus.textContent = 'Choose two different Git refs to see a comparison.';
        compareStatus.hidden = false;
      }
      return;
    }
    if (compareStatus) {
      compareStatus.textContent = 'Checking Git refs…';
      compareStatus.hidden = false;
    }
    const params = new URLSearchParams(new FormData(compareForm));
    const destination = `${compareForm.action}?${params.toString()}`;
    try {
      const response = await fetch(destination, { headers: { Accept: 'application/json' } });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || 'The Git refs could not be compared.');
      window.location.assign(destination);
    } catch (error) {
      if (compareStatus) {
        compareStatus.textContent = error.message || 'The Git refs could not be compared.';
        compareStatus.hidden = false;
      }
    }
  });
})();
