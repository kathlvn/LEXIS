// Settings page. Head Attorney only: the
// sidebar hides the link from everyone else and the server refuses them
// (routers/system_settings.py). The server checks every value against its
// limits; the checks here only make the form friendlier.
//
// Number settings share one "Client limits" card, one row each. Each wording
// setting (the endorsement letter template) gets a card of its own.
(function () {
  renderTopbar();
  renderSidebar('settings');

  const $ = id => document.getElementById(id);
  const list = $('settingsList');
  const NOTICE_ICONS = {ok: ICONS.check, error: ICONS.noticeError};

  // Shown beside each number box, and (for days only) in the range line.
  const UNITS = {
    verification_valid_days: {unit: 'days', range: 'days'},
    unverified_max_open_requests: {unit: 'open requests'},
    max_requests_per_day: {unit: 'requests'}
  };

  // Every card on the page, so leaving can check for unsaved edits.
  const cards = [];

  function friendlyError(error) {
    if (typeof error === 'string') return error;
    if (Array.isArray(error) && error[0] && error[0].msg) return error[0].msg.replace(/^Value error, /, '');
    return 'Something went wrong. Please try again.';
  }

  function pageMessage(text, tone) {
    const box = $('settingsPageMsg');
    box.innerHTML = text
      ? `<p class="page-msg ${tone}"><span class="page-msg-icon">${NOTICE_ICONS[tone]}</span><span>${escapeHtml(text)}</span></p>`
      : '';
  }

  // The line under a setting's buttons: done (ok), nothing to do (note) or what went wrong (error).
  function lineStatus(el, text, tone) {
    el.className = `set-status ${tone || 'ok'}`;
    el.setAttribute('role', tone === 'error' ? 'alert' : 'status');
    el.innerHTML = text ? `${tone === 'ok' ? ICONS.check : ''}<span>${escapeHtml(text)}</span>` : '';
  }

  const num = n => Number(n).toLocaleString('en-US');

  function changedLine(s) {
    if (!s.updated_by_name || !s.updated_at) return 'Not changed yet (using the default).';
    return `Last changed by ${s.updated_by_name} on ${formatLongDate(s.updated_at)}.`;
  }

  // The same clean-up the server does before saving text, so "no changes" is judged the same way.
  function cleanText(text) {
    return text.replace(/\r\n/g, '\n').split('\n').map(line => line.replace(/\s+$/, '')).join('\n').trim();
  }

  function isChanged(s, input) {
    if (s.kind === 'text') return cleanText(input.value) !== cleanText(s.value);
    return input.value.trim() !== String(s.value);
  }

  /* ---------------- Saving (shared by both kinds of card) ---------------- */
  // holder = {s, el, input, status}; draw = re-draws the holder from the saved setting.
  async function save(holder, value, what, draw) {
    const {s, el, status} = holder;
    lineStatus(status, '');
    if (s.kind !== 'text') {
      const n = Number(value);
      if (!/^\d+$/.test(String(value).trim()) || n < s.min || n > s.max) {
        lineStatus(status, `Enter a whole number from ${num(s.min)} to ${num(s.max)}.`, 'error');
        holder.input.focus();
        return;
      }
    } else if (!cleanText(value) && !(await confirmDialog({
      title: 'Save an empty template?', message: 'Clients will no longer see a download link for it.',
      confirmLabel: 'Save empty template', danger: true
    }))) {
      return;
    }
    const btns = el.querySelectorAll('button');
    btns.forEach(b => { b.disabled = true; });
    const r = await apiFetch(`/settings/${encodeURIComponent(s.key)}`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({value: String(value).trim()})
    });
    if (!r.ok) {
      btns.forEach(b => { b.disabled = false; });
      lineStatus(status, friendlyError(r.error), 'error');
      return;
    }
    holder.s = r.data;
    draw();
    pageMessage(`${what}: ${r.data.label}.`, 'ok');
    // The template card is taller than the screen, so it says so beside its button too.
    if (r.data.kind === 'text') lineStatus(holder.status, `${what}.`, 'ok');
  }

  function onSave(holder, draw) {
    if (!isChanged(holder.s, holder.input)) {
      lineStatus(holder.status, 'No changes to save.', 'note');
      return;
    }
    save(holder, holder.input.value, 'Saved', draw);
  }

  async function onReset(holder, draw) {
    const s = holder.s;
    if (await confirmDialog({
      title: `Reset “${s.label}”?`,
      message: `It goes back to the default (${s.kind === 'text' ? 'the original wording' : num(s.default)}).`,
      confirmLabel: 'Reset to default'
    })) save(holder, s.default, 'Reset to default', draw);
  }

  /* ---------------- Number settings: one row each ---------------- */
  function numberRow(setting) {
    const row = document.createElement('div');
    row.className = 'set-row';
    const holder = {s: setting, el: row};
    cards.push(holder);

    function draw() {
      const s = holder.s;
      const u = UNITS[s.key] || {};
      const id = `set_${s.key}`;
      row.innerHTML = `
        <div class="set-text">
          <label class="set-name" for="${id}">${escapeHtml(s.label)}</label>
          <p class="set-help" id="${id}_help">${escapeHtml(s.help)}</p>
          <p class="set-meta">${escapeHtml(changedLine(s))}</p>
        </div>
        <div class="set-control">
          <div class="set-value">
            <input type="number" class="rd-input set-num" id="${id}" min="${s.min}" max="${s.max}" step="1"
              value="${escapeHtml(s.value)}" inputmode="numeric" aria-describedby="${id}_range ${id}_help">
            ${u.unit ? `<span class="set-unit">${escapeHtml(u.unit)}</span>` : ''}
          </div>
          <p class="set-range" id="${id}_range">From ${num(s.min)} to ${num(s.max)}${u.range ? ' ' + escapeHtml(u.range) : ''} · default ${escapeHtml(num(s.default))}</p>
          <div class="set-actions">
            ${s.value !== s.default ? '<button type="button" class="btn btn-tertiary btn-compact" data-action="reset">Reset to default</button>' : ''}
            <button type="button" class="btn btn-primary btn-compact" data-action="save">Save</button>
          </div>
          <p class="set-status"></p>
        </div>`;
      holder.input = row.querySelector('input');
      holder.status = row.querySelector('.set-status');
    }
    draw();

    row.addEventListener('click', e => {
      const btn = e.target.closest('button[data-action]');
      if (!btn) return;
      if (btn.dataset.action === 'save') onSave(holder, draw);
      else onReset(holder, draw);
    });
    row.addEventListener('keydown', e => {
      if (e.key === 'Enter' && e.target === holder.input) { e.preventDefault(); onSave(holder, draw); }
    });
    row.addEventListener('input', () => {
      if (!holder.status.classList.contains('error')) lineStatus(holder.status, '');
    });
    return row;
  }

  function limitsCard(settings) {
    const card = document.createElement('section');
    card.className = 'rd-card set-card set-group';
    card.setAttribute('aria-labelledby', 'setLimitsTitle');
    card.innerHTML = `
      <div class="set-card-head">
        <h2 class="rd-card-title" id="setLimitsTitle">Client limits</h2>
        <p class="set-card-sub">Rules that apply when clients send requests. The server refuses any value outside the range shown.</p>
      </div>`;
    settings.forEach(s => card.appendChild(numberRow(s)));
    return card;
  }

  /* ---------------- Wording settings: a card each ---------------- */
  function textCard(setting) {
    const card = document.createElement('section');
    card.className = 'rd-card set-card set-text-card';
    const holder = {s: setting, el: card};
    cards.push(holder);

    function draw() {
      const s = holder.s;
      const id = `set_${s.key}`;
      card.setAttribute('aria-labelledby', `${id}_title`);
      card.innerHTML = `
        <div class="set-card-head">
          <h2 class="rd-card-title" id="${id}_title">${escapeHtml(s.label)}</h2>
          <p class="set-card-sub" id="${id}_help">${escapeHtml(s.help)}</p>
        </div>
        <textarea class="rd-textarea set-textarea" id="${id}" maxlength="${s.max}" spellcheck="true"
          aria-labelledby="${id}_title" aria-describedby="${id}_help">${escapeHtml(s.value)}</textarea>
        <div class="set-meta-row">
          <p class="set-meta">${escapeHtml(changedLine(s))}</p>
          <p class="set-meta set-count"><span class="set-len">${num(s.value.length)}</span> / ${num(s.max)} characters</p>
        </div>
        <div class="set-actions set-actions-end">
          <p class="set-status"></p>
          ${s.value ? '<button type="button" class="btn btn-secondary btn-compact" data-action="download">Download as clients get it</button>' : ''}
          ${s.value !== s.default ? '<button type="button" class="btn btn-tertiary btn-compact" data-action="reset">Reset to default</button>' : ''}
          <button type="button" class="btn btn-primary btn-compact" data-action="save">Save</button>
        </div>`;
      holder.input = card.querySelector('textarea');
      holder.status = card.querySelector('.set-status');
    }
    draw();

    card.addEventListener('click', e => {
      const btn = e.target.closest('button[data-action]');
      if (!btn) return;
      if (btn.dataset.action === 'save') onSave(holder, draw);
      else if (btn.dataset.action === 'reset') onReset(holder, draw);
      else download(holder.s);
    });
    card.addEventListener('input', () => {
      card.querySelector('.set-len').textContent = num(holder.input.value.length);
      if (!holder.status.classList.contains('error')) lineStatus(holder.status, '');
    });
    return card;
  }

  // The SAVED wording, exactly as clients download it (Windows line endings).
  function download(s) {
    const blob = new Blob([s.value.replace(/\r?\n/g, '\r\n')], {type: 'text/plain;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'ESSU-Legal-Office-endorsement-letter-template.txt';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  // Leaving with an edit not yet saved: the browser asks first.
  window.addEventListener('beforeunload', e => {
    if (cards.some(h => h.input && isChanged(h.s, h.input))) { e.preventDefault(); e.returnValue = ''; }
  });

  async function load() {
    const r = await apiFetch('/settings');
    if (!r.ok) {
      list.innerHTML = '';
      pageMessage(r.status === 403 ? 'Only the Head Attorney can change settings.' : friendlyError(r.error), 'error');
      return;
    }
    list.innerHTML = '';
    const numbers = r.data.filter(s => s.kind !== 'text');
    if (numbers.length) list.appendChild(limitsCard(numbers));
    r.data.filter(s => s.kind === 'text').forEach(s => list.appendChild(textCard(s)));
  }

  load();
})();
