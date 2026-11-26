// Reports page. Read-only;
// every staff member can view it. All figures come from GET /reports — this
// file only draws them. One request per period change; nothing polls.
// Layout: a card per section,
// "On this page" on the right.
(function () {
  const MONTHS = ['January','February','March','April','May','June','July',
                  'August','September','October','November','December'];
  const BAR_MAX_PX = 150;   // the tallest month's bar; the chart is 190px with its numbers

  renderTopbar();
  renderSidebar('reports');

  const $ = id => document.getElementById(id);
  $('rptPrintBtn').innerHTML = `${ICONS.documents} Print report`;
  $('rptPrintListBtn').innerHTML = `${ICONS.documents} Print this list`;
  $('rptHowtoIcon').innerHTML = ICONS.noticeInfo;
  $('rptHowtoChev').innerHTML = ICONS.chevron;
  $('rptTocHowto').insertAdjacentHTML('afterbegin', ICONS.noticeInfo);
  document.querySelector('#rpt-month .rpt-fold summary').innerHTML = `${ICONS.chevron} Show as a table`;

  /* ---------------- Period controls ---------------- */
  const monthSel = $('rptMonth'), quarterSel = $('rptQuarter'), yearSel = $('rptYear');
  const periodRadios = [...document.querySelectorAll('input[name="rptPeriod"]')];
  const period = () => periodRadios.find(r => r.checked).value;

  const now = new Date();
  MONTHS.forEach((m, i) => monthSel.add(new Option(m, i + 1)));
  for (let y = now.getFullYear(); y >= 2025; y--) yearSel.add(new Option(y, y));
  monthSel.value = String(now.getMonth() + 1);
  quarterSel.value = String(Math.floor(now.getMonth() / 3) + 1);
  yearSel.value = String(now.getFullYear());

  function syncControls() {
    monthSel.hidden = period() !== 'month';
    quarterSel.hidden = period() !== 'quarter';
  }
  [...periodRadios, monthSel, quarterSel, yearSel].forEach(el =>
    el.addEventListener('change', () => { syncControls(); load(); }));
  syncControls();

  /* ---------------- Small helpers ---------------- */
  function fmt(n) { return Number(n || 0).toLocaleString('en-US'); }
  function plural(n, word) { return `${fmt(n)} ${word}${n === 1 ? '' : 's'}`; }
  // "this month" / "this quarter" / "this year", for the notes.
  function thisPeriod(data) { return `this ${data.period.type}`; }

  // A plain calendar date from the server (YYYY-MM-DD), read as that day.
  // The year is left out when it is the report's year ("Nov 4").
  let reportYear = now.getFullYear();
  function shortDate(iso) {
    if (!iso) return '—';
    const [y, m, d] = iso.split('-').map(Number);
    const opts = { month: 'short', day: 'numeric' };
    if (y !== reportYear) opts.year = 'numeric';
    return new Date(y, m - 1, d).toLocaleDateString('en-US', opts);
  }

  // A table: head = [{label, num, cls}], rows = <tr> strings, foot = optional <tr>.
  function table(id, head, rows, emptyText, foot) {
    const th = head.map(h => `<th${h.num || h.cls ? ` class="${[h.num ? 'num' : '', h.cls || ''].join(' ').trim()}"` : ''}>${h.label}</th>`).join('');
    const body = rows.length ? rows.join('') : `<tr><td colspan="${head.length}" class="rpt-empty">${emptyText}</td></tr>`;
    $(id).innerHTML = `<thead><tr>${th}</tr></thead><tbody>${body}</tbody>${rows.length && foot ? `<tfoot>${foot}</tfoot>` : ''}`;
  }

  // A share bar with its percentage (requesting party, CSM).
  function shareCell(pctValue) {
    if (pctValue === null || pctValue === undefined) return '—';
    const w = Math.max(0, Math.min(100, Number(pctValue)));
    return `<span class="rpt-share"><span class="rpt-track" aria-hidden="true"><span class="rpt-fill" style="width:${w}%"></span></span><span class="rpt-share-num">${pctValue}%</span></span>`;
  }

  function requestLink(trackingId) {
    return `<a class="rpt-id" href="request-detail.html?id=${encodeURIComponent(trackingId)}">${escapeHtml(trackingId)}</a>`;
  }

  /* ---------------- Summary ---------------- */
  function renderSummary(data) {
    const s = data.summary, label = escapeHtml(data.period.label);
    $('rptSummaryNote').innerHTML = `Requests submitted in ${label}. Completed, pending, overdue, and cancelled or rejected show where those requests stand today.`;
    const share = s.received ? Math.round((s.completed / s.received) * 100) : null;
    const tiles = [
      { label: 'Received', value: s.received, sub: `in ${label}` },
      { label: 'Completed', value: s.completed, sub: share === null ? 'none received' : `${share}% of received` },
      { label: 'Pending', value: s.pending, sub: 'not yet completed' },
      { label: 'Overdue', value: s.overdue, sub: 'past their due date', warn: s.overdue > 0 },
      { label: 'Cancelled or rejected', value: s.cancelled_or_rejected, sub: 'not counted as pending' },
    ];
    $('rptTiles').innerHTML = tiles.map(t => `
      <div class="rpt-stat${t.warn ? ' warn' : ''}">
        <span class="rpt-stat-label">${t.warn ? ICONS.noticeWarning : ''}${t.label}</span>
        <span class="rpt-stat-value">${fmt(t.value)}</span>
        <span class="rpt-stat-sub">${t.sub}</span>
      </div>`).join('');
  }

  /* ---------------- Received per month (bars; the period's months dark) ---------------- */
  function renderChart(data) {
    const year = Number(data.period.first_day.slice(0, 4));
    const firstM = Number(data.period.first_day.slice(5, 7));
    const lastM = Number(data.period.last_day.slice(5, 7));
    const inPeriod = m => m >= firstM && m <= lastM;
    // Months that haven't started yet show "—", not 0.
    const future = m => year > now.getFullYear() || (year === now.getFullYear() && m > now.getMonth() + 1);
    $('rptChartTitle').textContent = `Requests received per month, ${year}`;
    const which = { month: ' The selected month is dark green.', quarter: ' The selected quarter is dark green.', year: '' };
    $('rptChartNote').textContent = `Requests submitted in each month of the year.${which[data.period.type] || ''}`;

    const max = Math.max(1, ...data.monthly.map(d => d.received));
    const said = data.monthly.filter(d => !future(d.month)).map(d => `${MONTHS[d.month - 1]} ${d.received}`).join(', ');
    const cols = data.monthly.map(d => {
      const h = future(d.month) || !d.received ? 2 : Math.max(2, Math.round((d.received / max) * BAR_MAX_PX));
      return `<div class="rpt-bar-col${inPeriod(d.month) ? ' on' : ''}">
          <span class="rpt-bar-val">${future(d.month) ? '—' : fmt(d.received)}</span>
          <span class="rpt-bar" style="height:${h}px"></span>
        </div>`;
    }).join('');
    const labels = data.monthly.map(d => `<span class="${inPeriod(d.month) ? 'on' : ''}">${escapeHtml(d.label)}</span>`).join('');
    $('rptChart').innerHTML = `
      <div class="rpt-bars" role="img" aria-label="Requests received per month in ${year}: ${escapeHtml(said)}">${cols}</div>
      <div class="rpt-bar-labels" aria-hidden="true">${labels}</div>`;

    table('rptMonthTable', [{ label: 'Month' }, { label: 'Received', num: 1 }],
      data.monthly.map(d => `<tr><td>${MONTHS[d.month - 1]}</td><td class="num">${future(d.month) ? '—' : fmt(d.received)}</td></tr>`), '');
  }

  /* ---------------- Tables ---------------- */
  function overdueCell(n, applies) {
    if (!applies) return '—';
    return n ? `<span class="rpt-bad">${fmt(n)}</span>` : '0';
  }

  function renderTables(data) {
    const t = { received: 0, completed: 0, pending: 0, overdue: 0 };
    table('rptServiceTable',
      [{ label: 'Service' }, { label: 'Set period' }, { label: 'Received', num: 1 }, { label: 'Completed', num: 1 },
       { label: 'Pending', num: 1 }, { label: 'Overdue', num: 1 }, { label: 'Avg. days', num: 1 }],
      data.by_service.map(s => {
        ['received', 'completed', 'pending', 'overdue'].forEach(k => { t[k] += s[k] || 0; });
        const setPeriod = s.processing_days ? plural(s.processing_days, 'working day') : 'No set period';
        let avg = '—';
        if (s.avg_working_days !== null && s.avg_working_days !== undefined) {
          const over = s.processing_days && s.avg_working_days > s.processing_days;
          const days = Number(s.avg_working_days).toFixed(1);   // 4.0, not 4
          avg = over ? `<span class="rpt-bad" title="Over the set period">${days}</span>` : days;
        }
        return `<tr><td class="wrap">${escapeHtml(s.service)}</td><td class="nowrap">${setPeriod}</td><td class="num">${fmt(s.received)}</td>
          <td class="num">${fmt(s.completed)}</td><td class="num">${fmt(s.pending)}</td>
          <td class="num">${overdueCell(s.overdue, !!s.processing_days)}</td><td class="num">${avg}</td></tr>`;
      }), 'No services.',
      `<tr><th scope="row">Total</th><td></td><td class="num">${fmt(t.received)}</td><td class="num">${fmt(t.completed)}</td>
        <td class="num">${fmt(t.pending)}</td><td class="num">${fmt(t.overdue)}</td><td></td></tr>`);

    $('rptStatusNote').textContent = `Where ${thisPeriod(data)}’s requests stand today.`;
    table('rptStatusTable', [{ label: 'Status' }, { label: 'Requests', num: 1 }],
      data.by_status.map(s => `<tr><td>${escapeHtml(s.status)}</td><td class="num">${fmt(s.count)}</td></tr>`),
      'No requests in this period.');

    table('rptOfficeTable', [{ label: 'Office or agency' }, { label: 'Requests', num: 1 }],
      data.by_office.map(o => `<tr><td class="wrap">${escapeHtml(o.office)}</td><td class="num">${fmt(o.count)}</td></tr>`),
      'No requests in this period.');

    // Requesting party = client category as filed.
    $('rptPartyNote').textContent = `The client’s category as filed with each request. Share is out of the ${fmt(data.summary.received)} received.`;
    table('rptPartyTable', [{ label: 'Requesting party' }, { label: 'Requests', num: 1 }, { label: 'Share', cls: 'rpt-share-col' }],
      (data.by_category || []).map(p => `<tr><td class="wrap">${escapeHtml(p.party)}</td><td class="num">${fmt(p.count)}</td>
        <td>${shareCell(p.share)}</td></tr>`),
      'No requests in this period.');

    table('rptStaffTable',
      [{ label: 'Staff member' }, { label: 'Role' }, { label: 'Handled', num: 1 }, { label: 'Completed', num: 1 },
       { label: 'Pending', num: 1 }, { label: 'Overdue', num: 1 }],
      data.by_staff.map(s => `<tr><td class="wrap">${escapeHtml(s.staff)}</td><td>${escapeHtml(s.role)}</td><td class="num">${fmt(s.handled)}</td>
        <td class="num">${fmt(s.completed)}</td><td class="num">${fmt(s.pending)}</td>
        <td class="num">${overdueCell(s.overdue, true)}</td></tr>`),
      'No staff to show.');
  }

  /* ---------------- Client Satisfaction Measurement ----------------
     Totals only. The server sends no breakdown below its minimum number of
     responses, so nothing here can show one client's answers. Question
     wording and suggestions come from the survey file and clients: escaped. */
  function renderCsm(data) {
    const c = data.csm, box = $('rptCsm');
    if (!c) { box.innerHTML = ''; $('rptCsmNote').textContent = ''; return; }
    $('rptCsmNote').textContent = `Surveys clients answered in ${data.period.label}. Satisfied means Agree or Strongly agree, out of answers that are not “Not applicable”. Totals only, shown from ${c.min_responses || 3} responses.`;
    if (!c.available) {
      box.innerHTML = `<div class="rpt-card-body"><p class="rpt-card-note">The survey file (backend/app/csm_form.json) has a problem, so results can't be shown: ${escapeHtml(c.problem || '')}</p></div>`;
      return;
    }
    const notes = [];
    if (c.placeholder) notes.push('Draft survey: the questions are a placeholder based on the ARTA harmonized CSM until the office confirms its own form.');
    if (c.other_version_responses) notes.push(`${fmt(c.other_version_responses)} response(s) in this period used an earlier version of the survey and are not counted here.`);
    if (!c.shown) {
      notes.push(c.responses
        ? `Too few responses to show without revealing individual answers (results appear from ${c.min_responses}).`
        : 'No surveys were answered in this period.');
    }
    let html = `<div class="rpt-card-body">
      <div class="rpt-stats rpt-stats-2">
        <div class="rpt-stat"><span class="rpt-stat-label">Responses</span><span class="rpt-stat-value">${fmt(c.responses)}</span>
          <span class="rpt-stat-sub">answered in ${escapeHtml(data.period.label)}</span></div>
        <div class="rpt-stat"><span class="rpt-stat-label">Overall satisfaction</span><span class="rpt-stat-value">${c.shown && c.overall_rate !== null ? `${c.overall_rate}%` : '—'}</span>
          <span class="rpt-stat-sub">all rated statements together</span></div>
      </div>
      ${notes.map(n => `<p class="rpt-card-note">${escapeHtml(n)}</p>`).join('')}
    </div>`;

    if (c.shown) {
      const scored = c.questions.filter(q => q.scored);
      const others = c.questions.filter(q => !q.scored);
      html += `<div class="table-wrap"><table class="data-table rpt-table">
        <thead><tr><th>Statement</th><th class="num">Rated</th><th class="rpt-share-col">Satisfied</th></tr></thead>
        <tbody>${scored.map(q => `<tr>
          <td class="wrap"><span class="rpt-qid">${escapeHtml(q.id)}</span> ${escapeHtml(q.text)}</td>
          <td class="num">${fmt(q.rated)}</td>
          <td>${q.rated ? shareCell(q.rate) : '<span class="rpt-muted">Not applicable to all</span>'}</td>
        </tr>`).join('')}</tbody></table></div>`;

      // Questions without a satisfaction score (the Citizen's Charter ones): answer counts.
      const otherIds = others.map(q => q.id);
      const otherTitle = otherIds.length && otherIds.every(id => /^CC/i.test(id))
        ? `Citizen’s Charter answers (${otherIds.length > 1 ? `${otherIds[0]}–${otherIds[otherIds.length - 1]}` : otherIds[0]})`
        : 'Other answers';
      html += `<div class="rpt-card-folds">`;
      if (others.length) {
        html += `<details class="rpt-fold rpt-fold-print"><summary>${ICONS.chevron} ${escapeHtml(otherTitle)}</summary>
          <dl class="rpt-answers">${others.map(q => `
            <dt><span class="rpt-qid">${escapeHtml(q.id)}</span> ${escapeHtml(q.text)}</dt>
            <dd>${q.options.map(o => `${escapeHtml(o.label)} <b>${fmt(o.count)}</b>`).join('<span class="rpt-sep"> · </span>')}</dd>`).join('')}
          </dl></details>`;
      }
      html += `<details class="rpt-fold rpt-fold-print"><summary>${ICONS.chevron} Suggestions from clients (${fmt(c.suggestions.length)})</summary>
        ${c.suggestions.length
          ? `<ul class="rpt-suggestions">${c.suggestions.map(s => `<li>${escapeHtml(s)}</li>`).join('')}</ul>`
          : '<p class="rpt-card-note">No suggestions in this period.</p>'}</details></div>`;
    }
    box.innerHTML = html;
  }

  /* ---------------- Contract reviews ---------------- */
  function renderContracts(data) {
    const rows = data.contracts || [];
    $('rptConNote').textContent = `“Review of MOA/MOU, Contracts” requests filed in ${data.period.label}, plus earlier ones still open.`;
    table('rptConTable',
      [{ label: 'Request ID' }, { label: 'Office or agency' }, { label: 'Filed' }, { label: 'Due' }, { label: 'Completed' }, { label: 'Status' }],
      rows.map(r => `<tr><td class="nowrap">${requestLink(r.tracking_id)}</td><td class="wrap">${escapeHtml(r.office)}</td>
        <td class="nowrap">${shortDate(r.submitted_on)}</td><td class="nowrap">${shortDate(r.due_on)}</td><td class="nowrap">${shortDate(r.completed_on)}</td>
        <td><span class="rpt-status">${statusLabel(r.status)}${r.overdue ? '<span class="due-flag overdue">Overdue</span>' : ''}</span></td></tr>`),
      'No contract reviews in this period.');
  }

  /* ---------------- Accomplished requests ---------------- */
  function renderAccomplished(data) {
    const rows = data.accomplished || [];
    $('rptAccNote').textContent = `Requests completed in ${data.period.label}, whenever they were filed. Working days are counted like due dates.`;
    table('rptAccTable',
      [{ label: 'Request ID' }, { label: 'Service' }, { label: 'Office or agency' }, { label: 'Filed' }, { label: 'Completed' },
       { label: 'Working days' }],
      rows.map(r => {
        let days;
        if (r.within_period === false) {
          days = `<span class="rpt-bad">${fmt(r.working_days)}</span><span class="rpt-days-note rpt-bad-note">over by ${fmt(r.working_days - r.processing_days)}</span>`;
        } else {
          days = `${fmt(r.working_days)}<span class="rpt-days-note">${r.within_period ? `within ${fmt(r.processing_days)}` : 'no set period'}</span>`;
        }
        return `<tr><td class="nowrap">${requestLink(r.tracking_id)}</td><td class="wrap">${escapeHtml(r.service)}</td>
          <td class="wrap">${escapeHtml(r.office)}</td><td class="nowrap">${shortDate(r.submitted_on)}</td><td class="nowrap">${shortDate(r.completed_on)}</td>
          <td class="nowrap">${days}</td></tr>`;
      }), 'No requests were completed in this period.');

    const foot = $('rptAccFoot');
    foot.hidden = !rows.length;
    if (rows.length) {
      const within = rows.filter(r => r.within_period === true).length;
      const over = rows.filter(r => r.within_period === false).length;
      const none = rows.length - within - over;
      const parts = [];
      if (within) parts.push(`${fmt(within)} within the set period`);
      if (over) parts.push(`${fmt(over)} over`);
      if (none) parts.push(`${fmt(none)} with no set period`);
      foot.textContent = `${plural(rows.length, 'accomplished request')} · ${parts.join(', ')}`;
    }
  }

  /* ---------------- Printing ---------------- */
  // The fold-outs that belong in a printed report open for printing and close after.
  let openedForPrint = [];
  window.addEventListener('beforeprint', () => {
    openedForPrint = [...document.querySelectorAll('.rpt-fold-print:not([open]), #rpt-howto:not([open])')];
    openedForPrint.forEach(d => { d.open = true; });
  });
  window.addEventListener('afterprint', () => {
    openedForPrint.forEach(d => { d.open = false; });
    openedForPrint = [];
    document.body.classList.remove('print-acc-list');
  });
  $('rptPrintBtn').addEventListener('click', () => window.print());
  // Print only the accomplished list (the walk-in slip uses the same idea).
  $('rptPrintListBtn').addEventListener('click', () => {
    document.body.classList.add('print-acc-list');
    window.print();
  });

  /* ---------------- On this page ---------------- */
  // The link of the section at the top of the window is marked. A clicked
  // link stays marked until the person scrolls themselves (near the bottom
  // of the page a short section can't reach the top). "How the figures are
  // counted" also opens its fold-out.
  const tocLinks = [...document.querySelectorAll('.rpt-toc-link')];
  const sections = tocLinks.map(a => document.querySelector(a.getAttribute('href')));
  let pinned = -1;
  function markCurrent() {
    const line = 120;   // just under the 68px top bar
    let current = 0;
    sections.forEach((s, i) => { if (s && s.getBoundingClientRect().top <= line) current = i; });
    if (pinned >= 0) current = pinned;
    tocLinks.forEach((a, i) => {
      a.classList.toggle('current', i === current);
      if (i === current) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
    });
  }
  let tocFrame = 0;
  window.addEventListener('scroll', () => {
    if (tocFrame) return;
    tocFrame = requestAnimationFrame(() => { tocFrame = 0; markCurrent(); });
  }, { passive: true });
  tocLinks.forEach((a, i) => a.addEventListener('click', () => {
    if (a === $('rptTocHowto')) $('rpt-howto').open = true;
    pinned = i;
    requestAnimationFrame(markCurrent);
  }));
  ['wheel', 'touchstart', 'keydown'].forEach(type =>
    window.addEventListener(type, () => { pinned = -1; }, { passive: true }));
  markCurrent();

  /* ---------------- Loading ---------------- */
  const made = $('rptMade');
  let loadSeq = 0;
  async function load() {
    const seq = ++loadSeq;                  // ignore answers to older selections
    const params = new URLSearchParams({ period: period(), year: yearSel.value });
    if (period() === 'month') params.set('month', monthSel.value);
    if (period() === 'quarter') params.set('quarter', quarterSel.value);
    made.classList.remove('error');
    made.textContent = 'Loading…';
    const { ok, status, data, error } = await apiFetch(`/reports?${params}`);
    if (seq !== loadSeq) return;
    if (!ok) {
      made.classList.add('error');
      made.textContent = status === 429
        ? 'The report was loaded too many times in the last minute. Wait a moment, then try again.'
        : (typeof error === 'string' ? error : 'Could not load the report.');
      return;
    }
    reportYear = Number(data.period.first_day.slice(0, 4));
    const at = new Date(data.generated_at);
    made.textContent = `${data.period.label} · made ${at.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}, ${at.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}`;
    renderSummary(data);
    renderChart(data);
    renderTables(data);
    renderCsm(data);
    renderContracts(data);
    renderAccomplished(data);
    markCurrent();
  }

  load();
})();
