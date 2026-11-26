// Shared top bar, sidebar, and page heading for the internal console.
// Load order matters: api-config.js, auth-guard.js, api-data.js, then
// render-helper.js (defines ICONS), then this file, then the page's own script.
//
// Each page keeps three one-line placeholders:
//   <header class="app-topbar" id="appTopbar"></header>
//   <nav class="app-sidebar" id="appSidebar"></nav>
//   <div class="breadcrumb-row" id="breadcrumbRow"></div>
// and calls renderTopbar(), renderSidebar('page-key'), and
// renderBreadcrumb({...}) after including this file.

const SIDEBAR_ITEMS = [
  {key:'dashboard', href:'dashboard.html', label:'Dashboard', icon:'dashboard'},
  {key:'requests', href:'requests-all.html', label:'Requests', icon:'requests', children:[
    {key:'requests-all',             href:'requests-all.html',             label:'All requests'},
    {key:'requests-assigned',        href:'requests-assigned.html',        label:'Assigned to me'},
    {key:'requests-review',          href:'requests-review.html',          label:'For Review'},
    {key:'requests-progress',        href:'requests-progress.html',        label:'In progress'},
    {key:'requests-awaiting-client', href:'requests-awaiting-client.html', label:'Awaiting client'},
    {key:'requests-approval',        href:'requests-approval.html',        label:'For Approval'},
    {key:'requests-reassignment',    href:'requests-reassignment.html',    label:'Reassignment requests', roles:['head_attorney']},
    {key:'requests-completed',       href:'requests-completed.html',       label:'Completed'}
  ]},
  {key:'reports',    href:'reports.html',    label:'Reports',    icon:'reports'},
  {key:'articles',   href:'articles.html',   label:'Articles',   icon:'documents'},
  {key:'holidays',   href:'holidays.html',   label:'Holidays',   icon:'calendar'},
  // Head Attorney only (the server refuses everyone else too). `group` puts a
  // small heading above the first visible item of each group.
  {key:'records-archive', href:'records-archive.html', label:'Records Archive', icon:'archive', roles:['head_attorney'], group:'Records'},
  {key:'security-log',    href:'security-log.html',    label:'Security Log',    icon:'shield',  roles:['head_attorney'], group:'Records'},
  {key:'user-management', href:'user-management.html', label:'User Management', icon:'users',   roles:['head_attorney'], group:'Administration'},
  {key:'service-types',   href:'service-types.html',   label:'Service Types',   icon:'settings', roles:['head_attorney'], group:'Administration'},
  {key:'offices',         href:'offices.html',         label:'Offices',         icon:'building', roles:['head_attorney'], group:'Administration'},
  {key:'settings',        href:'settings.html',        label:'Settings',        icon:'gear',    roles:['head_attorney'], group:'Administration'}
];

const ROLE_LABELS = {head_attorney:'Head Attorney', legal_assistant:'Legal Assistant', attorney:'Attorney'};

// "Saturday, 7 November 2026" (the computer's own date).
function todayLabel(){
  const d = new Date();
  const weekday = d.toLocaleDateString('en-GB', {weekday:'long'});
  const date = d.toLocaleDateString('en-GB', {day:'numeric', month:'long', year:'numeric'});
  return `${weekday}, ${date}`;
}

