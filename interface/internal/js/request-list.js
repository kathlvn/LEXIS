// Search, filters, and pages for the request lists (All requests, For review,
// In progress, Completed, and the others). The server does the searching
// and counting (GET /requests with X-Total-Count); this file only draws the
// controls, keeps them in the page address (so Back and a refresh keep your
// search), and asks for one page at a time.
//
// Usage on a page:
//   <div class="list-toolbar" id="listToolbar"></div>  ...list...  <div class="list-pager" id="listPager"></div>
//   initRequestList({
//     controls: ['q', 'service', 'status', 'officer', 'date', 'deadline', 'sort'],
//               (also 'client' = find one client, 'clientWaiting' = clients to verify)
//     searchPlaceholder: 'Search requests in review',   // the 'q' box's hint
//     searchInputId: 'topSearchInput',      // use an existing box instead of 'q' (All requests)
//     toolbarClass: 'list-toolbar-queue',   // extra class(es) on the toolbar (queue layout)
//     sortNote: 'Sorted by due date, soonest first',   // fixed order, said in words instead of a sort box
//     base: {status: 'Completed'},          // fixed filters for this page
//     pageSize: 25,
//     render(rows, info) { ... },           // draw the rows; info = {total, error, filtered}
//   });

const REQUEST_LIST_STATUSES = ['Submitted', 'Received', 'For Validation', 'Under Review',
  'Clarificatory Meeting', 'Processing', 'For Attorney Review', 'Approved', 'Completed',
  'Cancelled', 'Rejected'];

// Client filter: verification wording, same as the client box.
const LIST_CLIENT_STATUS = {
  unverified: {cls: 'neutral', text: 'Not yet verified'},
  pending:    {cls: 'waiting', text: 'Endorsement letter to check'},
  verified:   {cls: 'ok',      text: 'Verified'},
  expired:    {cls: 'waiting', text: 'Verification expired'},
  declined:   {cls: 'bad',     text: 'Declined'},
  revoked:    {cls: 'bad',     text: 'Verification revoked'}
};
  
let staffDirectoryPromise = null;
function loadStaffDirectory(){
  // Once per page, shared by every control that needs it.
  if(!staffDirectoryPromise){
    staffDirectoryPromise = apiFetch('/staff/directory').then(r => r.ok ? r.data : []);
  }
  return staffDirectoryPromise;
}

