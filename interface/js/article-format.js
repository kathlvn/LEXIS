// Article text -> HTML. Used by the public article page
// and by the Preview on the staff Articles page, so both look the same.
//
// Safety: EVERYTHING is escaped first (& < > " '), so no HTML a staff member
// types ever reaches the page. Only then are our own few marks turned into
// tags we write ourselves:
//   blank line        -> new paragraph (a single line break stays a line break)
//   "## " at a line's start -> heading
//   "- " at a line's start  -> list item
//   **words**         -> bold
//   https://... or http://... -> link (new tab, rel="noopener noreferrer nofollow")
// Links must start with http:// or https://, so "javascript:" and similar
// can never become a link.
(function () {
  function escapeText(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // A web address ends at a space, at one of our own tags (<), or at an
  // escaped < > " ' from the text. Punctuation at the very end (a full stop
  // after the address, a closing bracket) is left outside the link.
  const URL_RE = /https?:\/\/(?:(?!&lt;|&gt;|&quot;|&#39;)[^\s<])+/g;

  function inline(escaped) {
    return escaped
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(URL_RE, match => {
        const tail = match.match(/[.,;:!?)\]]+$/);
        const url = tail ? match.slice(0, -tail[0].length) : match;
        if (!/^https?:\/\/[^/]/.test(url)) return match;
        return `<a href="${url}" target="_blank" rel="noopener noreferrer nofollow">${url}</a>${tail ? tail[0] : ''}`;
      });
  }

  function formatArticle(text) {
    const lines = String(text == null ? '' : text).replace(/\r\n?/g, '\n').split('\n');
    const out = [];
    let para = [];
    let list = [];
    const flushPara = () => { if (para.length) out.push(`<p>${para.join('<br>')}</p>`); para = []; };
    const flushList = () => { if (list.length) out.push(`<ul>${list.map(li => `<li>${li}</li>`).join('')}</ul>`); list = []; };

    lines.forEach(raw => {
      const line = raw.trim();
      if (!line) { flushPara(); flushList(); return; }
      if (/^##\s+/.test(line)) {
        flushPara(); flushList();
        out.push(`<h2>${inline(escapeText(line.replace(/^##\s+/, '')))}</h2>`);
      } else if (/^-\s+/.test(line)) {
        flushPara();
        list.push(inline(escapeText(line.replace(/^-\s+/, ''))));
      } else {
        flushList();
        para.push(inline(escapeText(line)));
      }
    });
    flushPara(); flushList();
    return out.join('\n');
  }

  // Dates in Philippine time, e.g. "October 31, 2026". The server sends UTC
  // without a trailing "Z".
  function articleDate(iso) {
    if (!iso) return '';
    const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z');
    if (isNaN(d)) return '';
    return d.toLocaleDateString('en-US', {month: 'long', day: 'numeric', year: 'numeric', timeZone: 'Asia/Manila'});
  }

  // One card in an article list (Home and articles.html). Built with
  // textContent, so titles and summaries are never read as HTML.
  function articleCard(a) {
    const card = document.createElement('article');
    card.className = 'article-card';
    const title = document.createElement('a');
    title.className = 'article-card-title';
    title.href = 'article.html?id=' + encodeURIComponent(a.id);
    title.textContent = a.title;
    // A real heading, so screen reader users can jump from card to card.
    const heading = document.createElement('h3');
    heading.className = 'article-card-heading';
    heading.appendChild(title);
    const date = document.createElement('div');
    date.className = 'article-card-date';
    date.textContent = articleDate(a.published_at);
    const excerpt = document.createElement('p');
    excerpt.className = 'article-card-excerpt';
    excerpt.textContent = a.excerpt || '';
    card.append(heading, date, excerpt);
    return card;
  }

  // Small helper for the public article pages. textContent, never innerHTML.
  function articleEl(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Grey shapes that hold the place of one card while the list loads.
  function articleCardSkeleton() {
    const card = articleEl('div', 'skeleton-card');
    ['card-title', 'card-date', 'card-line', 'card-short'].forEach(kind => {
      card.appendChild(articleEl('div', 'skeleton-bar skeleton-' + kind));
    });
    return card;
  }

  // Server not reached: the message, plus a button that tries again without
  // reloading the whole page. Public pages only (the icon path is theirs).
  function articleErrorBlock(title, message, onRetry) {
    const notice = articleEl('div', 'notice notice-error');
    notice.setAttribute('role', 'alert');
    const icon = articleEl('img');
    icon.src = 'img/icons/error.svg';
    icon.alt = '';
    const content = articleEl('div');
    content.append(articleEl('p', 'notice-title', title), articleEl('p', 'notice-message', message));
    notice.append(icon, content);

    const retry = articleEl('button', 'nav-btn btn-secondary', 'Try again');
    retry.type = 'button';
    retry.addEventListener('click', onRetry);

    const block = articleEl('div', 'article-error');
    block.append(notice, retry);
    return block;
  }

  window.formatArticle = formatArticle;
  window.articleDate = articleDate;
  window.articleCard = articleCard;
  window.articleEl = articleEl;
  window.articleCardSkeleton = articleCardSkeleton;
  window.articleErrorBlock = articleErrorBlock;
})();
