// One published article:
// article.html?id=N (GET /public/articles/{id}). The title is set with
// textContent; the body goes through formatArticle(), which escapes everything
// before adding its own few tags (js/article-format.js).
(function(){
  const main = document.querySelector('main');
  const state = document.getElementById('articleState');
  const error = document.getElementById('articleError');
  const view = document.getElementById('articleView');
  const title = document.getElementById('articleTitle');
  const id = new URLSearchParams(location.search).get('id') || '';

  function clearStates(){
    state.textContent = '';
    error.textContent = '';
  }

  function showLoading(){
    clearStates();
    const shapes = articleEl('div', 'article-skeleton');
    ['heading', 'byline', 'line', 'line', 'line-long', 'line', 'line-short'].forEach(kind => {
      shapes.appendChild(articleEl('div', 'skeleton-bar skeleton-article-' + kind));
    });
    shapes.appendChild(articleEl('p', 'articles-loading-text', 'Loading the article…'));
    state.appendChild(shapes);
  }

  // The same answer for a hidden, draft, missing or mistyped article.
  function showNotAvailable(moveFocus){
    clearStates();
    const heading = articleEl('h1', '', 'This article is not available.');
    heading.tabIndex = -1;
    const all = articleEl('a', 'text-link', 'See all articles');
    all.href = 'articles.html';
    const arrow = articleEl('img');
    arrow.src = 'img/icons/arrow-right-green.svg';
    arrow.alt = '';
    all.appendChild(arrow);

    const box = articleEl('div', 'article-unavailable');
    box.append(heading, articleEl('p', '', 'It may have been taken down.'), all);
    state.appendChild(box);
    if(moveFocus) heading.focus();
  }

  function showError(moveFocus){
    clearStates();
    const block = articleErrorBlock('The article could not be loaded right now.',
      'Please try again later.', () => load(true));
    error.appendChild(block);
    if(moveFocus) block.querySelector('button').focus();
  }

  function showArticle(a, moveFocus){
    clearStates();
    title.textContent = a.title;
    document.getElementById('articleDate').textContent = articleDate(a.published_at);
    document.getElementById('articleBody').innerHTML = formatArticle(a.body);
    document.title = `${a.title} — The Legal Office, Eastern Samar State University`;
    main.classList.remove('article-bare');
    view.hidden = false;
    if(moveFocus) title.focus();
  }

  // The 3 newest articles other than this one. It is an extra, so if there are
  // none or the list can't be loaded, the section simply stays away.
  async function loadMore(){
    let rows = [];
    try{
      const res = await fetch(`${API_BASE_URL}/public/articles?limit=4`);
      if(res.ok) rows = await res.json();
    }catch(err){
      return;
    }
    if(!Array.isArray(rows)) return;
    const others = rows.filter(a => String(a.id) !== id).slice(0, 3);
    if(!others.length) return;
    const cards = document.getElementById('articleMoreCards');
    others.forEach(a => cards.appendChild(articleCard(a)));
    document.getElementById('articleMore').hidden = false;
  }

  // moveFocus: only after "Try again", whose button has just disappeared.
  async function load(moveFocus){
    if(!/^\d{1,9}$/.test(id)){
      showNotAvailable(moveFocus);
      return;
    }
    showLoading();
    try{
      const res = await fetch(`${API_BASE_URL}/public/articles/${id}`);
      if(res.status === 404){
        showNotAvailable(moveFocus);
        return;
      }
      if(!res.ok) throw new Error(String(res.status));
      showArticle(await res.json(), moveFocus);
    }catch(err){
      showError(moveFocus);
      return;
    }
    loadMore();
  }

  load(false);
})();
