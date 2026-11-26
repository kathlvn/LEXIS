// Dashboard: counts that link into the existing queues and the
// recent requests (further down).
// All counts come from one call, GET /requests/summary. The
// server counts each tile with the same filters as the queue its link opens
// (routers/requests.py dashboard_summary; tests/test_dashboard_summary.py
// compares them), decides which tiles the role gets, and splits each count
// by deadline (overdue, due soon, on track, no set period, closed) for the
// tile's bar.
//
// Legal assistants see their own work (plus the office's
// New requests); the Head Attorney sees the whole office. The reserved
// "attorney" role gets the Head Attorney's tiles except Reassignment
// requests (Head Attorney only). Login still opens All requests.
(function () {
  renderTopbar();
  renderSidebar('dashboard');
  renderBreadcrumb({title: 'Dashboard'});

  const tilesEl = document.getElementById('dashTiles');
  const msgEl = document.getElementById('dashMsg');

  // key: the tile's name in GET /requests/summary; href: the queue with the
  // same filters the server counts with; icon and tone: the chip.
  // "Overdue or due soon" comes first: it is the one that needs action.
  function tilesFor(staff){
    const me = encodeURIComponent(staff.id);
    const newTile = {key: 'new', label: 'New requests', note: 'Submitted, not yet received',
      icon: 'requests', tone: 'new', href: 'requests-all.html?status=Submitted'};
    const dueNote = 'Overdue, today, or next working day';
    if(staff.role === 'legal_assistant'){
      return [
        {key: 'my_due_soon', label: 'My overdue or due soon', note: dueNote, icon: 'noticeWarning', tone: 'danger',
          warn: true, href: `requests-all.html?officer=${me}&deadline=due_soon`},
        newTile,
        {key: 'assigned_to_me', label: 'Assigned to me', note: 'Your open requests', icon: 'users', tone: 'progress',
          href: 'requests-assigned.html'},
        {key: 'my_awaiting_client', label: 'My requests awaiting the client', note: 'The deadline keeps running',
          icon: 'clock', tone: 'waiting', href: `requests-awaiting-client.html?officer=${me}`}
      ];
    }
    const tiles = [
      {key: 'due_soon', label: 'Overdue or due soon', note: dueNote, icon: 'noticeWarning', tone: 'danger',
        warn: true, href: 'requests-all.html?deadline=due_soon'},
      newTile,
      {key: 'for_approval', label: 'For approval', note: 'For Attorney Review', icon: 'check', tone: 'attorney',
        href: 'requests-approval.html'},
      {key: 'awaiting_client', label: 'Awaiting the client', note: 'The deadline keeps running', icon: 'clock',
        tone: 'waiting', href: 'requests-awaiting-client.html'}
    ];
    if(staff.role === 'head_attorney'){
      tiles.push({key: 'reassignment', label: 'Reassignment requests', note: 'Waiting for your decision',
        icon: 'swap', tone: 'brand', href: 'requests-reassignment.html'});
    }
    tiles.push({key: 'client_waiting', label: 'Client waiting for verification', note: 'Requests from unverified clients',
      icon: 'users', tone: 'progress', href: 'requests-all.html?waiting=1'});
    return tiles;
  }

  // The bar's parts, in order. The server's parts always add up to the count.
  const PARTS = [
    {key: 'overdue', label: 'overdue'},
    {key: 'due_soon', label: 'due soon'},
    {key: 'on_track', label: 'on track'},
    {key: 'no_period', label: 'no set period'},
    {key: 'closed', label: 'closed'}
  ];

  function drawTiles(tiles){
    tilesEl.classList.toggle('dash-tiles-2', tiles.length === 4);
    tilesEl.innerHTML = tiles.map((t, i) => `
      <a class="dash-tile" href="${escapeHtml(t.href)}" id="dashTile${i}">
        <span class="dash-tile-head">
          <span class="dash-chip tone-${t.tone}" aria-hidden="true">${ICONS[t.icon]}</span>
          <span class="dash-tile-text">
            <span class="dash-tile-label">${escapeHtml(t.label)}</span>
            <span class="dash-tile-note">${escapeHtml(t.note)}</span>
          </span>
          <span class="dash-tile-arrow" aria-hidden="true">${ICONS.chevRight}</span>
        </span>
        <span class="dash-tile-body">
          <span class="dash-count" aria-live="polite">&hellip;</span>
          <span class="dash-split">
            <span class="dash-bar" aria-hidden="true"></span>
            <span class="dash-key">Loading</span>
          </span>
        </span>
      </a>`).join('');
  }

  function fillTile(tile, t, counts){
    const countEl = tile.querySelector('.dash-count');
    const bar = tile.querySelector('.dash-bar');
    const key = tile.querySelector('.dash-key');
    if(!counts || typeof counts.count !== 'number'){
      countEl.textContent = '—';
      key.textContent = 'Could not load';
      return false;
    }
    countEl.textContent = counts.count;
    tile.classList.toggle('danger', !!t.warn && counts.count > 0);
    const parts = PARTS.filter(p => counts[p.key] > 0);
    if(!parts.length){
      key.textContent = 'None right now';
      return true;
    }
    bar.innerHTML = parts.map(p =>
      `<span class="dash-seg part-${p.key}" style="flex-grow:${Number(counts[p.key])}"></span>`).join('');
    key.innerHTML = parts.map(p =>
      `<span class="dash-key-item"><span class="dash-dot part-${p.key}"></span>${Number(counts[p.key])} ${p.label}</span>`).join('');
    return true;
  }

  async function loadCounts(tiles){
    const {ok, data} = await apiFetch('/requests/summary');
    const all = ok && data ? data : {};
    let failed = 0;
    tiles.forEach((t, i) => {
      if(!fillTile(document.getElementById('dashTile' + i), t, all[t.key])) failed++;
    });
    if(failed){
      msgEl.textContent = failed === tiles.length
        ? 'The counts could not be loaded right now. Please refresh the page in a moment.'
        : 'Some counts could not be loaded (shown as —). Please refresh the page in a moment.';
      msgEl.hidden = false;
    }
  }

  // ---- Recent requests: the 10 newest. Legal assistants see their
  // own (open and closed); the Head Attorney (and attorney) sees the office's,
  // with an officer and a service filter. "See all" opens All requests with
  // the same filters.
  const RECENT_LIMIT = 10;
  const recentTable = document.getElementById('dashRecentTable');
  const filtersEl = document.getElementById('dashFilters');
  const seeAll = document.getElementById('dashSeeAll');
  const recent = {officer: '', service: ''};
  let recentSeq = 0;

  function startRecent(staff){
    const own = staff.role === 'legal_assistant';
    if(own) recent.officer = String(staff.id);
    document.getElementById('dashRecentTitle').textContent = own ? 'My recent requests' : 'Recent requests';

    const columns = [
      {key: 'id', label: 'Request ID', render: r => `<span class="tracking-id">${escapeHtml(r.id)}</span>`},
      {key: 'service', label: 'Service'},
      {key: 'client', label: 'Client', render: clientCell},
      {key: 'submitted', label: 'Filed'},
      ...(own ? [] : [{key: 'officer', label: 'Officer'}]),   // always the assistant themself
      {key: 'status', label: 'Status', render: statusCell},
      {key: 'due', label: 'Due', render: dueCell}
    ];
    recentTable.querySelector('thead').innerHTML =
      `<tr>${columns.map(c => `<th>${escapeHtml(c.label)}</th>`).join('')}<th><span class="visually-hidden">Open</span></th></tr>`;

    if(!own){
      filtersEl.innerHTML = `
        <select id="dashOfficer" aria-label="Assigned officer"><option value="">All officers</option><option value="0">Unassigned</option></select>
        <select id="dashService" aria-label="Service"><option value="">All services</option></select>`;
      filtersEl.hidden = false;
      const officerSel = document.getElementById('dashOfficer');
      const serviceSel = document.getElementById('dashService');
      // Same lists as the queue pages' filters (request-list.js).
      loadStaffDirectory().then(list => list.filter(s => s.role === 'legal_assistant')
        .forEach(s => officerSel.add(new Option(s.full_name + (s.is_active ? '' : ' (inactive)'), String(s.id)))));
      const fillServices = () => {
        serviceSel.length = 1;
        SERVICE_TYPES.forEach(s => serviceSel.add(new Option(s.label, s.code)));
        serviceSel.value = recent.service;
      };
      fillServices();
      apiFetch('/service-types').then(r => {
        if(r.ok && Array.isArray(r.data)){
          SERVICE_TYPES.length = 0;
          r.data.forEach(s => SERVICE_TYPES.push({code: s.code, label: s.name}));
          fillServices();
        }
      });
      officerSel.addEventListener('change', () => { recent.officer = officerSel.value; loadRecent(columns); });
      serviceSel.addEventListener('change', () => { recent.service = serviceSel.value; loadRecent(columns); });
    }
    loadRecent(columns, own);
  }

  async function loadRecent(columns, own){
    const mySeq = ++recentSeq;
    const p = new URLSearchParams();
    if(recent.service) p.set('service', recent.service);
    if(recent.officer) p.set('officer', recent.officer);
    seeAll.href = 'requests-all.html' + (p.toString() ? '?' + p : '');

    const result = await fetchAllRequests({serviceTypeCode: recent.service, officerId: recent.officer,
      sort: 'newest', limit: RECENT_LIMIT});
    if(mySeq !== recentSeq) return;   // a newer filter choice is already loading
    const filtered = !own && !!(recent.officer || recent.service);
    renderListTable(recentTable, result.rows, columns, {error: result.error, filtered},
      own ? 'No requests have been assigned to you yet.' : 'No requests have been submitted yet.');
  }

  // auth-guard.js sets window.currentStaff after its check; wait for it.
  (function start(triesLeft){
    if(!window.currentStaff){
      if(triesLeft > 0) setTimeout(() => start(triesLeft - 1), 100);
      return;
    }
    const tiles = tilesFor(window.currentStaff);
    drawTiles(tiles);
    loadCounts(tiles);
    startRecent(window.currentStaff);
  })(50);
})();
