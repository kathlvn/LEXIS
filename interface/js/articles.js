// Public list of articles:
// every published article, newest first (GET /public/articles). The server
// sends the whole list at once (at most 100), so "Show more articles" only
// reveals cards the page already has. Everything is built with textContent
// (js/article-format.js), so nothing a staff member typed is read as HTML.
(function(){
  const FIRST_SHOWN = 7;   // The latest article + two rows of three.
  const SHOW_MORE = 6;     // Two more rows at a time.

  const area = document.getElementById('articleListArea');
  const status = document.getElementById('articleListStatus');
  const count = document.getElementById('articleCount');

  function arrow(){
    const img = articleEl('img');
    img.src = 'img/icons/arrow-right-green.svg';
    img.alt = '';
    return img;
  }

  // The newest article. The title is the real link; CSS stretches its click
  // area over the whole card, so the card is one link with a short name.
  function latestCard(a){
    const card = articleEl('article', 'article-latest');

    const panel = articleEl('div', 'article-latest-date');
    panel.append(articleEl('p', 'article-latest-label', 'Latest'),
      articleEl('p', 'article-latest-day', articleDate(a.published_at)));

    const title = articleEl('a', 'article-latest-title', a.title);
    title.href = 'article.html?id=' + encodeURIComponent(a.id);
    const heading = articleEl('h2');
    heading.appendChild(title);

    // Looks like a link but is part of the card's link, so it is not read twice.
    const read = articleEl('span', 'text-link', 'Read the article');
    read.setAttribute('aria-hidden', 'true');
    read.appendChild(arrow());

    const text = articleEl('div', 'article-latest-text');
    text.append(heading, articleEl('p', 'article-latest-excerpt', a.excerpt || ''), read);

    card.append(panel, text);
    return card;
  }

  function showLoading(){
    area.textContent = '';
    count.hidden = false;
    count.textContent = '';   // Keeps its height, so the hero does not jump.

    const shapes = articleEl('div', 'articles-skeleton');
    const latest = articleEl('div', 'skeleton-latest');
    ['latest-label', 'latest-title', 'latest-line', 'latest-short'].forEach(kind => {
      latest.appendChild(articleEl('div', 'skeleton-bar skeleton-' + kind));
    });
    const cards = articleEl('div', 'article-cards');
    for(let i = 0; i < 3; i++) cards.appendChild(articleCardSkeleton());
    shapes.append(latest, cards);
    area.appendChild(shapes);

    status.textContent = '';
    status.appendChild(articleEl('p', 'articles-loading-text', 'Loading articles…'));
  }

  function showEmpty(moveFocus){
    area.textContent = '';
    status.textContent = '';
    count.textContent = '';   // Keeps its height, so the list does not jump up (PERF-03).

    const home = articleEl('a', 'text-link', 'Back to Home');
    home.href = 'home.html';
    home.appendChild(arrow());

    // "Announcements from the Legal Office…" is new, for Legal Office review.
    const box = articleEl('div', 'article-empty');
    box.append(articleEl('h2', '', 'No articles have been posted yet.'),
      articleEl('p', '', 'Announcements from the Legal Office will appear here.'),
      home);
    status.appendChild(box);
    if(moveFocus) home.focus();
  }

  function showError(moveFocus){
    area.textContent = '';
    status.textContent = '';
    count.textContent = '';   // Keeps its height (PERF-03).

    const block = articleErrorBlock('The articles could not be loaded right now.',
      'Please try again later.', () => load(true));
    area.appendChild(block);
    if(moveFocus) block.querySelector('button').focus();
  }

  function showList(rows, moveFocus){
    area.textContent = '';
    status.textContent = '';
    count.hidden = false;
    count.textContent = rows.length === 1 ? '1 article' : `${rows.length} articles, newest first`;

    const latest = latestCard(rows[0]);
    area.appendChild(latest);
    if(moveFocus) latest.querySelector('a').focus();
    if(rows.length === 1) return;

    area.appendChild(articleEl('h2', 'articles-more-title', 'More articles'));
    const cards = articleEl('div', 'article-cards');
    area.appendChild(cards);

    let shown = Math.min(rows.length, FIRST_SHOWN);
    rows.slice(1, shown).forEach(a => cards.appendChild(articleCard(a)));
    if(shown === rows.length) return;

    const showing = articleEl('p', 'articles-showing');
    showing.setAttribute('role', 'status');
    const more = articleEl('button', 'nav-btn btn-secondary', 'Show more articles');
    more.type = 'button';
    const write = () => { showing.textContent = `Showing ${shown} of ${rows.length} articles`; };
    write();

    more.addEventListener('click', () => {
      const next = rows.slice(shown, shown + SHOW_MORE).map(articleCard);
      next.forEach(card => cards.appendChild(card));
      shown += next.length;
      write();
      if(shown === rows.length) more.remove();
      // Keyboard users carry on from the first new card.
      next[0].querySelector('a').focus();
    });

    const row = articleEl('div', 'articles-more-row');
    row.append(showing, more);
    area.appendChild(row);
  }

  // moveFocus: only after "Try again", whose button has just disappeared.
  async function load(moveFocus){
    showLoading();
    let rows;
    try{
      const res = await fetch(`${API_BASE_URL}/public/articles`);
      if(!res.ok) throw new Error(String(res.status));
      rows = await res.json();
      if(!Array.isArray(rows)) throw new Error('Unexpected answer');
    }catch(err){
      showError(moveFocus);
      return;
    }
    if(rows.length) showList(rows, moveFocus);
    else showEmpty(moveFocus);
  }

  load(false);
})();
