// Home page. The services list comes from
// the server (GET /service-types, the same list the Submit form uses), so a
// service the Head Attorney adds or edits shows here too. Everything is built
// with textContent, so nothing typed by staff is ever read as HTML.

// Each service's colour and icon, in the order they are shown.
const SERVICE_LOOK = {
  legal_advice:      {accent: 'green',      icon: 'legal-advice'},
  legal_research:    {accent: 'teal',       icon: 'legal-research'},
  contract_review:   {accent: 'gold',       icon: 'contract-review'},
  investigation:     {accent: 'plum',       icon: 'investigation'},
  drafting_document: {accent: 'terracotta', icon: 'drafting'},
  other:             {accent: 'sage',       icon: 'other-concerns'},
  complaint_referral:{accent: 'brick',      icon: 'complaint'}
};
const COMPLAINT_CODE = 'complaint_referral';

// A service added later by the Head Attorney: comes after these, with this look.
const NEW_SERVICE_LOOK = {accent: 'sage', icon: 'other-concerns'};

function el(tag, className, text){
  const node = document.createElement(tag);
  if(className) node.className = className;
  if(text !== undefined) node.textContent = text;
  return node;
}

function icon(file){
  const img = el('img');
  img.src = 'img/icons/' + file;
  img.alt = '';   // Decorative: the words beside it say the same thing.
  return img;
}

function accentSquare(code){
  const look = SERVICE_LOOK[code] || NEW_SERVICE_LOOK;
  const square = el('span', 'accent-square accent-' + look.accent);
  square.appendChild(icon(look.icon + '.svg'));
  return square;
}

function serviceRank(service){
  const rank = Object.keys(SERVICE_LOOK).indexOf(service.code);
  return rank === -1 ? 99 : rank;
}

function periodLine(service){
  const line = el('p', 'service-period');
  const value = service.days
    ? ` ${service.days} working day${service.days === 1 ? '' : 's'}`
    : ' depends on the request';
  line.append('Processing period:', el('b', '', value));
  return line;
}

// Name, description and processing period.
function serviceMain(service){
  const main = el('div', 'service-main');
  const head = el('div', 'service-head');
  head.append(accentSquare(service.code), el('h3', '', service.label));
  main.appendChild(head);
  if(service.description) main.appendChild(el('p', 'service-desc', service.description));
  main.appendChild(periodLine(service));
  return main;
}

// Desktop shows the label and the list. Phones show a button that opens the
// list instead (CSS picks which), so a long page of services stays short.
function serviceDocs(service, index){
  const docs = el('div', 'service-docs');
  const listId = 'serviceDocs' + index;

  const toggle = el('button', 'service-docs-toggle', 'Documents to prepare');
  toggle.type = 'button';
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', listId);
  toggle.appendChild(icon('chevron-down-green.svg'));
  toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!open));
  });

  const list = el('ul', 'service-docs-list');
  list.id = listId;
  service.requiredDocuments.forEach(d => list.appendChild(el('li', '', d)));

  docs.append(el('p', 'service-docs-label', 'Documents to prepare'), toggle, list);
  return docs;
}

function serviceCard(service, index){
  const card = el('article', 'service-card');
  // A search result opens this card (home.html#service-legal_advice).
  card.id = 'service-' + service.code;
  card.tabIndex = -1;
  card.appendChild(serviceMain(service));
  if(service.requiredDocuments && service.requiredDocuments.length){
    card.append(el('hr', 'service-divider'), serviceDocs(service, index));
  }
  return card;
}

// Complaint / Referral is open to anyone, so it gets a wide card with its own button.
function complaintCard(service, index){
  const card = serviceCard(service, index);
  card.classList.add('service-card-wide');
  const action = el('a', 'nav-btn btn-secondary', 'File a Complaint or Referral');
  action.href = 'portal.html?door=complaint';
  action.appendChild(icon('arrow-right-green.svg'));
  card.appendChild(action);
  return card;
}

function notice(kind, title, message){
  const box = el('div', 'notice notice-' + kind);
  box.setAttribute('role', 'status');
  const content = el('div');
  const text = el('p', 'notice-message');
  text.append(...message);
  content.append(el('p', 'notice-title', title), text);
  box.append(icon(kind + '.svg'), content);
  return box;
}

function serviceNameCard(service){
  const card = el('article', 'service-card service-card-name');
  card.append(accentSquare(service.code), el('h3', '', service.label));
  return card;
}

function skeletonBars(card, bars){
  bars.forEach(kind => card.appendChild(el('div', 'skeleton-bar skeleton-' + kind)));
  return card;
}

(async function renderHomeServices(){
  const wrap = document.getElementById('homeServices');
  const status = document.getElementById('homeServicesStatus');

  // Grey cards hold the space, so the page doesn't jump when the real ones arrive.
  status.textContent = 'Loading services…';
  for(let i = 0; i < 6; i++){
    wrap.appendChild(skeletonBars(el('div', 'skeleton-card'), ['icon', 'title', 'line', 'short']));
  }

  const loaded = await loadServiceTypes();
  wrap.textContent = '';
  status.textContent = '';

  const services = SERVICE_TYPES.slice().sort((a, b) => serviceRank(a) - serviceRank(b));

  // Server not reached: the built-in list has names only. Say what is missing
  // rather than show a wrong processing period.
  if(!loaded){
    wrap.appendChild(notice('warning', 'Service details could not be loaded',
      ['Processing periods and documents are missing below. Reload the page to try again.']));
    services.forEach(s => wrap.appendChild(serviceNameCard(s)));
    return;
  }

  // Only if the Head Attorney has hidden every service.
  if(!services.length){
    const mail = el('a', '', 'legaloffice@essu.edu.ph');
    mail.href = 'mailto:legaloffice@essu.edu.ph';
    wrap.appendChild(notice('info', 'No services are listed right now',
      ['Contact the Legal Office at ', mail, '.']));
    return;
  }

  services.forEach((s, i) => {
    if(s.code !== COMPLAINT_CODE) wrap.appendChild(serviceCard(s, i));
  });
  services.forEach((s, i) => {
    if(s.code === COMPLAINT_CODE) wrap.appendChild(complaintCard(s, i));
  });

  // The cards didn't exist yet when the browser looked for the link's target.
  if(location.hash.startsWith('#service-')){
    const target = document.getElementById(location.hash.slice(1));
    if(target){
      target.focus({preventScroll: true});
      target.scrollIntoView();
    }
  }
})();

// Latest articles: the 3 newest published articles.
// The section is removed if none are published or the server can't be
// reached, so Home never shows an empty or broken box.
(async function renderHomeArticles(){
  const section = document.getElementById('articles');
  const wrap = document.getElementById('homeArticles');
  const status = document.getElementById('homeArticlesStatus');

  status.textContent = 'Loading articles…';
  for(let i = 0; i < 3; i++){
    wrap.appendChild(skeletonBars(el('div', 'skeleton-card'), ['article-title', 'date', 'line', 'line', 'short']));
  }

  let rows = [];
  try{
    const res = await fetch(`${API_BASE_URL}/public/articles?limit=3`);
    if(res.ok) rows = await res.json();
  }catch(err){
    // Handled below: no rows, so the section is removed.
  }

  status.textContent = '';
  wrap.textContent = '';
  if(!Array.isArray(rows) || !rows.length){
    section.hidden = true;
    return;
  }
  rows.forEach(a => wrap.appendChild(articleCard(a)));
  document.getElementById('homeArticlesAll').hidden = false;
})();