function renderTopbar(){
  const target = document.getElementById('appTopbar');
  if(!target) return;

  const staff = window.currentStaff;
  const initials = staff && staff.full_name
    ? staff.full_name.split(' ').filter(Boolean).map(w=>w[0]).join('').slice(0,2).toUpperCase()
    : '…'; // shown briefly before auth-guard.js's check resolves
  const displayName = staff ? staff.full_name : 'Loading…';
  const roleLabel = staff ? (ROLE_LABELS[staff.role] || '') : '';
  // auth-guard.js draws the top bar again once the sign-in check is done:
  // keep what was typed in the search box and whether a list searches live.
  const oldSearch = document.getElementById('topSearchInput');
  const searchHadFocus = !!oldSearch && document.activeElement === oldSearch;

  target.innerHTML = `
    <div class="app-brand">
      <div class="seal"><img src="../img/essu-logo.png" alt="Eastern Samar State University seal"></div>
      <div class="app-brand-title">The Legal Office</div>
    </div>
    <div class="app-topbar-main">
      <div class="app-search">
        ${ICONS.search}
        <input type="search" id="topSearchInput" maxlength="100" placeholder="Search requests by ID, name, email or office" aria-label="Search all requests">
      </div>
      <div class="app-topbar-right">
        <span class="topbar-today">${escapeHtml(todayLabel())}</span>
        <span class="topbar-divider today-divider" aria-hidden="true"></span>
        <div class="notif-wrap">
          <button type="button" class="icon-btn" id="notifBellBtn" title="Notifications" aria-label="Notifications" aria-haspopup="true" aria-expanded="false">${ICONS.bell}<span class="notif-count" id="notifCount" hidden></span></button>
          <div class="notif-panel" id="notifPanel" hidden>
            <div class="notif-head"><b>Notifications</b><button type="button" class="notif-readall" id="notifReadAll">Mark all as read</button></div>
            <div class="notif-list" id="notifList"><p class="notif-empty">Loading…</p></div>
          </div>
        </div>
        <span class="topbar-divider" aria-hidden="true"></span>
        <div class="avatar-menu-wrap">
          <button type="button" class="avatar-btn" id="avatarMenuBtn" aria-haspopup="true" aria-expanded="false">
            <span class="avatar-circle" aria-hidden="true">${escapeHtml(initials)}</span>
            <span class="avatar-text">
              <span class="avatar-name">${escapeHtml(displayName)}</span>
              ${roleLabel ? `<span class="avatar-role">${escapeHtml(roleLabel)}</span>` : ''}
            </span>
            ${ICONS.chevron}
          </button>
          <div class="avatar-dropdown" id="avatarDropdown">
            <button type="button" class="avatar-dropdown-logout" id="logoutBtn">Log out</button>
          </div>
        </div>
      </div>
    </div>
  `;

  // Top search: opens All requests with the search filled in (request-list.js).
  const topSearch = document.getElementById('topSearchInput');
  if(oldSearch){
    topSearch.value = oldSearch.value;
    if(oldSearch.dataset.liveList) topSearch.dataset.liveList = '1';
    if(searchHadFocus) topSearch.focus();
  }
  topSearch.addEventListener('keydown', (e)=>{
    // On All requests the box searches the list as you type (request-list.js).
    if(e.key !== 'Enter' || topSearch.dataset.liveList) return;
    const term = topSearch.value.trim().slice(0, 100);
    if(!term) return;
    window.location.href = 'requests-all.html?q=' + encodeURIComponent(term);
  });

  initNotificationBell();

  const menuBtn = document.getElementById('avatarMenuBtn');
  const dropdown = document.getElementById('avatarDropdown');
  const setMenu = (open)=>{
    dropdown.classList.toggle('open', open);
    menuBtn.setAttribute('aria-expanded', String(open));
  };
  menuBtn.addEventListener('click', (e)=>{
    e.stopPropagation();
    setMenu(!dropdown.classList.contains('open'));
  });
  document.addEventListener('click', ()=> setMenu(false));
  document.addEventListener('keydown', (e)=>{
    if(e.key === 'Escape' && dropdown.classList.contains('open')){ setMenu(false); menuBtn.focus(); }
  });

  document.getElementById('logoutBtn').addEventListener('click', async ()=>{
    try{
      const res = await fetch(`${API_BASE_URL}/auth/logout`, {method:'POST', credentials:'include'});
      if(res.status === 429){
        // Rate limit: the session was NOT ended, so don't pretend it was.
        showPageMessage('Too many requests in a short time. Wait a moment, then log out again.', 'error');
        return;
      }
    }catch(err){
      // Even if the network call fails, still send them to the login page.
    }
    window.location.href = 'login.html';
  });
}

let _lastSidebarActiveKey = null;

function canSeeNavItem(item){
  return !item.roles || (window.currentStaff && item.roles.includes(window.currentStaff.role));
}

// When the sidebar is collapsed the labels are hidden, so each icon gets a
// tooltip with its label; when it is open the tooltips are removed.
function setNavTooltips(sidebar, collapsed){
  sidebar.querySelectorAll('.nav-link').forEach(link=>{
    const label = link.querySelector('.label');
    if(collapsed && label) link.title = label.textContent;
    else link.removeAttribute('title');
  });
  const btn = document.getElementById('sidebarCollapseBtn');
  if(btn){
    const text = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    btn.title = text;
    btn.setAttribute('aria-label', text);
    btn.setAttribute('aria-expanded', String(!collapsed));
  }
}

/** "Skip to main content": the first Tab on every staff page, so keyboard
    users don't tab through the whole sidebar first. Added once. */
