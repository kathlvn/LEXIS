// The full results page, search.html?q=... The
// searching itself is in js/site-search.js. Everything is built with
// textContent, so the words from the address bar are never read as HTML.
(function(){
  const MAX_LENGTH = 100;   // The same limit as the field.
  const area = document.getElementById('searchResults');
  const count = document.getElementById('searchCount');
  const query = (new URLSearchParams(window.location.search).get('q') || '').trim().slice(0, MAX_LENGTH);

  document.getElementById('searchPageInput').value = query;

  function el(tag, className, text){
    const node = document.createElement(tag);
    if(className) node.className = className;
    if(text !== undefined) node.textContent = text;
    return node;
  }

  // No results, search unavailable, or nothing typed yet.
  function showMessage(title, message){
    const arrow = el('img');
    arrow.src = 'img/icons/arrow-right-green.svg';
    arrow.alt = '';
    const link = el('a', 'text-link', 'Browse Services offered');
    link.href = 'home.html#services';
    link.appendChild(arrow);

    const box = el('div', 'search-message');
    box.append(el('h2', '', title), el('p', '', message), link);
    area.appendChild(box);
  }

  function resultCard(entry){
    const link = el('a', 'search-result-title', entry.title);
    link.href = entry.url;
    const card = el('li', 'search-result');
    card.append(link, el('p', 'search-result-meta', SiteSearch.describe(entry, query, true)));
    return card;
  }

  function showResults(found){
    SiteSearch.GROUPS.forEach(({key, title}) => {
      const rows = found[key];
      if(!rows.length) return;
      const list = el('ul', 'search-result-list');
      rows.forEach(entry => list.appendChild(resultCard(entry)));
      const group = el('section', 'search-group');
      group.append(el('h2', '', `${title} · ${SiteSearch.plural(rows.length, 'result')}`), list);
      area.appendChild(group);
    });
  }

  async function run(){
    if(query.length < SiteSearch.MIN_CHARS){
      // New wording, for Legal Office review.
      showMessage(query ? 'Type at least 2 characters.' : 'Type a word to search.',
        'You can search services, articles and pages, like “contract” or “complaint”.');
      return;
    }

    document.title = `“${query}” — Search results — The Legal Office, ESSU`;
    count.textContent = 'Searching…';
    const ready = await SiteSearch.loadIndex();
    if(!ready){
      count.textContent = '';
      showMessage('Search isn’t available right now.',
        'Please try again later. You can still browse Services offered and Articles.');
      return;
    }

    const found = SiteSearch.find(query);
    count.textContent = `${SiteSearch.plural(found.total, 'result')} for “${query}”`;
    if(found.total) showResults(found);
    else showMessage(`No matches for “${query}”.`,
      'Try a shorter or different word, like “contract” or “complaint”, or browse Services offered.');
  }

  run();
})();
