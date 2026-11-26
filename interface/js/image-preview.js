// Picture previews in the request chat. A JPG or PNG sent
// with a message is shown in its bubble, so nobody has to download it just
// to see it; its Download button stays under the picture.
//
// Used by the client's request page (js/request.js) and the staff Request
// Detail (internal/js/request-detail.js). Each page passes its own fetch
// function, which goes through the same access-checked download endpoint as
// its Download button: an uploaded file still has no public URL. The picture
// is shown from a blob: address that only this page can read (the Caddyfile
// CSP allows img-src blob: for this).
//
// Pictures load only when they come near the screen (saves the client's
// mobile data and stays under the download rate limit), once each per page.
// The frame has a fixed height, so the thread doesn't jump while one loads.
// If a picture can't be loaded or isn't really a JPG/PNG, only its card stays.

const ChatImagePreview = (() => {
  const PREVIEW_TYPES = ['image/jpeg', 'image/png'];
  const loaded = new Map();   // document id -> Promise of a blob: URL (or null)

  function isImage(mimeType){
    return PREVIEW_TYPES.includes(String(mimeType || '').toLowerCase());
  }

  /** The empty frame put in a bubble; watch() fills it. The name must
      already be escaped (it goes into an attribute). */
  function frameHtml(className, documentId, escapedName){
    return `<span class="${className}" data-preview-doc="${Number(documentId)}" data-preview-name="${escapedName}"></span>`;
  }

  function urlFor(id, fetchBlob){
    if(!loaded.has(id)){
      loaded.set(id, Promise.resolve()
        .then(() => fetchBlob(id))
        // The server sets the type from the file name; anything else is not shown.
        .then(blob => (blob && isImage(blob.type)) ? URL.createObjectURL(blob) : null)
        .catch(() => null)
        .then(url => {
          if(!url) loaded.delete(id);   // a later visit may try again (e.g. after a rate limit)
          return url;
        }));
    }
    return loaded.get(id);
  }

  async function fill(frame, fetchBlob){
    const url = await urlFor(frame.dataset.previewDoc, fetchBlob);
    if(!url){ frame.remove(); return; }
    const img = document.createElement('img');
    img.alt = `Picture: ${frame.dataset.previewName}`;   // set as a property, so no escaping is needed
    img.decoding = 'async';
    img.addEventListener('load', () => frame.classList.add('is-loaded'));
    img.addEventListener('error', () => frame.remove());
    img.src = url;
    frame.appendChild(img);
  }

  /** Starts loading the empty frames under root as they come near the screen. */
  function watch(root, fetchBlob){
    const frames = [...root.querySelectorAll('[data-preview-doc]:not([data-preview-watched])')];
    if(!frames.length) return;
    frames.forEach(f => { f.dataset.previewWatched = '1'; });
    if(!('IntersectionObserver' in window)){
      frames.forEach(f => fill(f, fetchBlob));
      return;
    }
    const observer = new IntersectionObserver((entries, obs) => {
      entries.forEach(entry => {
        if(!entry.isIntersecting) return;
        obs.unobserve(entry.target);
        fill(entry.target, fetchBlob);
      });
    }, {rootMargin: '200px 0px'});
    frames.forEach(f => observer.observe(f));
  }

  return {isImage, frameHtml, watch};
})();
