// Assigned to me (requests-assigned.html). Kept out of the page: the Content-Security-Policy forbids inline scripts.
// Four count tiles, then your open requests
// in three due-date groups, soonest first.
renderTopbar();
renderSidebar('requests-assigned');
renderBreadcrumb({title:'Assigned to me'});

const columns = [
  {key:'id', render:r=>`<span class="tracking-id">${escapeHtml(r.id)}</span>`},
  {key:'service'},
  {key:'client', render:clientCell},
  {key:'submitted'},
  {key:'status', render:statusCell},
  {key:'due', render:dueCell}
];
const table = document.getElementById('assignedTable');
const BASE = {assignedToMe:true, openOnly:true};

function bucketFor(row){
  // Red group: the server's deadline status (working days + holidays).
  if(row.deadline === 'overdue' || row.deadline === 'approaching') return 'soon';
  if(!row.dueDateRaw) return 'later';
  const due = new Date(row.dueDateRaw);
  const today = new Date();
  today.setHours(0,0,0,0);
  due.setHours(0,0,0,0);
  const daysUntilDue = Math.round((due - today) / 86400000);
  if(daysUntilDue <= 0) return 'soon';
  if(daysUntilDue <= 7) return 'week';
  return 'later';
}

function groupRows(rows){
  const groups = {soon:[], week:[], later:[]};
  rows.forEach(r => groups[bucketFor(r)].push(r));
  return groups;
}

// The tiles and the heading count show all your open work, so a search
// below never changes them.
function showTiles(rows, total){
  const groups = groupRows(rows);
  document.getElementById('dueTodayNum').textContent = groups.soon.length;
  document.getElementById('dueWeekNum').textContent = groups.week.length;
  document.getElementById('laterNum').textContent = groups.later.length;
  document.getElementById('tileSoon').classList.toggle('danger', groups.soon.length > 0);
  renderBreadcrumb({title:'Assigned to me', count: total});
}

// Completed count: only the total is needed, so ask for one row.
fetchAllRequests({assignedToMe:true, status:'Completed', limit:1}).then(r => {
  document.getElementById('completedNum').textContent = r.error ? '—' : (r.total ?? 0);
});

// 200 per page is far more than one person's open cases; if there are
// ever more, the pager appears below.
let tilesShown = false;
initRequestList({
  controls: ['q','service'],
  searchPlaceholder: 'Search my requests',
  toolbarClass: 'list-toolbar-queue service-240',
  sortNote: 'Sorted by due date, soonest first',
  base: BASE,
  defaultSort: 'due',
  pageSize: 200,
  render(rows, info){
    table.querySelectorAll('tbody').forEach(tb => tb.remove());
    if(info.error){ showTableState(table, [], info.error, ''); return; }
    if(!info.filtered){ showTiles(rows, info.total); tilesShown = true; }
    const groups = groupRows(rows);
    [['soon', 'Overdue or due soon', 'danger'], ['week', 'Due this week', ''], ['later', 'Later or no due date', '']]
      .forEach(([key, label, tone]) => {
        if(groups[key].length) table.appendChild(buildGroupedTableBody(groups[key], columns, label, tone));
      });
    showTableState(table, rows, null,
      info.filtered ? 'No requests match your search.' : 'You have no open requests assigned to you.');
  }
});

// Opened with a search already in the address (Back, a refresh): the list
// is filtered, so the tiles are counted with one unfiltered call.
const opening = new URLSearchParams(window.location.search);
if(opening.get('q') || opening.get('service')){
  fetchAllRequests({...BASE, sort:'due', limit:200}).then(r => {
    if(!r.error && !tilesShown) showTiles(r.rows, r.total ?? r.rows.length);
  });
}
