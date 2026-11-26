// Holidays page (calendar + list, and the
// "Check proclamation" results). Everyone on staff can view the
// calendar and the list; only the Head Attorney sees "Check proclamation",
// "Add a date" and the Remove links, and only the Head Attorney can click an
// empty date to add it. The server enforces the same rule
// (routers/holidays.py), so hiding them here is only for a cleaner page.
(function () {
  const TYPE_LABELS = {
    regular: 'Regular holiday',
    special: 'Special non-working day',
    local: 'Local holiday',
    suspension: 'Work suspension',
  };
  // Shorter names for the type pills.
  const TAG_LABELS = {
    regular: 'Regular',
    special: 'Special non-working',
    local: 'Local',
    suspension: 'Work suspension',
  };
  // Pill colours: the same tones as the status pills elsewhere in the console.
  const TAG_TONES = {
    regular: 'tone-attorney',
    special: 'tone-progress',
    local: 'tone-new',
    suspension: 'tone-neutral',
  };
  const MONTHS = ['January','February','March','April','May','June','July',
                  'August','September','October','November','December'];
  const MONTHS_SHORT = MONTHS.map(m => m.slice(0, 3));
  const WEEKDAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const NOTICE_ICONS = {ok: ICONS.check, error: ICONS.noticeError, warn: ICONS.noticeWarning, info: ICONS.noticeInfo};

  renderTopbar();
  renderSidebar('holidays');

  const $ = id => document.getElementById(id);
  const table = $('holidayTable');
  const yearSelect = $('holidayYear');
  const addCard = $('holAddCard');
  const form = $('holidayForm');
  const formMessage = $('holidayFormMessage');
  const submitBtn = $('holidayFormSubmitBtn');
  const calGrid = $('calGrid');

  $('procBackBtn').innerHTML = ICONS.back;
  $('calPrev').innerHTML = ICONS.chevRight;
  $('calNext').innerHTML = ICONS.chevRight;

  const today = new Date();
  let calYear = today.getFullYear();
  let calMonth = today.getMonth();        // 0-11
  let holidays = [];                      // the loaded year's rows
  let loadedYear = null;

  function isHeadAttorney() {
    return !!(window.currentStaff && window.currentStaff.role === 'head_attorney');
  }

  function friendlyError(error) {
    if (typeof error === 'string') return error;
    if (Array.isArray(error) && error[0] && error[0].msg) return error[0].msg.replace(/^Value error, /, '');
    return 'Please check the form and try again.';
  }

  function plural(n, word) {
    return `${n} ${word}${n === 1 ? '' : 's'}`;
  }

  // Dates are handled as "YYYY-MM-DD" strings and built as local dates, so
  // the day of the week is never shifted by time zones.
  function parseDate(iso) {
    const [y, m, d] = iso.split('-').map(Number);
    return new Date(y, m - 1, d);
  }
  function isoDate(y, m, d) {
    return `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  }
  function shortDate(d) {                 // "30 Nov 2026"
    return `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]} ${d.getFullYear()}`;
  }

  function typePill(type, extraClass) {
    return `<span class="status-label ${TAG_TONES[type] || 'tone-neutral'}${extraClass ? ' ' + extraClass : ''}" title="${escapeHtml(TYPE_LABELS[type] || type)}">${escapeHtml(TAG_LABELS[type] || type)}</span>`;
  }

  /* ---------------- Messages (shared .page-msg banner) ---------------- */
  function noticeHtml(text, tone) {
    return `<span class="page-msg-icon">${NOTICE_ICONS[tone]}</span><span>${escapeHtml(text)}</span>`;
  }
  function msgHtml(text, tone) {
    return `<p class="page-msg ${tone}">${noticeHtml(text, tone)}</p>`;
  }
  // One or more messages above the calendar: [[text, tone], ...]
  function pageMessages(list) {
    $('holPageMsg').innerHTML = list.filter(m => m && m[0]).map(m => msgHtml(m[0], m[1])).join('');
  }
  function pageMessage(text, tone) {
    pageMessages(text ? [[text, tone || 'ok']] : []);
  }

  /* ---------------- Year ---------------- */
  function ensureYearOption(y) {
    if (![...yearSelect.options].some(o => Number(o.value) === y)) {
      const opt = document.createElement('option');
      opt.value = y;
      opt.textContent = y;
      yearSelect.appendChild(opt);
      [...yearSelect.options]
        .sort((a, b) => Number(a.value) - Number(b.value))
        .forEach(o => yearSelect.appendChild(o));
    }
    yearSelect.value = String(y);
  }
  for (let y = today.getFullYear() - 1; y <= today.getFullYear() + 2; y++) ensureYearOption(y);
  yearSelect.value = String(calYear);

  yearSelect.addEventListener('change', () => {
    calYear = Number(yearSelect.value);
    load();
  });

  $('calPrev').addEventListener('click', () => moveMonth(-1));
  $('calNext').addEventListener('click', () => moveMonth(1));

  function moveMonth(step) {
    calMonth += step;
    if (calMonth < 0) { calMonth = 11; calYear -= 1; }
    if (calMonth > 11) { calMonth = 0; calYear += 1; }
    if (calYear !== loadedYear) {
      ensureYearOption(calYear);
      load();                // new year: fetch its dates, then redraw both
    } else {
      renderCalendar();
    }
  }

  /* ---------------- Calendar ---------------- */
  function renderCalendar() {
    $('calTitle').textContent = `${MONTHS[calMonth]} ${calYear}`;
    const byDate = {};
    holidays.forEach(h => { byDate[h.holiday_date] = h; });

    const first = new Date(calYear, calMonth, 1).getDay();
    const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
    const todayIso = isoDate(today.getFullYear(), today.getMonth(), today.getDate());
    const canAdd = isHeadAttorney();

    let html = '';
    for (let i = 0; i < first; i++) html += '<div class="hol-day blank" aria-hidden="true"></div>';
    for (let d = 1; d <= daysInMonth; d++) {
      const iso = isoDate(calYear, calMonth, d);
      const dow = (first + d - 1) % 7;
      const h = byDate[iso];
      const classes = ['hol-day'];
      if (dow === 0 || dow === 6) classes.push('weekend');
      if (h) classes.push('holiday', h.holiday_type);
      if (iso === todayIso) classes.push('today');

      const label = h
        ? `${MONTHS[calMonth]} ${d}: ${h.name} (${TYPE_LABELS[h.holiday_type] || h.holiday_type})`
        : `${MONTHS[calMonth]} ${d}${iso === todayIso ? ' (today)' : ''}${canAdd ? ': add a date' : ''}`;
      const inner = `<span class="hol-day-n">${d}</span>${h ? `<i class="hol-dot ${escapeHtml(h.holiday_type)}"></i>` : ''}`;

      // Holidays are clickable for everyone (finds the list row); empty
      // dates only for the Head Attorney (opens the Add form).
      if (h || canAdd) {
        html += `<button type="button" class="${classes.join(' ')}" data-date="${iso}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}">${inner}</button>`;
      } else {
        html += `<div class="${classes.join(' ')}" title="${escapeHtml(label)}"><span class="visually-hidden">${escapeHtml(label)}</span><span aria-hidden="true">${inner}</span></div>`;
      }
    }
    calGrid.innerHTML = html;

    calGrid.querySelectorAll('button[data-date]').forEach(btn => {
      btn.addEventListener('click', () => {
        const h = byDate[btn.dataset.date];
        if (h) highlightRow(h.id);
        else openAddForm(btn.dataset.date);
      });
    });

    // "In November": the month's dates under the grid
    const inMonth = holidays.filter(h => {
      const d = parseDate(h.holiday_date);
      return d.getFullYear() === calYear && d.getMonth() === calMonth;
    });
    $('calMonthLabel').textContent = `In ${MONTHS[calMonth]}`;
    $('calMonthItems').innerHTML = inMonth.length
      ? inMonth.map(h => {
          const d = parseDate(h.holiday_date);
          return `<li><i class="hol-dot ${escapeHtml(h.holiday_type)}" title="${escapeHtml(TYPE_LABELS[h.holiday_type] || h.holiday_type)}"></i>
            <span class="hol-item-date">${d.getDate()} ${MONTHS_SHORT[calMonth]}</span>
            <span class="hol-item-name">${escapeHtml(h.name)}</span></li>`;
        }).join('')
      : '<li class="hol-item-none">No non-working days this month.</li>';

    $('calHint').textContent = canAdd
      ? 'Click an empty date to add it, or a holiday to find it in the list.'
      : 'Click a holiday to find it in the list.';
  }

  function highlightRow(id) {
    const row = table.querySelector(`tr[data-row-id="${id}"]`);
    if (!row) return;
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    row.classList.remove('row-flash');
    void row.offsetWidth;                 // restart the animation
    row.classList.add('row-flash');
  }

  /* ---------------- List ---------------- */
  async function load() {
    const year = calYear;
    $('holListTitle').textContent = `Dates in ${year}`;
    const { ok, data, error } = await apiFetch(`/holidays?year=${encodeURIComponent(year)}`);
    if (year !== calYear) return;          // the user moved on while this loaded
    table.querySelectorAll('tbody').forEach(tb => tb.remove());
    const tbody = document.createElement('tbody');
    const foot = $('holidayFoot');
    const canEdit = isHeadAttorney();
    table.classList.toggle('no-actions', !canEdit);
    if (!ok) {
      tbody.innerHTML = `<tr><td colspan="5" class="hol-empty">${escapeHtml(friendlyError(error) || 'Could not load the holidays.')}</td></tr>`;
      holidays = [];
      loadedYear = null;
      foot.hidden = true;
      table.appendChild(tbody);
      renderCalendar();
      return;
    }
    holidays = data;
    loadedYear = year;
    yearSelect.value = String(year);

    if (!data.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="hol-empty">No non-working days entered for ${escapeHtml(String(year))}.</td></tr>`;
    }
    data.forEach(h => {
      const d = parseDate(h.holiday_date);
      const tr = document.createElement('tr');
      tr.dataset.rowId = h.id;
      tr.title = 'Show this month in the calendar';
      tr.innerHTML = `
        <td>${escapeHtml(shortDate(d))}</td>
        <td class="hol-muted">${WEEKDAYS[d.getDay()]}</td>
        <td class="hol-name">${escapeHtml(h.name)}</td>
        <td>${typePill(h.holiday_type)}</td>
        <td class="hol-col-act">${canEdit ? `<button type="button" class="hol-remove" data-id="${h.id}" aria-label="Remove ${escapeHtml(h.name)}">Remove</button>` : ''}</td>`;
      // Clicking a row shows its month in the calendar.
      tr.addEventListener('click', (e) => {
        if (e.target.closest('button')) return;
        calMonth = d.getMonth();
        renderCalendar();
      });
      tbody.appendChild(tr);
    });
    tbody.querySelectorAll('[data-id]').forEach(btn => {
      btn.addEventListener('click', () => removeHoliday(btn));
    });
    table.appendChild(tbody);

    foot.hidden = !data.length;
    foot.textContent = `${plural(data.length, 'date')} in ${year}` +
      (canEdit ? ' · Removing a date changes due dates of requests sent after it.' : '');
    renderCalendar();
  }

  async function removeHoliday(btn) {
    const h = holidays.find(x => String(x.id) === btn.dataset.id);
    const name = h ? h.name : btn.closest('tr').children[2].textContent;
    if (!(await confirmDialog({
      title: `Remove “${name}”?`,
      message: 'Due dates of requests submitted after this will count it as a working day.',
      confirmLabel: 'Remove date', cancelLabel: 'Keep it', danger: true
    }))) return;
    btn.disabled = true;
    const { ok, error } = await apiFetch(`/holidays/${btn.dataset.id}`, { method: 'DELETE' });
    if (!ok) {
      pageMessage(friendlyError(error) || 'Could not remove this date.', 'error');
      btn.disabled = false;
      return;
    }
    pageMessage(`“${name}” is removed.`, 'ok');
    load();
  }

  /* ---------------- Add a date (Head Attorney) ---------------- */
  function openAddForm(iso) {
    addCard.hidden = false;
    if (iso) $('holidayDate').value = iso;
    formMessage.textContent = '';
    addCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
    $(iso ? 'holidayName' : 'holidayDate').focus({ preventScroll: true });
  }
  function closeAddForm() {
    form.reset();
    formMessage.textContent = '';
    addCard.hidden = true;
  }

  /* ---------------- Views: list / proclamation results ---------------- */
  // The browser's Back button returns from the results to the list,
  // instead of leaving the page. Nothing goes into the address.
  let historyPushed = false;
  function showView(next) {
    $('holListView').hidden = next !== 'list';
    $('holProcView').hidden = next !== 'proc';
    window.scrollTo(0, 0);
  }
  function pushStep() {
    if (historyPushed) return;
    history.pushState({ holidaysProclamation: true }, '');
    historyPushed = true;
  }
  function dropStep() {
    if (!historyPushed) return;
    historyPushed = false;
    history.back();          // its popstate is ignored (historyPushed is already false)
  }
  window.addEventListener('popstate', () => {
    if (!historyPushed) return;
    historyPushed = false;
    closeProclamation();
  });

  /* ---------------- Check proclamation (Official Gazette) ---------------- */
  // Head Attorney only (the server checks too: GET /holidays/suggestions).
  // The server reads the proclamation's scanned PDF with OCR, so a check
  // takes about 15 seconds. Nothing is saved until "Add N ticked dates",
  // which uses the same POST /holidays as the Add form, one date at a time.
  const GAZETTE_ORIGIN = 'https://www.officialgazette.gov.ph/';
  const procMessage = $('proclamationMessage');
  const procResult = $('proclamationResult');
  const procAddBtn = $('proclamationAddBtn');
  let procYear = null;
  let procSuggestions = [];
  let procRun = 0;           // a newer check (or leaving the view) makes an older reply stale
  let procAdding = false;

  function setProcMessage(text, tone) {
    procMessage.innerHTML = text ? msgHtml(text, tone) : '';
  }

  async function checkProclamation() {
    const run = ++procRun;
    procYear = Number(yearSelect.value);
    procSuggestions = [];
    $('proclamationTitle').textContent = `${procYear} holiday proclamation`;
    procResult.innerHTML = '';
    $('procActions').hidden = true;
    setProcMessage(`Reading the ${procYear} proclamation from the Official Gazette… This takes about 15 seconds.`, 'info');
    pageMessage('');
    showView('proc');
    pushStep();
    $('procBackBtn').focus({ preventScroll: true });
    // One try with a long timeout: each check downloads a PDF and runs OCR.
    const { ok, status, data, error } = await apiFetch(
      `/holidays/suggestions?year=${encodeURIComponent(procYear)}`,
      { timeoutMs: 90000, retries: 0 },
    );
    if (run !== procRun) return;           // the user left (or checked again) while this ran
    if (!ok) {
      // The server allows 5 checks an hour (a repeat check of the same year
      // within the hour reuses the last read, but still counts).
      const text = status === 429
        ? 'Too many proclamation checks in the last hour. Try again later, or enter the dates by hand.'
        : friendlyError(error);
      setProcMessage(text, 'error');
      return;
    }
    setProcMessage('');
    procSuggestions = data.suggestions;
    renderProclamation(data);
  }

  function closeProclamation() {
    if (procAdding) return;
    procRun++;
    procSuggestions = [];
    procResult.innerHTML = '';
    setProcMessage('');
    showView('list');
    $('checkProclamationBtn').focus({ preventScroll: true });
  }
  function leaveProclamation() {
    if (procAdding) return;
    dropStep();
    closeProclamation();
  }

  function renderProclamation(data) {
    // Only link to the Gazette, whatever the server sends.
    const pdfLink = data.pdf_url && data.pdf_url.startsWith(GAZETTE_ORIGIN)
      ? `<a class="btn btn-secondary btn-compact hol-pdf-link" href="${escapeHtml(data.pdf_url)}" target="_blank" rel="noopener noreferrer">Open the signed PDF <span aria-hidden="true">↗</span><span class="visually-hidden">(opens in a new tab)</span></a>`
      : '';
    let html = `<section class="rd-card hol-source" aria-label="Proclamation">
        <div class="hol-source-text">
          <p class="hol-source-title">${escapeHtml(data.title)}</p>
          <p class="hol-source-sub">Declaring the regular holidays and special (non-working) days for the year ${escapeHtml(String(data.year))} · officialgazette.gov.ph</p>
        </div>${pdfLink}
      </section>`;

    html += `<p class="page-msg info">${noticeHtml('Check each date against the signed PDF. Eid’l Fitr, Eid’l Adha, local holidays and work suspensions are declared separately: add them with “Add a date”. Special working days (offices open) are left out.', 'info')}</p>`;

    // "Check these in the PDF first": unreadable lines and dates with a note
    const checks = [];
    const lines = data.unreadable_lines || [];
    if (lines.length === 1) {
      checks.push(`<li>1 line couldn't be read: “${escapeHtml(lines[0])}”. Add it with “Add a date” if it belongs.</li>`);
    } else if (lines.length > 1) {
      checks.push(`<li>${lines.length} lines couldn't be read. Add them with “Add a date” if they belong:
        <ul>${lines.map(l => `<li>“${escapeHtml(l)}”</li>`).join('')}</ul></li>`);
    }
    const noted = data.suggestions.filter(s => s.check_note && !s.already_listed_as).length;
    if (noted) {
      checks.push(`<li>${noted === 1 ? '1 date has a note and is not ticked.' : `${noted} dates have a note and are not ticked.`}</li>`);
    }
    if (checks.length) {
      html += `<div class="page-msg warn" role="note"><span class="page-msg-icon">${NOTICE_ICONS.warn}</span>
        <div class="page-msg-body"><p class="page-msg-title">Check these in the PDF first</p><ul class="page-msg-list">${checks.join('')}</ul></div></div>`;
    }

    const rows = data.suggestions.map((s, i) => {
      const d = parseDate(s.holiday_date);
      const listed = s.already_listed_as;
      const note = listed ? `Already listed as “${escapeHtml(listed)}”` : escapeHtml(s.check_note || '');
      const cls = listed ? 'hol-listed' : (s.check_note ? 'hol-noted' : '');
      return `<tr class="${cls}">
        <td class="hol-col-tick"><input type="checkbox" class="hol-check" data-index="${i}" ${!listed && !s.check_note ? 'checked' : ''} ${listed ? 'disabled' : ''} aria-label="Add ${escapeHtml(s.name)}, ${escapeHtml(shortDate(d))}"></td>
        <td>${escapeHtml(shortDate(d))}</td>
        <td class="hol-muted">${WEEKDAYS[d.getDay()]}</td>
        <td class="hol-name">${escapeHtml(s.name)}</td>
        <td>${typePill(s.holiday_type)}</td>
        <td class="hol-note">${note}</td>
      </tr>`;
    }).join('');
    html += `<section class="rd-card hol-list hol-found" aria-labelledby="holFoundTitle">
        <div class="hol-list-head">
          <h2 class="hol-list-title" id="holFoundTitle">Dates found</h2>
          <p class="hol-found-count" id="holFoundCount" aria-live="polite"></p>
        </div>
        <div class="table-wrap"><table class="data-table hol-table hol-proc-table">
          <thead><tr><th class="hol-col-tick"><span class="visually-hidden">Add</span></th><th>Date</th><th>Day</th><th>Name</th><th>Type</th><th>Note</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="6" class="hol-empty">No dates were found in this proclamation.</td></tr>'}</tbody></table></div>
      </section>`;
    procResult.innerHTML = html;
    procResult.querySelectorAll('.hol-check').forEach(cb => cb.addEventListener('change', updateProcAddBtn));
    updateProcAddBtn();
  }

  function tickedSuggestions() {
    return [...procResult.querySelectorAll('.hol-check:checked:not(:disabled)')]
      .map(cb => procSuggestions[Number(cb.dataset.index)]);
  }

  function updateProcAddBtn() {
    const listed = procSuggestions.filter(s => s.already_listed_as).length;
    const anyNew = procSuggestions.length > listed;
    const count = tickedSuggestions().length;
    const countEl = $('holFoundCount');
    if (countEl) {
      countEl.textContent = `${procSuggestions.length} found · ${count} ticked` + (listed ? ` · ${listed} already listed` : '');
    }
    $('procActions').hidden = false;
    procAddBtn.hidden = !anyNew;
    procAddBtn.disabled = count === 0;
    procAddBtn.textContent = `Add ${plural(count, 'ticked date')}`;
    $('proclamationCloseBtn').textContent = anyNew ? 'Cancel' : 'Back to the holidays';
    if (!anyNew && procSuggestions.length) {
      setProcMessage('Every date in this proclamation is already listed.', 'ok');
    }
  }

  async function addTickedDates() {
    const picked = tickedSuggestions();
    if (!picked.length || procAdding) return;
    if (!(await confirmDialog({
      title: `Add ${plural(picked.length, 'date')} for ${procYear}?`,
      message: 'Due dates of requests submitted after this will skip them.',
      confirmLabel: `Add ${plural(picked.length, 'date')}`
    }))) return;
    procAdding = true;
    procAddBtn.disabled = true;
    $('proclamationCloseBtn').disabled = true;
    let added = 0;
    const failed = [];
    for (const s of picked) {
      const { ok, error } = await apiFetch('/holidays', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ holiday_date: s.holiday_date, name: s.name, holiday_type: s.holiday_type }),
      });
      if (ok) added++;
      else failed.push(`${s.name}: ${friendlyError(error)}`);
    }
    procAdding = false;
    $('proclamationCloseBtn').disabled = false;
    const year = procYear;
    leaveProclamation();
    pageMessages([
      added ? [`${plural(added, 'date')} added for ${year}.`, 'ok'] : null,
      failed.length ? [`Not added: ${failed.join('; ')}`, 'error'] : null,
    ]);
    calYear = year;
    ensureYearOption(calYear);
    load();
  }

  /* ---------------- Head Attorney controls ---------------- */
  function setupHeadAttorneyControls() {
    if (!isHeadAttorney()) return;
    $('holHeadActions').hidden = false;
    $('checkProclamationBtn').addEventListener('click', checkProclamation);
    $('procBackBtn').addEventListener('click', leaveProclamation);
    $('proclamationCloseBtn').addEventListener('click', leaveProclamation);
    procAddBtn.addEventListener('click', addTickedDates);

    $('holAddBtn').addEventListener('click', () => {
      if (addCard.hidden) openAddForm('');
      else closeAddForm();
    });
    $('holidayFormCancelBtn').addEventListener('click', closeAddForm);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const payload = {
        holiday_date: $('holidayDate').value,
        name: $('holidayName').value.trim(),
        holiday_type: $('holidayType').value,
      };
      if (!payload.holiday_date) {
        formMessage.textContent = 'Enter the date.';
        $('holidayDate').focus();
        return;
      }
      if (!payload.name) {
        formMessage.textContent = 'Enter the name.';
        $('holidayName').focus();
        return;
      }
      submitBtn.disabled = true;
      const { ok, error } = await apiFetch('/holidays', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      submitBtn.disabled = false;
      if (!ok) {
        formMessage.textContent = friendlyError(error);
        return;
      }
      closeAddForm();
      // Show the month of the date just added.
      const added = parseDate(payload.holiday_date);
      pageMessage(`“${payload.name}” is added for ${shortDate(added)}.`, 'ok');
      calYear = added.getFullYear();
      calMonth = added.getMonth();
      ensureYearOption(calYear);
      load();
    });
  }

  // Wait for auth-guard.js to set window.currentStaff (it redirects to the
  // login page on its own if there is no session).
  (function waitForStaff(triesLeft) {
    if (window.currentStaff || triesLeft <= 0) {
      setupHeadAttorneyControls();
      load();
      return;
    }
    setTimeout(() => waitForStaff(triesLeft - 1), 100);
  })(50);
})();
