// For review (requests-review.html). Kept out of the page: the Content-Security-Policy forbids inline scripts.
renderTopbar();
renderSidebar('requests-review');
renderBreadcrumb({title:'For Review'});

const columns = [
  {key:'id', render:r=>`<span class="tracking-id">${escapeHtml(r.id)}</span>`},
  {key:'service'},
  {key:'client', render:clientCell},
  {key:'submitted'},
  {key:'officer'},
  {key:'due', render:dueCell}
];
const table = document.getElementById('reviewTable');

initRequestList({
  controls: ['q','service','officer','sort'],
  searchPlaceholder: 'Search requests in review',
  base: {status:'Under Review'},
  render(rows, info){
    renderBreadcrumb({title:'For Review', count: info.total});
    renderListTable(table, rows, columns, info, 'Nothing is awaiting review.');
  }
});