function addSkipLink(){
  if(document.getElementById('skipLink')) return;
  const main = document.querySelector('main');
  if(!main) return;
  if(!main.id) main.id = 'main';
  main.tabIndex = -1;   // so the link can move focus into it
  const link = document.createElement('a');
  link.id = 'skipLink';
  link.className = 'skip-link';
  link.href = '#' + main.id;
  link.textContent = 'Skip to main content';
  document.body.prepend(link);
}

function renderSidebar(activeKey){
  const target = document.getElementById('appSidebar');
  if(!target) return;
  addSkipLink();
  if(activeKey !== undefined) _lastSidebarActiveKey = activeKey;
  const key = _lastSidebarActiveKey;
  target.setAttribute('aria-label', 'Main');

  let html = `<div class="sidebar-collapse-row"><button type="button" class="sidebar-collapse" id="sidebarCollapseBtn">${ICONS.collapse}</button></div>`;
  let lastGroup = null;

  SIDEBAR_ITEMS.forEach(item=>{
    if(!canSeeNavItem(item)) return;
    if(item.group && item.group !== lastGroup){
      html += `<div class="nav-group-label">${escapeHtml(item.group)}</div><div class="nav-group-divider" aria-hidden="true"></div>`;
    }
    lastGroup = item.group || null;
    if(item.children){
      const visibleChildren = item.children.filter(canSeeNavItem);
      if(!visibleChildren.length) return;
      // The parent is only marked in the collapsed rail, where the sub-items are hidden.
      const childActive = visibleChildren.some(child => child.key === key);
      html += `<a class="nav-link${childActive ? ' current-parent' : ''}" href="${item.href}">${ICONS[item.icon]}<span class="label">${escapeHtml(item.label)}</span></a>`;
      html += `<div class="nav-sublist">`;
      visibleChildren.forEach(child=>{
        const current = child.key === key;
        html += `<a class="nav-sublink${current ? ' current' : ''}" href="${child.href}"${current ? ' aria-current="page"' : ''}>${escapeHtml(child.label)}</a>`;
      });
      html += `</div>`;
    } else {
      const current = item.key === key;
      html += `<a class="nav-link${current ? ' current' : ''}" href="${item.href}"${current ? ' aria-current="page"' : ''}>${ICONS[item.icon]}<span class="label">${escapeHtml(item.label)}</span></a>`;
    }
  });
  target.innerHTML = html;

  let storedCollapsed = null;
  try{ storedCollapsed = localStorage.getItem('sidebarCollapsed'); }catch(_){ }
  target.classList.toggle('collapsed', storedCollapsed === '1');
  setNavTooltips(target, storedCollapsed === '1');

  document.getElementById('sidebarCollapseBtn').addEventListener('click', ()=>{
    const isCollapsed = target.classList.toggle('collapsed');
    setNavTooltips(target, isCollapsed);
    try{ localStorage.setItem('sidebarCollapsed', isCollapsed ? '1' : '0'); }catch(_){ }
  });
}

/**
 * config: { title, count (optional), actions (optional): ['newRequest'],
 *           back (optional): {href, label} }
 * Renders the page heading: back button (subpages only), title, count, and
 * the right-side actions.
 */
function renderBreadcrumb(config){
  const target = document.getElementById('breadcrumbRow');
  if(!target) return;
  const countHtml = (config.count != null) ? `<span class="count-pill">${escapeHtml(String(config.count))}</span>` : '';
  let actionsHtml = '';
  if(config.actions && config.actions.includes('newRequest')){
    actionsHtml += `<button type="button" class="btn btn-primary" id="newRequestBtn">${ICONS.plus} New request</button>`;
  }
  target.innerHTML = `
    ${config.back ? backButtonHtml(config.back) : ''}
    <div class="page-heading"><h1 class="page-title">${escapeHtml(config.title)}</h1>${countHtml}</div>
    ${actionsHtml ? `<div class="breadcrumb-actions">${actionsHtml}</div>` : ''}
  `;
  if(config.back) wireBackButton(target.querySelector('.back-btn'));
  const newRequestBtn = document.getElementById('newRequestBtn');
  if(newRequestBtn){
    newRequestBtn.addEventListener('click', ()=>{ window.location.href = 'new-request.html'; });
  }
}

/* ---------- Back button on subpages ----------
   Request Detail, the walk-in form and the editors. It is a real link to
   the page above (href), so it also works when the page was opened on its
   own or in a new tab. When the person came from another console page in
   this tab, it steps back instead, so that list keeps its filters, search
   and page. Not after the walk-in form (going back would show the empty
   form again) or the login page. */
