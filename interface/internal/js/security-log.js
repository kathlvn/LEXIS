// Security Log — Head Attorney only; the server enforces the same
// rule (routers/security_log.py). Read-only.
(function () {
  (function guardHeadAttorneyOnly(triesLeft) {
    if (!window.currentStaff) {
      if (triesLeft > 0) setTimeout(() => guardHeadAttorneyOnly(triesLeft - 1), 100);
      return;
    }
    if (window.currentStaff.role !== 'head_attorney') window.location.replace('requests-all.html');
  })(50);

  renderTopbar();
  renderSidebar('security-log');
  renderBreadcrumb({title: 'Security log'});

  const PAGE_SIZE = 50;
  const $ = id => document.getElementById(id);
  let page = 1;
  let seq = 0;

  function when(iso){
    const d = parseServerTime(iso);
    return d.toLocaleString('en-PH', {timeZone: 'Asia/Manila', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit'});
  }
  function windowText(minutes){
    if(!minutes) return '';
    if(minutes >= 1440) return 'per day';
    return minutes === 60 ? 'per hour' : `per ${minutes} min`;
  }

  async function loadSummary(){
    const r = await apiFetch('/security-events/summary');
    const tbody = $('secSummary').querySelector('tbody');
    if(!r.ok){ tbody.innerHTML = `<tr><td colspan="5" class="table-state error">${escapeHtml(r.error || 'Could not load.')}</td></tr>`; return; }
    $('secGenerated').textContent = 'As of ' + when(r.data.generated_at);
    const typeSel = $('secType');
    const keep = typeSel.value;
    typeSel.length = 1;
    r.data.types.forEach(t => typeSel.add(new Option(t.label, t.event_type)));
    typeSel.value = keep;
    tbody.innerHTML = r.data.types.map(t => `
      <tr class="${t.unusual ? 'sec-unusual' : ''}">
        <td>${escapeHtml(t.label)}${t.meaning ? `<div class="sec-meaning">${escapeHtml(t.meaning)}</div>` : ''}</td>
        <td>${t.last_24h}</td>
        <td>${t.last_7d}</td>
        <td>${t.threshold ? `${t.threshold} ${escapeHtml(windowText(t.window_minutes))}` : '—'}</td>
        <td>${t.unusual ? '<span class="deadline-tag overdue">Unusual</span>' : (t.last_7d ? 'Normal' : '—')}</td>
      </tr>`).join('');
  }

  async function loadEvents(){
    const mySeq = ++seq;
    const params = new URLSearchParams({days: $('secDays').value, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE});
    if($('secType').value) params.set('event_type', $('secType').value);
    $('secMsg').textContent = 'Loading…';
    const r = await apiFetch('/security-events?' + params.toString());
    if(mySeq !== seq) return;
    const tbody = $('secEvents').querySelector('tbody');
    if(!r.ok){ $('secMsg').textContent = ''; tbody.innerHTML = `<tr><td colspan="4" class="table-state error">${escapeHtml(r.error || 'Could not load.')}</td></tr>`; return; }
    const total = r.total ?? r.data.length;
    $('secMsg').textContent = `${total} event${total === 1 ? '' : 's'}`;
    tbody.innerHTML = r.data.length ? r.data.map(e => `
      <tr>
        <td class="sec-when">${escapeHtml(when(e.created_at))}</td>
        <td>${escapeHtml(e.label)}</td>
        <td class="sec-detail">${escapeHtml(e.detail)}</td>
        <td>${escapeHtml(e.source_ip || '—')}</td>
      </tr>`).join('') : '<tr><td colspan="4" class="table-state empty">Nothing recorded in this period.</td></tr>';
    const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    $('secPager').innerHTML = total > PAGE_SIZE ? `
      <button type="button" class="btn btn-secondary btn-compact" id="secPrev" ${page <= 1 ? 'disabled' : ''}>&lsaquo; Previous</button>
      <span class="list-pager-info">Page ${page} of ${pages}</span>
      <button type="button" class="btn btn-secondary btn-compact" id="secNext" ${page >= pages ? 'disabled' : ''}>Next &rsaquo;</button>` : '';
    if($('secPrev')){
      $('secPrev').addEventListener('click', () => { page--; loadEvents(); });
      $('secNext').addEventListener('click', () => { page++; loadEvents(); });
    }
  }

  $('secType').addEventListener('change', () => { page = 1; loadEvents(); });
  $('secDays').addEventListener('change', () => { page = 1; loadEvents(); });
  loadSummary().then(loadEvents);
})();
