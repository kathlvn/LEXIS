// Reassignment requests (requests-reassignment.html). Kept out of the page: the Content-Security-Policy forbids inline scripts.
renderTopbar();
renderSidebar('requests-reassignment');
renderBreadcrumb({title:'Reassignment requests'});

const columns = [
  {key:'id', render:r=>`<span class="tracking-id">${escapeHtml(r.id)}</span>`},
  {key:'service'},
  {key:'client', render:clientCell},
  {key:'officer'},
  {key:'reassignmentReason', render:r=>escapeHtml(r.reassignmentReason || '\u2014')},
  {key:'submitted'}
];
const table = document.getElementById('reassignmentTable');

initRequestList({
  controls: ['q'],
  searchPlaceholder: 'Search reassignment requests',
  base: {reassignmentRequested:true},
  render(rows, info){
    renderBreadcrumb({title:'Reassignment requests', count: info.total});
    renderListTable(table, rows, columns, info, 'No pending reassignment requests.');
  }
});
