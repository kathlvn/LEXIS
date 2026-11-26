// In progress (requests-progress.html). Kept out of the page: the Content-Security-Policy forbids inline scripts.
renderTopbar();
renderSidebar('requests-progress');
renderBreadcrumb({title:'In progress'});

const columns = [
  {key:'id', render:r=>`<span class="tracking-id">${escapeHtml(r.id)}</span>`},
  {key:'service'},
  {key:'client', render:clientCell},
  {key:'officer'},
  {key:'due', render:dueCell}
];
const table = document.getElementById('progressTable');

initRequestList({
  controls: ['q','service','officer','deadline','sort'],
  searchPlaceholder: 'Search requests in progress',
  base: {status:'Processing'},
  defaultSort: 'due',
  render(rows, info){
    renderBreadcrumb({title:'In progress', count: info.total});
    renderListTable(table, rows, columns, info, 'No requests are currently in processing.');
  }
});
