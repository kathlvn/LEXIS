// Completed (requests-completed.html). Kept out of the page: the Content-Security-Policy forbids inline scripts.
renderTopbar();
renderSidebar('requests-completed');
renderBreadcrumb({title:'Completed'});

const columns = [
  {key:'id', render:r=>`<span class="tracking-id">${escapeHtml(r.id)}</span>`},
  {key:'service'},
  {key:'client', render:clientCell},
  {key:'officer'},
  {key:'submitted'}
];
const table = document.getElementById('completedTable');

initRequestList({
  controls: ['q','service','officer','date','sort'],
  searchPlaceholder: 'Search completed requests',
  base: {status:'Completed'},
  render(rows, info){
    renderBreadcrumb({title:'Completed', count: info.total});
    renderListTable(table, rows, columns, info, 'No requests have been completed yet.');
  }
});
