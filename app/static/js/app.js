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
  });
})();
