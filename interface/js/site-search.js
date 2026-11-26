// Site search.
// Loaded on every public page. Adds the panel the header's Search button opens,
// and suggestions under the large field on Home.
//
// The search runs here, in the visitor's browser. The list of things to search
// (the index) is read once, the first time someone uses a search field:
//   services: GET /service-types (the list Home already shows)
//   articles: GET /public/articles/search-text (published articles only)
//   pages:    our own public pages, read from <main>, so the words can't go stale
// Suggestions are worked out here, so typing sends nothing to the API, and nothing
// goes to another company. Opening the results page puts the words in its address
// (search.html?q=...), so that one request reaches the web
// server. Caddy keeps no access log today. Everything is built with textContent,
// so typed words and staff text are never read as HTML.
window.SiteSearch = (function(){
  const MIN_CHARS = 2;          // suggestions start after 2 characters
  const MAX_SUGGESTIONS = 6;    // up to 6 matches under the field
  const MAX_WORDS = 8;
  const RETRY_AFTER_MS = 10000; // After a failed read, wait before trying again.

  const GROUPS = [
    {key: 'services', title: 'Services'},
    {key: 'articles', title: 'Articles'},
    {key: 'pages', title: 'Pages'}
  ];

  // The pages people can find. Not the request pages: they need a sign-in.
  const PAGES = [
    {title: 'Home', url: 'home.html', skip: '#contact'},
    {title: 'About', url: 'about.html'},
    {title: 'My Requests', url: 'portal.html'},
    {title: 'Articles', url: 'articles.html'},
    {title: 'Contact', url: 'home.html#contact', file: 'home.html', part: '#contact'},
    {title: 'Privacy Notice', url: 'privacy.html'},
    {title: 'Terms of Use', url: 'terms.html'},
    {title: 'Cookies', url: 'cookies.html'}
  ];

  const ALL_CATEGORIES = ['essu_office', 'essu_employee', 'essu_student', 'ext_government', 'ext_private'];

  function el(tag, className, text){
    const node = document.createElement(tag);
    if(className) node.className = className;
    if(text !== undefined) node.textContent = text;
    return node;
  }

  // Lower case and no accents, so "Peña" and "pena" match each other.
  function fold(text){
    return String(text == null ? '' : text).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  }

  function plural(count, word){
    return `${count} ${word}${count === 1 ? '' : 's'}`;
  }

  // Who may use a service, from its saved categories. A mix with no short,
  // accurate name is left out rather than guessed.
  function audience(categories){
    const list = categories || [];
    if(ALL_CATEGORIES.every(c => list.includes(c))) return 'Open to anyone';
    if(list.some(c => c.startsWith('ext_')) || !list.length) return '';
    return list.includes('essu_student') ? 'ESSU offices, officials and students' : 'ESSU offices and officials';
  }

  function item(group, title, url, text, extra){
    return Object.assign({group, title, url, titleKey: fold(title), textKey: fold(title + ' ' + text)}, extra);
  }

  async function readJson(path){
    const res = await fetch(API_BASE_URL + path);
    if(!res.ok) throw new Error(String(res.status));
    const rows = await res.json();
    if(!Array.isArray(rows)) throw new Error('Unexpected answer');
    return rows;
  }

  // A page that can't be read is left out; the other results still show.
  async function readPages(){
    const files = [...new Set(PAGES.map(p => p.file || p.url))];
    const docs = {};
    await Promise.all(files.map(async file => {
      try{
        const res = await fetch(file);
        if(res.ok) docs[file] = new DOMParser().parseFromString(await res.text(), 'text/html');
      }catch(err){
        // Left out, as above.
      }
    }));
    const items = [];
    PAGES.forEach(page => {
      const doc = docs[page.file || page.url];
      const part = doc && doc.querySelector(page.part || 'main');
      if(!part) return;
      const copy = part.cloneNode(true);
      copy.querySelectorAll('script,style,template,[hidden]').forEach(n => n.remove());
      if(page.skip) copy.querySelectorAll(page.skip).forEach(n => n.remove());
      items.push(item('pages', page.title, page.url, copy.textContent));
    });
    return items;
  }

  let index = null;      // The items, once read.
  let reading = null;    // The read in progress.
  let failedAt = 0;

  // Resolves true when the index is ready, false when it can't be read.
  function loadIndex(){
    if(index) return Promise.resolve(true);
    if(reading) return reading;
    if(failedAt && Date.now() - failedAt < RETRY_AFTER_MS) return Promise.resolve(false);

    reading = (async () => {
      try{
        const [services, articles, pages] = await Promise.all([
          readJson('/service-types'), readJson('/public/articles/search-text'), readPages()
        ]);
        if(!pages.length) throw new Error('No pages could be read');
        index = [
          ...services.map(s => item('services', s.name, 'home.html#service-' + encodeURIComponent(s.code),
            [s.description, ...(s.required_documents || [])].join(' '),
            {who: audience(s.categories), days: s.default_processing_days || null})),
          ...articles.map(a => item('articles', a.title, 'article.html?id=' + encodeURIComponent(a.id), a.text,
            {publishedAt: a.published_at})),
          ...pages
        ];
        return true;
      }catch(err){
        failedAt = Date.now();
        return false;
      }finally{
        reading = null;
      }
    })();
    return reading;
  }

  // Every word must appear. Inside a group, matches in the title come first.
  function find(query){
    const found = {services: [], articles: [], pages: [], total: 0};
    if(!index) return found;
    const words = fold(query).split(/\s+/).filter(Boolean).slice(0, MAX_WORDS);
    if(!words.length) return found;
    const scored = index
      .filter(entry => words.every(w => entry.textKey.includes(w)))
      .map((entry, order) => ({entry, order, score: words.filter(w => entry.titleKey.includes(w)).length}))
      .sort((a, b) => b.score - a.score || a.order - b.order);
    scored.forEach(({entry}) => found[entry.group].push(entry));
    found.total = scored.length;
    return found;
  }

  // The grey line under a result. withDate: the full results page shows article dates.
  function describe(entry, query, withDate){
    if(entry.group === 'services'){
      const period = entry.days ? plural(entry.days, 'working day') : 'No set period';
      return ['Service', entry.who, period].filter(Boolean).join(' · ');
    }
    if(entry.group === 'articles'){
      return ['Article', 'The Legal Office', withDate ? articleDate(entry.publishedAt) : ''].filter(Boolean).join(' · ');
    }
    return `Page · mentions “${query}”`;
  }

  // Up to 6 under the field, taken from each group in turn, so a long list of
  // services can't push the articles and pages out of sight.
  function pickSuggestions(found){
    const picked = {services: [], articles: [], pages: []};
    let count = 0;
    let added = true;
    for(let round = 0; added && count < MAX_SUGGESTIONS; round++){
      added = false;
      GROUPS.forEach(({key}) => {
        if(count < MAX_SUGGESTIONS && found[key][round]){
          picked[key].push(found[key][round]);
          count++;
          added = true;
        }
      });
    }
    return picked;
  }

  let fieldCount = 0;

  /* Suggestions for one search form.
     options.closeOnBlur: on Home the list floats over the page, so it closes
     when focus leaves the form. In the header panel it stays. Returns the
     controls the panel needs. */
  function attachField(form, options){
    const input = form.querySelector('input[type="search"]');
    const listId = input.id + 'List';
    const panel = el('div', 'search-suggest');
    panel.hidden = true;
    const live = el('p', 'visually-hidden');
    live.setAttribute('role', 'status');
    form.append(panel, live);

    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    // aria-controls is set only while the list exists (showResults), so it
    // never points to a missing element.
    input.autocomplete = 'off';

    let rows = [];
    let active = -1;
    let runNumber = 0;     // A newer keystroke cancels an older read.
    let liveTimer = null;

    function announce(message){
      clearTimeout(liveTimer);
      liveTimer = setTimeout(() => { live.textContent = message; }, 500);
    }

    function setActive(next){
      if(rows[active]){
        rows[active].classList.remove('is-active');
        rows[active].setAttribute('aria-selected', 'false');
      }
      active = next;
      if(rows[active]){
        rows[active].classList.add('is-active');
        rows[active].setAttribute('aria-selected', 'true');
        rows[active].scrollIntoView({block: 'nearest'});
        input.setAttribute('aria-activedescendant', rows[active].id);
      }else{
        input.removeAttribute('aria-activedescendant');
      }
    }

    function close(){
      runNumber++;
      clearTimeout(liveTimer);
      setActive(-1);
      rows = [];
      panel.hidden = true;
      panel.textContent = '';
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-controls');
    }

    function footLink(text, href){
      const foot = el('div', 'search-suggest-foot');
      const link = el('a', '', text);
      link.href = href;
      foot.appendChild(link);
      return foot;
    }

    function showMessage(title, message){
      const box = el('div', 'search-suggest-message');
      box.append(el('p', 'search-suggest-message-title', title), el('p', '', message));
      panel.append(box, footLink('Browse Services offered', 'home.html#services'));
      panel.hidden = false;
      announce(title);
    }

    function row(entry, query){
      const link = el('a', 'search-row');
      link.href = entry.url;
      link.id = `${listId}Row${rows.length}`;
      link.tabIndex = -1;       // Arrow keys move through the rows, Tab skips them.
      link.setAttribute('role', 'option');
      link.setAttribute('aria-selected', 'false');
      link.append(el('span', 'search-row-title', entry.title), el('span', 'search-row-meta', describe(entry, query, false)));
      rows.push(link);
      return link;
    }

    function showResults(query, found){
      const picked = pickSuggestions(found);
      const list = el('div', 'search-suggest-list');
      list.id = listId;
      list.setAttribute('role', 'listbox');
      list.setAttribute('aria-label', 'Search suggestions');

      GROUPS.forEach(({key, title}) => {
        if(!picked[key].length) return;
        const group = el('div', 'search-suggest-group');
        const heading = el('p', 'search-suggest-heading', title);
        heading.id = `${listId}${title}`;
        group.setAttribute('role', 'group');
        group.setAttribute('aria-labelledby', heading.id);
        group.appendChild(heading);
        picked[key].forEach(entry => group.appendChild(row(entry, query)));
        list.appendChild(group);
      });

      const seeAll = found.total === 1 ? `See 1 result for “${query}”` : `See all ${found.total} results for “${query}”`;
      panel.append(list, footLink(seeAll, 'search.html?q=' + encodeURIComponent(query)));
      panel.hidden = false;
      input.setAttribute('aria-controls', listId);
      input.setAttribute('aria-expanded', 'true');
      announce(`${plural(found.total, 'result')}. Use the up and down arrows to choose one, or press Enter to see all.`);
    }

    async function update(){
      const query = input.value.trim();
      if(query.length < MIN_CHARS){
        close();
        live.textContent = '';
        return;
      }
      const run = ++runNumber;
      const ready = await loadIndex();
      if(run !== runNumber) return;

      setActive(-1);
      rows = [];
      panel.textContent = '';
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-controls');
      if(!ready){
        // The field stays usable and tries again later.
        showMessage('Search isn’t available right now.',
          'Please try again later. You can still browse Services offered and Articles.');
        return;
      }
      const found = find(query);
      if(found.total) showResults(query, found);
      else showMessage(`No matches for “${query}”.`,
        'Try a shorter or different word, like “contract” or “complaint”, or browse Services offered.');
    }

    input.addEventListener('focus', () => {
      loadIndex();
      if(panel.hidden) update();
    });
    input.addEventListener('input', update);

    input.addEventListener('keydown', e => {
      if(e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      e.preventDefault();
      if(panel.hidden){
        update();
        return;
      }
      if(!rows.length) return;
      // -1 means "back in the field", so Enter opens the full results again.
      if(e.key === 'ArrowDown') setActive(active === rows.length - 1 ? -1 : active + 1);
      else setActive(active === -1 ? rows.length - 1 : active - 1);
    });

    // Esc closes the list and keeps the words (a search field clears itself on
    // Esc unless told not to).
    form.addEventListener('keydown', e => {
      if(e.key !== 'Escape') return;
      e.preventDefault();
      const wasInList = panel.contains(document.activeElement);
      close();
      if(wasInList) input.focus();
    });

    // Enter: the chosen row, or else the full results page.
    form.addEventListener('submit', e => {
      e.preventDefault();
      if(rows[active]){
        rows[active].click();
        return;
      }
      const query = input.value.trim();
      if(!query){
        input.focus();
        return;
      }
      window.location.href = 'search.html?q=' + encodeURIComponent(query);
    });

    // Pressing a row must not take focus from the field first, or the list
    // would close before the click lands (Safari doesn't focus links).
    panel.addEventListener('mousedown', e => e.preventDefault());
    panel.addEventListener('click', e => {
      if(!e.target.closest('a')) return;
      close();
      if(options.onLeave) options.onLeave();
    });

    if(options.closeOnBlur){
      form.addEventListener('focusout', e => {
        if(!form.contains(e.relatedTarget)) close();
      });
    }

    return {input, close};
  }

  // The panel under the header. It is in the page's flow, so it
  // pushes the page down instead of covering it.
  function buildHeaderPanel(){
    const button = document.getElementById('siteSearchBtn');
    const header = document.getElementById('siteHeader');
    if(!button || !header) return;

    const panel = el('div', 'site-search-panel');
    panel.id = 'site-search';
    panel.hidden = true;

    const fieldId = 'siteSearchInput';
    const form = el('form', 'search-form');
    form.setAttribute('role', 'search');
    form.action = 'search.html';
    form.method = 'get';
    const label = el('label', 'search-label', 'Search the site');
    label.htmlFor = fieldId;
    const box = el('div', 'search-box');
    const icon = el('img');
    icon.src = 'img/icons/search-field.svg';
    icon.alt = '';
    const input = el('input');
    input.type = 'search';
    input.id = fieldId;
    input.name = 'q';
    input.maxLength = 100;
    const submit = el('button', 'search-submit', 'Search');
    submit.type = 'submit';
    box.append(icon, input, submit);
    form.append(label, box);

    // "Close" on desktop, "Close search" on phones (CSS shows the second word).
    const closeBtn = el('button', 'nav-btn btn-tertiary site-search-close');
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', 'Close search');
    const closeLabel = el('span', '', 'Close');
    closeLabel.appendChild(el('span', 'site-search-close-more', ' search'));
    closeBtn.appendChild(closeLabel);

    const row = el('div', 'site-search-row');
    row.append(form, closeBtn);
    // New line, for Legal Office review.
    const hint = el('p', 'site-search-hint',
      'Press Enter to see all results. Esc closes the search and returns you to the Search button.');
    panel.append(row, hint);
    header.insertAdjacentElement('afterend', panel);

    let scrollBefore = 0;

    function setOpen(open){
      panel.hidden = !open;
      button.setAttribute('aria-expanded', String(open));
    }

    const field = attachField(form, {
      // A result was chosen: shut the panel and let the link do the moving.
      onLeave(){ setOpen(false); }
    });

    function openPanel(){
      scrollBefore = window.scrollY;
      setOpen(true);
      // The panel sits at the top of the page, under the sticky header.
      window.scrollTo(0, 0);
      field.input.focus({preventScroll: true});
    }

    function closePanel(){
      field.close();
      setOpen(false);
      window.scrollTo(0, scrollBefore);
      button.focus({preventScroll: true});
    }

    button.addEventListener('click', () => {
      if(panel.hidden) openPanel();
      else closePanel();
    });
    closeBtn.addEventListener('click', closePanel);
    panel.addEventListener('keydown', e => {
      if(e.key === 'Escape') closePanel();
    });
  }

  // Home's large field. Desktop and phone word the example differently.
  function attachPageFields(){
    document.querySelectorAll('form[data-site-search]').forEach(form => {
      const input = form.querySelector('input[type="search"]');
      if(!input.id) input.id = 'siteSearchField' + (++fieldCount);
      const phone = input.dataset.placeholderPhone;
      if(phone){
        const narrow = window.matchMedia('(max-width: 640px)');
        const setExample = () => { input.placeholder = narrow.matches ? phone : input.dataset.placeholderDesktop; };
        narrow.addEventListener('change', setExample);
        setExample();
      }
      attachField(form, {closeOnBlur: true});
    });
  }

  buildHeaderPanel();
  attachPageFields();

  return {MIN_CHARS, GROUPS, loadIndex, find, describe, plural};
})();
