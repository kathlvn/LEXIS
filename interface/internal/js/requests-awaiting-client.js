// Awaiting client (requests-awaiting-client.html). Kept out of the page: the Content-Security-Policy forbids inline scripts.
// Two columns of cards, each with what
// staff asked the client for.
renderTopbar();
renderSidebar('requests-awaiting-client');
renderBreadcrumb({title:'Awaiting client'});

const grid = document.getElementById('cardGrid');
initRequestList({
  controls: ['q','service','officer','sort'],
  searchPlaceholder: 'Search requests awaiting the client',
  toolbarClass: 'list-toolbar-queue',
  base: {waitingForClient:true},
  pageSize: 24,
  render(rows, info){
    renderBreadcrumb({title:'Awaiting client', count: info.total});
    grid.innerHTML = '';
    if(info.error){ grid.innerHTML = `<p class="table-state error">${escapeHtml(info.error)}</p>`; return; }
    if(!rows.length){
      grid.innerHTML = `<p class="table-state empty">${info.filtered ? 'No requests match your search or filters.' : 'No requests are currently awaiting the client.'}</p>`;
      return;
    }
    // Waiting for the client does not pause the clock, so
    // each card still shows the due date.
    rows.forEach(row => grid.appendChild(renderAwaitingClientCard(row)));
  }
});
