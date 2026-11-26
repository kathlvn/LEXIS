// The "On this page" lists on the Privacy Notice, Terms of Use and Cookies pages.
// Built from the section headings, so a list can't
// disagree with its page. A heading can give a shorter label with data-toc.
(function(){
  const sections = Array.from(document.querySelectorAll('.legal-section[id]'));
  const rail = document.getElementById('legalToc');
  const phone = document.getElementById('legalTocPhone');
  const aside = document.querySelector('.legal-aside');

  // html { scroll-padding-top: 128px } in base.css, plus a little: a section
  // opened from the list lands at 128px and must count as the current one.
  const READING_LINE = 136;

  function fillList(list){
    sections.forEach(section => {
      const heading = section.querySelector('h2');
      const link = document.createElement('a');
      link.href = '#' + section.id;
      link.textContent = heading.dataset.toc || heading.textContent;
      const item = document.createElement('li');
      item.appendChild(link);
      list.appendChild(item);
    });
  }

  fillList(rail.querySelector('.legal-toc-list'));
  fillList(phone.querySelector('.legal-toc-list'));
  phone.querySelector('.legal-toc-phone-count').textContent = `${sections.length} sections`;
  rail.hidden = false;
  phone.hidden = false;

  // "Also read" sits in the side column on a computer and under the text on a
  // phone. CSS shows one copy, so the keyboard order always matches the screen.
  document.querySelector('.legal-back').before(aside.querySelector('.legal-also').cloneNode(true));

  const links = Array.from(document.querySelectorAll('.legal-toc-list a'));

  // A short screen scrolls the side column: keep the marked link inside it.
  function keepInView(link){
    const box = aside.getBoundingClientRect();
    const spot = link.getBoundingClientRect();
    if(!box.height) return;   // Hidden on phones.
    if(spot.top < box.top) aside.scrollTop -= box.top - spot.top;
    else if(spot.bottom > box.bottom) aside.scrollTop += spot.bottom - box.bottom;
  }

  function setCurrent(id){
    links.forEach(link => {
      if(link.getAttribute('href') === '#' + id){
        if(!link.hasAttribute('aria-current')) link.setAttribute('aria-current', 'true');
        keepInView(link);
      }else{
        link.removeAttribute('aria-current');
      }
    });
  }

  function currentFromScroll(){
    // The last sections can be too short to reach the top of a tall screen.
    const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
    if(atBottom) return sections[sections.length - 1].id;
    let id = sections[0].id;
    sections.forEach(section => {
      if(section.getBoundingClientRect().top <= READING_LINE) id = section.id;
    });
    return id;
  }

  // The jump after a click on the list must not replace what was clicked: near
  // the end of a page, the section may be unable to reach the reading line.
  let justClicked = false;
  links.forEach(link => {
    link.addEventListener('click', () => {
      setCurrent(link.getAttribute('href').slice(1));
      justClicked = true;
      phone.open = false;   // Closing it first, so the jump lands in the right place.
    });
  });

  let queued = false;
  function update(){
    if(queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      setCurrent(currentFromScroll());
    });
  }

  window.addEventListener('scroll', () => {
    if(justClicked){
      justClicked = false;
      return;
    }
    update();
  }, {passive: true});
  window.addEventListener('resize', update);

  setCurrent(currentFromScroll());
})();
