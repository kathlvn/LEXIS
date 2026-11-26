// Shared header/footer for the public-facing site.
// Each page keeps a one-line placeholder:
//   <header class="site-header" id="siteHeader"></header>
//   <footer class="site-footer" id="siteFooter"></footer>
// and calls renderSiteHeader('page-key') / renderSiteFooter() after
// including this file. Update nav links or footer content here once —
// every page picks it up automatically.

// One door for submitting and following requests (My Requests).
// The old Submit Request and Track Request pages were retired;
// a request opens on request.html from My Requests.
const SITE_NAV = [
  {href:'home.html',            label:'Home',            key:'home'},
  {href:'about.html',           label:'About',           key:'about'},
  {href:'portal.html',          label:'My Requests',     key:'portal'},
  {href:'articles.html',        label:'Articles',        key:'articles'},
  {href:'home.html#contact',    label:'Contact',         key:'contact'}
];

// The ESSU logo with the office's name. The name is the text; the logo is
// decoration, so its alt is empty.
function brandHtml(){
  return `
    <span class="seal"><img src="img/essu-logo.png" alt=""></span>
    <span class="brand-text">
      <span class="title">The Legal Office</span>
      <span class="subtitle">Eastern Samar State University</span>
    </span>`;
}

function renderSiteHeader(activeKey){
  const target = document.getElementById('siteHeader');
  if(!target) return;

  // Skip link: the first thing a keyboard user reaches on every public page.
  // tabindex -1 lets the link move focus to <main> when pressed.
  target.insertAdjacentHTML('beforebegin', '<a class="skip-link" href="#main">Skip to main content</a>');
  const main = document.querySelector('main');
  if(main){
    main.id = 'main';
    main.tabIndex = -1;
  }

  const current = key => (key === activeKey ? ' aria-current="page"' : '');
  // Desktop: My Requests is the button after the links. Phone: the logo is the
  // way Home, and My Requests is a normal link. CSS shows one version of each.
  const navHtml = SITE_NAV.map(item => {
    const only = item.key === 'home' ? ' site-nav-desktop' : item.key === 'portal' ? ' site-nav-phone' : '';
    return `<a class="site-nav-link${only}" href="${item.href}"${current(item.key)}>${item.label}</a>`;
  }).join('\n        ');

  target.innerHTML = `
    <a class="brand" href="home.html">${brandHtml()}
    </a>
    <div class="site-header-right">
      <nav class="site-nav" aria-label="Main">
        ${navHtml}
      </nav>
      <!-- js/site-search.js makes this open the panel under the header. -->
      <button class="site-search-btn" id="siteSearchBtn" type="button" aria-expanded="false" aria-controls="site-search">
        <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><circle cx="9.17" cy="9.17" r="5.83"/><path d="M16.67 16.67l-3.63-3.63"/></svg>
        <span>Search</span>
      </button>
      <a class="nav-btn btn-secondary site-nav-button" href="portal.html"${current('portal')}>My Requests</a>
    </div>
  `;
}

function renderSiteFooter(){
  const target = document.getElementById('siteFooter');
  if(!target) return;
  const legalLink = (href, label) => {
    const here = location.pathname.endsWith('/' + href);
    return `<a href="${href}"${here ? ' aria-current="page"' : ''}>${label}</a>`;
  };
  target.innerHTML = `
    <div class="footer-inner">
      <div class="brand brand-on-dark">${brandHtml()}
      </div>
      <nav class="footer-links" aria-label="Footer">
        ${SITE_NAV.map(item => `<a href="${item.href}">${item.label}</a>`).join('\n        ')}
      </nav>
      <div class="footer-contact">
        <a href="mailto:legaloffice@essu.edu.ph">legaloffice@essu.edu.ph</a>
        <span>Borongan City, Eastern Samar, Philippines 6800</span>
      </div>
    </div>
    <div class="footer-bottom">
      <nav class="footer-legal" aria-label="Legal">
        ${legalLink('privacy.html', 'Privacy Notice')}
        ${legalLink('terms.html', 'Terms of Use')}
        ${legalLink('cookies.html', 'Cookies')}
      </nav>
      <p class="footer-copyright">&copy; ${new Date().getFullYear()} Eastern Samar State University — The Legal Office</p>
    </div>
  `;
}