function initRequestList(config){
  const controls = config.controls || ['q'];
  const base = config.base || {};
  const pageSize = config.pageSize || 25;
  const toolbar = document.getElementById(config.toolbarId || 'listToolbar');
  const pager = document.getElementById(config.pagerId || 'listPager');
  const SEARCH_DELAY_MS = 350;

  // ---- state, read from the page address so links and Back keep filters
  const url = new URLSearchParams(window.location.search);
  const state = {
    q: (url.get('q') || '').slice(0, 100),
    service: url.get('service') || '',
    status: url.get('status') || '',
    officer: url.get('officer') || '',
    from: url.get('from') || '',
    to: url.get('to') || '',
    deadline: url.get('deadline') || '',
    client: (url.get('client') || '').replace(/\D/g, '').slice(0, 10),   // one client's id (digits only)
    waiting: url.get('waiting') === '1' ? '1' : '',                      // client waiting for verification
    sort: url.get('sort') || config.defaultSort || 'newest',
    page: Math.max(1, parseInt(url.get('page'), 10) || 1)
  };

  // ---- controls
  const has = name => controls.includes(name);
  let html = '';
  if(has('q')){
    const hint = escapeHtml(config.searchPlaceholder || 'Search requests');
    html += `<div class="list-search"><input type="search" id="lstQ" maxlength="100"
      placeholder="${hint}" aria-label="${hint}"></div>`;
  }
  if(has('client')){
    html += `<div class="list-client" id="lstClientWrap">
      <input type="search" id="lstClient" maxlength="100" autocomplete="off" placeholder="Find a client"
        aria-label="Find a client by name, email, or office" role="combobox" aria-expanded="false"
        aria-controls="lstClientList" aria-autocomplete="list">
      <div class="list-client-list" id="lstClientList" role="listbox" hidden></div></div>`;
  }
  if(has('clientWaiting')){
    html += `<select id="lstClientWaiting" aria-label="Client verification"><option value="">All clients</option>
      <option value="1">Client waiting for verification</option></select>`;
  }
  if(has('service')) html += `<select id="lstService" class="list-service" aria-label="Service"><option value="">All services</option></select>`;
  if(has('status')){
    html += `<select id="lstStatus" aria-label="Status"><option value="">All statuses</option>${
      REQUEST_LIST_STATUSES.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('')}</select>`;
  }
  if(has('officer')) html += `<select id="lstOfficer" aria-label="Assigned officer"><option value="">All officers</option><option value="0">Unassigned</option></select>`;
  if(has('deadline')){
    html += `<select id="lstDeadline" aria-label="Due date"><option value="">Any due date</option>
      <option value="due_soon">Overdue or due soon</option><option value="overdue">Overdue only</option></select>`;
  }
  if(has('date')){
    // The hint shows until a date is picked (a date box has no placeholder).
    ['From', 'To'].forEach(end => {
      html += `<label class="list-date-field" title="Filed ${end.toLowerCase()}"><span class="list-date-hint" aria-hidden="true">Filed ${end.toLowerCase()}</span>
        <input type="date" id="lst${end}" aria-label="Filed ${end.toLowerCase()}"></label>`;
    });
  }
  if(has('sort')){
    html += `<select id="lstSort" class="list-sort" aria-label="Sort"><option value="newest">Newest first</option>
      <option value="oldest">Oldest first</option><option value="due">Due date, soonest first</option></select>`;
  }
  if(config.sortNote) html += `<p class="list-sort-note">${escapeHtml(config.sortNote)}</p>`;
  html += `<button type="button" class="btn btn-tertiary btn-compact list-clear" id="lstClear" hidden>Clear filters</button>`;
  toolbar.innerHTML = html;
  toolbar.classList.toggle('list-toolbar-search-only', controls.length === 1 && has('q'));
  if(config.toolbarClass) toolbar.classList.add(...config.toolbarClass.split(' ').filter(Boolean));

  const $ = id => document.getElementById(id);
  // The search box: this list's own, or one the page already has (the top
  // bar's box, which is drawn again after the sign-in check, so it is looked
  // up each time instead of kept).
  const getQ = () => $('lstQ') || (config.searchInputId ? $(config.searchInputId) : null);
  const qInput = getQ();
  if(qInput && !$('lstQ')) qInput.dataset.liveList = '1';
  if(qInput) qInput.value = state.q;
  if($('lstStatus')) $('lstStatus').value = state.status;
  if($('lstDeadline')) $('lstDeadline').value = state.deadline;
  const markDate = input => input.parentElement.classList.toggle('has-value', !!input.value);
  ['lstFrom', 'lstTo'].forEach(id => {
    if(!$(id)) return;
    $(id).value = id === 'lstFrom' ? state.from : state.to;
    markDate($(id));
    $(id).addEventListener('input', () => markDate($(id)));
  });
  if($('lstSort')) $('lstSort').value = state.sort;
  if($('lstClientWaiting')) $('lstClientWaiting').value = state.waiting;

  // The chosen client, shown above the list with a way to clear it.
  const strip = document.createElement('div');
  strip.className = 'client-strip';
  strip.id = 'lstClientStrip';
  strip.hidden = true;
  toolbar.insertAdjacentElement('afterend', strip);

  if($('lstService')){
    // SERVICE_TYPES starts as the built-in list; the live list replaces it.
    const fill = () => {
      const sel = $('lstService');
      sel.length = 1;
      SERVICE_TYPES.forEach(s => sel.add(new Option(s.label, s.code)));
      sel.value = state.service;
    };
    fill();
    apiFetch('/service-types').then(r => {
      if(r.ok && Array.isArray(r.data)){
        SERVICE_TYPES.length = 0;
        r.data.forEach(s => SERVICE_TYPES.push({code: s.code, label: s.name}));
        fill();
      }
    });
  }
  if($('lstOfficer')){
    loadStaffDirectory().then(list => {
      const sel = $('lstOfficer');
      // Requests are assigned to legal assistants; former ones stay listed
      // (marked inactive) so their old requests can still be found.
      list.filter(s => s.role === 'legal_assistant')
          .forEach(s => sel.add(new Option(s.full_name + (s.is_active ? '' : ' (inactive)'), String(s.id))));
      sel.value = state.officer;
    });
  }

  function isFiltered(){
    return !!(state.q || state.service || state.status || state.officer || state.from || state.to || state.deadline
              || state.client || state.waiting);
  }

  function syncAddress(){
    const p = new URLSearchParams();
    ['q', 'service', 'status', 'officer', 'from', 'to', 'deadline', 'client', 'waiting'].forEach(k => { if(state[k]) p.set(k, state[k]); });
    if(state.sort !== (config.defaultSort || 'newest')) p.set('sort', state.sort);
    if(state.page > 1) p.set('page', String(state.page));
    const qs = p.toString();
    history.replaceState(null, '', window.location.pathname + (qs ? '?' + qs : ''));
    $('lstClear').hidden = !isFiltered();
  }

  let seq = 0;
  async function load(){
    const mySeq = ++seq;
    syncAddress();
    toolbar.classList.add('loading');
    const result = await fetchAllRequests({
      ...base,
      q: state.q,
      serviceTypeCode: state.service,
      status: base.status || state.status,
      officerId: state.officer,
      from: state.from,
      to: state.to,
      deadline: state.deadline,
      requesterId: state.client,
      clientWaiting: state.waiting === '1',
      sort: state.sort,
      limit: pageSize,
      offset: (state.page - 1) * pageSize
    });
    if(mySeq !== seq) return;          // a newer search already started
    toolbar.classList.remove('loading');
    const total = result.total ?? result.rows.length;
    // Past the last page (e.g. after a filter shrank the list): go back one.
    if(!result.error && !result.rows.length && total > 0 && state.page > 1){
      state.page = Math.max(1, Math.ceil(total / pageSize));
      return load();
    }
    config.render(result.rows, {total, error: result.error, filtered: isFiltered()});
    drawPager(total);
  }

  function drawPager(total){
    if(!pager) return;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    if(total <= pageSize){
      pager.innerHTML = total ? `<span class="list-pager-info list-pager-count">${total} request${total === 1 ? '' : 's'}</span>` : '';
      return;
    }
    const first = (state.page - 1) * pageSize + 1;
    const last = Math.min(total, state.page * pageSize);
    pager.innerHTML = `
      <span class="list-pager-info list-pager-count">${first}–${last} of ${total}</span>
      <div class="list-pager-nav">
        <button type="button" class="btn btn-secondary btn-compact" id="lstPrev" ${state.page <= 1 ? 'disabled' : ''}>${ICONS.back} Previous</button>
        <span class="list-pager-info">Page ${state.page} of ${pages}</span>
        <button type="button" class="btn btn-secondary btn-compact" id="lstNext" ${state.page >= pages ? 'disabled' : ''}>Next ${ICONS.forward}</button>
      </div>`;
    $('lstPrev').addEventListener('click', () => { state.page--; load(); window.scrollTo({top: 0}); });
    $('lstNext').addEventListener('click', () => { state.page++; load(); window.scrollTo({top: 0}); });
  }

  // ---- events: any change goes back to page 1
  let searchTimer = null;
  if(qInput){
    document.addEventListener('input', e => {
      if(e.target !== getQ()) return;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        const box = getQ();
        const v = box ? box.value.trim().slice(0, 100) : '';
        if(v === state.q) return;
        state.q = v; state.page = 1; load();
      }, SEARCH_DELAY_MS);
    });
  }
  const bind = (id, key) => {
    if(!$(id)) return;
    $(id).addEventListener('change', () => { state[key] = $(id).value; state.page = 1; load(); });
  };
  bind('lstService', 'service'); bind('lstStatus', 'status'); bind('lstOfficer', 'officer');
  bind('lstDeadline', 'deadline'); bind('lstFrom', 'from'); bind('lstTo', 'to'); bind('lstSort', 'sort');
  bind('lstClientWaiting', 'waiting');
  $('lstClear').addEventListener('click', () => {
    Object.assign(state, {q: '', service: '', status: '', officer: '', from: '', to: '', deadline: '',
                          client: '', waiting: '', page: 1});
    ['lstService', 'lstStatus', 'lstOfficer', 'lstFrom', 'lstTo', 'lstDeadline', 'lstClient', 'lstClientWaiting']
      .forEach(id => { if($(id)) $(id).value = ''; });
    if(getQ()) getQ().value = '';
    toolbar.querySelectorAll('.list-date-field').forEach(f => f.classList.remove('has-value'));
    drawClientStrip(null);
    load();
  });

  /* ---- client filter ----
     Typing 2+ letters asks the server for up to 10 matching clients, each
     with its number of requests. Picking one filters the list by that
     client's record (never by name, so two people with the same name are
     never mixed). Everything a client typed is escaped before it is shown. */
  const CLIENT_SEARCH_DELAY_MS = 300;
  let clientTimer = null, clientSeq = 0, clientMatches = [], clientActive = -1;

  function drawClientStrip(c){
    if(!c){ strip.hidden = true; strip.innerHTML = ''; return; }
    const st = LIST_CLIENT_STATUS[c.verification_status] || LIST_CLIENT_STATUS.unverified;
    strip.innerHTML = `
      <div class="client-strip-main">
        <span class="client-strip-label">Client</span>
        <b>${escapeHtml(c.formal_name)}</b>
        ${c.office_name ? `<span class="client-strip-meta">${escapeHtml(c.office_name)}</span>` : ''}
        ${c.email ? `<span class="client-strip-meta">${escapeHtml(c.email)}</span>` : ''}
        <span class="client-status ${st.cls}">${escapeHtml(st.text)}</span>
        <span class="client-strip-meta">${c.total_requests} request${c.total_requests === 1 ? '' : 's'} \u00b7 ${c.open_requests} open</span>
      </div>
      <button type="button" class="btn btn-tertiary btn-compact" id="lstClientClear">Show all clients</button>`;
    strip.hidden = false;
    $('lstClientClear').addEventListener('click', () => {
      state.client = ''; state.page = 1;
      drawClientStrip(null);
      load();
    });
  }

  function closeClientList(){
    if(!$('lstClientList')) return;
    $('lstClientList').hidden = true;
    $('lstClient').setAttribute('aria-expanded', 'false');
    clientActive = -1;
  }

  function drawClientList(){
    const list = $('lstClientList');
    if(!clientMatches.length){
      list.innerHTML = '<div class="list-client-empty">No clients match.</div>';
    }else{
      list.innerHTML = clientMatches.map((c, i) => {
        const st = LIST_CLIENT_STATUS[c.verification_status] || LIST_CLIENT_STATUS.unverified;
        return `<div class="list-client-option${i === clientActive ? ' active' : ''}" role="option" id="lstClientOpt${i}"
                     aria-selected="${i === clientActive}" data-index="${i}">
          <div><b>${escapeHtml(c.formal_name)}</b>${c.office_name ? ' \u00b7 ' + escapeHtml(c.office_name) : ''}</div>
          <div class="list-client-sub">${c.total_requests} request${c.total_requests === 1 ? '' : 's'} (${c.open_requests} open)
            \u00b7 <span class="client-status ${st.cls}">${escapeHtml(st.text)}</span></div></div>`;
      }).join('');
    }
    list.hidden = false;
    $('lstClient').setAttribute('aria-expanded', 'true');
    if(clientActive >= 0) $('lstClient').setAttribute('aria-activedescendant', 'lstClientOpt' + clientActive);
    list.querySelectorAll('.list-client-option').forEach(opt => {
      // mousedown, so it runs before the box loses focus and closes
      opt.addEventListener('mousedown', e => { e.preventDefault(); pickClient(clientMatches[Number(opt.dataset.index)]); });
    });
  }

  function pickClient(c){
    if(!c) return;
    state.client = String(c.id); state.page = 1;
    $('lstClient').value = '';
    closeClientList();
    drawClientStrip(c);
    load();
  }

  async function searchClients(term){
    const mySeq = ++clientSeq;
    const r = await apiFetch('/clients/search?q=' + encodeURIComponent(term));
    if(mySeq !== clientSeq) return;          // a newer search already started
    clientMatches = r.ok && Array.isArray(r.data) ? r.data : [];
    clientActive = -1;
    drawClientList();
  }

  if($('lstClient')){
    $('lstClient').addEventListener('input', () => {
      clearTimeout(clientTimer);
      const term = $('lstClient').value.trim().slice(0, 100);
      if(term.length < 2){ clientSeq++; closeClientList(); return; }
      clientTimer = setTimeout(() => searchClients(term), CLIENT_SEARCH_DELAY_MS);
    });
    $('lstClient').addEventListener('keydown', e => {
      const open = !$('lstClientList').hidden && clientMatches.length;
      if(e.key === 'ArrowDown' && open){ e.preventDefault(); clientActive = Math.min(clientActive + 1, clientMatches.length - 1); drawClientList(); }
      else if(e.key === 'ArrowUp' && open){ e.preventDefault(); clientActive = Math.max(clientActive - 1, 0); drawClientList(); }
      else if(e.key === 'Enter' && open){ e.preventDefault(); pickClient(clientMatches[Math.max(clientActive, 0)]); }
      else if(e.key === 'Escape'){ closeClientList(); }
    });
    $('lstClient').addEventListener('blur', () => setTimeout(closeClientList, 150));
  }

  // Opened with ?client=ID (e.g. the request count on Request Detail).
  if(state.client){
    apiFetch('/clients/' + encodeURIComponent(state.client)).then(r => {
      if(r.ok) drawClientStrip(r.data);
      else{ state.client = ''; drawClientStrip(null); load(); }   // unknown id: show everyone
    });
  }

  load();
  return {reload: load};
}

/** Table pages: replace the table body with this page's rows. */
function renderListTable(table, rows, columns, info, emptyText){
  table.querySelectorAll('tbody').forEach(tb => tb.remove());
  table.appendChild(buildTableBody(rows, columns));
  showTableState(table, rows, info.error, info.filtered ? 'No requests match your search or filters.' : emptyText);
}
