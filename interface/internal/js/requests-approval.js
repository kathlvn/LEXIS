// For Approval (requests-approval.html). Kept out of the page: the Content-Security-Policy forbids inline scripts.
// A table of the requests waiting for the
// attorney, with who prepared each one.
renderTopbar();
renderSidebar('requests-approval');
renderBreadcrumb({title:'For Approval'});

// "Resubmitted after changes": back for approval after the attorney
// returned it for changes.
function approvalStatusCell(row){
  const note = row.wasReturnedForRevision ? '<span class="status-note">Resubmitted after changes</span>' : '';
  return `<div class="cell-stack status-stack">${statusLabel(row.status, 'Waiting for approval')}${note}</div>`;
}

const columns = [
  {key:'id', render:r=>`<span class="tracking-id">${escapeHtml(r.id)}</span>`},
  {key:'service'},
  {key:'client', render:clientCell},
  {key:'submitted'},
  {key:'preparedBy', render:r=>escapeHtml(r.preparedByOfficer || r.officer)},
  {key:'status', render:approvalStatusCell},
  {key:'due', render:dueCell}
];
const table = document.getElementById('approvalTable');

initRequestList({
  controls: ['q','service','sort'],
  searchPlaceholder: 'Search requests for approval',
  toolbarClass: 'list-toolbar-queue service-220',
  base: {status:'For Attorney Review'},
  render(rows, info){
    renderBreadcrumb({title:'For Approval', count: info.total});
    renderListTable(table, rows, columns, info, 'Nothing is currently waiting for approval.');
  }
});