function backButtonHtml(back){
  const label = back.label || 'Back';
  return `<a class="back-btn" href="${escapeHtml(back.href)}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}">${ICONS.back}</a>`;
}

function cameFromConsoleList(){
  if(window.history.length < 2 || !document.referrer) return false;
  let from;
  try{ from = new URL(document.referrer); }catch(_){ return false; }
  if(from.origin !== window.location.origin) return false;
  if(!/\/internal\/[\w-]+\.html$/.test(from.pathname)) return false;
  if(from.pathname === window.location.pathname) return false;
  return !/\/(login|new-request)\.html$/.test(from.pathname);
}

function wireBackButton(link){
  if(!link) return;
  link.addEventListener('click', (e)=>{
    if(e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;   // new tab: follow the link
    if(cameFromConsoleList()){ e.preventDefault(); window.history.back(); }
  });
}

/* ---------- Page message bar and confirm dialog ----------
   These replace the browser's alert() and confirm() boxes (design audit).

   showPageMessage(text, tone, {keep}) shows one message bar under the top
   bar. tone: 'error' (default), 'success', 'info' or 'warning'. A success
   message closes itself after 8 seconds unless keep is true; the others stay
   until closed or replaced. An empty text hides it.

   confirmDialog({title, message, confirmLabel, cancelLabel, danger}) returns
   a Promise: true when the person confirms, false when they go back (the
   button, Escape). Only risky steps ask. Uses the browser's own <dialog>,
   so focus stays inside it while it is open. */
const NOTICE_ICON_KEYS = {error:'noticeError', success:'noticeSuccess', info:'noticeInfo', warning:'noticeWarning'};
let noticeTimer = null;

function showPageMessage(text, tone, options){
  const opts = options || {};
  const kind = NOTICE_ICON_KEYS[tone] ? tone : 'error';
  let bar = document.getElementById('pageNotice');
  clearTimeout(noticeTimer);
  if(!text){ if(bar) bar.hidden = true; return; }
  if(!bar){
    bar = document.createElement('div');
    bar.id = 'pageNotice';
    document.body.appendChild(bar);
  }
  bar.className = `page-notice ${kind}`;
  bar.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  bar.innerHTML = `${ICONS[NOTICE_ICON_KEYS[kind]]}<p class="page-notice-text"></p>
    <button type="button" class="page-notice-close" aria-label="Close message">${ICONS.close}</button>`;
  bar.querySelector('.page-notice-text').textContent = String(text);   // may include what someone typed
  bar.querySelector('.page-notice-close').addEventListener('click', ()=>{ bar.hidden = true; });
  bar.hidden = false;
  if(kind === 'success' && !opts.keep){
    noticeTimer = setTimeout(()=>{ bar.hidden = true; }, 8000);
  }
}

function confirmDialog(options){
  const o = Object.assign({
    title: 'Are you sure?', message: '', confirmLabel: 'Continue', cancelLabel: 'Go back', danger: false
  }, options || {});
  return new Promise(resolve=>{
    const returnFocus = document.activeElement;
    const dlg = document.createElement('dialog');
    dlg.className = 'confirm-dialog';
    dlg.setAttribute('aria-labelledby', 'confirmDialogTitle');
    dlg.setAttribute('aria-describedby', 'confirmDialogText');
    dlg.innerHTML = `
      <h2 class="confirm-dialog-title" id="confirmDialogTitle"></h2>
      <p class="confirm-dialog-text" id="confirmDialogText"></p>
      <div class="confirm-dialog-actions">
        <button type="button" class="btn btn-tertiary" data-answer="no"></button>
        <button type="button" class="btn ${o.danger ? 'btn-danger' : 'btn-primary'}" data-answer="yes"></button>
      </div>`;
    dlg.querySelector('.confirm-dialog-title').textContent = o.title;
    dlg.querySelector('.confirm-dialog-text').textContent = o.message;
    dlg.querySelector('[data-answer="no"]').textContent = o.cancelLabel;
    dlg.querySelector('[data-answer="yes"]').textContent = o.confirmLabel;
    if(!o.message) dlg.querySelector('.confirm-dialog-text').remove();

    let answer = false;
    dlg.addEventListener('click', e=>{
      const btn = e.target.closest('[data-answer]');
      if(!btn) return;
      answer = btn.dataset.answer === 'yes';
      dlg.close();
    });
    dlg.addEventListener('close', ()=>{
      dlg.remove();
      if(returnFocus && document.contains(returnFocus) && returnFocus.focus) returnFocus.focus();
      resolve(answer);
    });
    document.body.appendChild(dlg);
    dlg.showModal();
    // A risky step starts on "Go back"; an ordinary one on the main button.
    dlg.querySelector(o.danger ? '[data-answer="no"]' : '[data-answer="yes"]').focus();
  });
}

/* ---------------- Notification bell ----------------
   In-LEXIS notifications (routers/notifications.py). The unread count is
   checked once a minute, only while this tab is visible; after 3 errors in
   a row it stops checking until the page is reloaded (no endless retries).
   The list itself is only fetched when the bell is opened. */
const NOTIF_POLL_MS = 60 * 1000;
const NOTIF_MAX_FAILURES = 3;
let notifTimer = null;
let notifFailures = 0;

function setNotifCount(count){
  const badge = document.getElementById('notifCount');
  if(!badge) return;
  badge.hidden = !count;
  badge.textContent = count > 99 ? '99+' : String(count);
  document.getElementById('notifBellBtn').title = count ? `Notifications (${count} unread)` : 'Notifications';
}

async function refreshNotifCount(){
  if(document.visibilityState !== 'visible' || notifFailures >= NOTIF_MAX_FAILURES) return;
  const r = await apiFetch('/notifications/unread-count', {timeoutMs: 10000});
  if(r.ok){ notifFailures = 0; setNotifCount(r.data.count); return; }
  if(++notifFailures >= NOTIF_MAX_FAILURES && notifTimer){ clearInterval(notifTimer); notifTimer = null; }
}

function notifTimeAgo(iso){
  const t = parseServerTime(iso);
  const mins = Math.round((Date.now() - t.getTime()) / 60000);
  if(mins < 1) return 'just now';
  if(mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if(hours < 24) return `${hours} h ago`;
  return formatShortDate(iso);
}

async function loadNotifList(){
  const list = document.getElementById('notifList');
  const r = await apiFetch('/notifications?limit=20');
  if(!r.ok){ list.innerHTML = `<p class="notif-empty">${escapeHtml(r.error || 'Could not load notifications.')}</p>`; return; }
  setNotifCount(r.data.unread_count);
  if(!r.data.items.length){ list.innerHTML = '<p class="notif-empty">No notifications yet.</p>'; return; }
  list.innerHTML = r.data.items.map(n => `
    <button type="button" class="notif-item${n.read ? '' : ' unread'}${n.kind === 'security_alert' ? ' alert' : ''}"
            data-id="${n.id}" data-tracking="${escapeHtml(n.tracking_id || '')}" data-kind="${escapeHtml(n.kind)}">
      <span class="notif-msg">${escapeHtml(n.message)}</span>
      <span class="notif-when">${escapeHtml(notifTimeAgo(n.created_at))}</span>
    </button>`).join('');
  list.querySelectorAll('.notif-item').forEach(item => {
    item.addEventListener('click', async () => {
      if(item.classList.contains('unread')) await apiFetch(`/notifications/${item.dataset.id}/read`, {method: 'POST'});
      if(item.dataset.tracking){
        window.location.href = 'request-detail.html?id=' + encodeURIComponent(item.dataset.tracking);
      }else if(item.dataset.kind === 'security_alert'){
        window.location.href = 'security-log.html';
      }else{
        item.classList.remove('unread');
        refreshNotifCount();
      }
    });
  });
}

function initNotificationBell(){
  const btn = document.getElementById('notifBellBtn');
  const panel = document.getElementById('notifPanel');
  if(!btn || !panel) return;
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = panel.hidden;
    panel.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    if(open) loadNotifList();
  });
  panel.addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('click', () => { panel.hidden = true; btn.setAttribute('aria-expanded', 'false'); });
  document.addEventListener('keydown', (e) => { if(e.key === 'Escape'){ panel.hidden = true; btn.setAttribute('aria-expanded', 'false'); } });
  document.getElementById('notifReadAll').addEventListener('click', async () => {
    const r = await apiFetch('/notifications/read-all', {method: 'POST'});
    if(r.ok){ setNotifCount(0); loadNotifList(); }
  });
  refreshNotifCount();
  notifTimer = setInterval(refreshNotifCount, NOTIF_POLL_MS);
  document.addEventListener('visibilitychange', refreshNotifCount);
}

