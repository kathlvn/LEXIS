// All requests (requests-all.html). Kept out of the page: the Content-Security-Policy forbids inline scripts.
renderTopbar();
renderSidebar('requests-all');
renderBreadcrumb({title:'All requests', actions:['newRequest']});

const columns = [
  {key:'id', render:r=>`<span class="tracking-id">${escapeHtml(r.id)}</span>`},
  {key:'service'},
  {key:'client', render:clientCell},
  {key:'submitted'},
  {key:'officer'},
  {key:'status', render:statusCell},
  {key:'due', render:dueCell}
];
const table = document.getElementById('allRequestsTable');

// The search box for this page is the one in the top bar.
initRequestList({
  controls: ['client','clientWaiting','service','status','officer','deadline','date','sort'],
  searchInputId: 'topSearchInput',
  base: {},
  render(rows, info){
    renderBreadcrumb({title:'All requests', count: info.total, actions:['newRequest']});
    renderListTable(table, rows, columns, info, 'No requests have been submitted yet.');
  }
});
