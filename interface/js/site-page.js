// Site header and footer for the public pages. Each page names its menu
// item on the script tag, e.g. <script src="js/site-page.js" data-nav="home">
// (empty = none highlighted). A file, not an inline <script>, because the
// Content-Security-Policy forbids inline scripts.
renderSiteHeader(document.currentScript.dataset.nav || '');
renderSiteFooter();
