/* Service Worker — Marca Atractora Academy */
const CACHE = 'academia-v1';

const PRECACHE = [
  '/static/tailwind.js',
  '/static/manifest.json',
];

/* Instalar: precachear estáticos */
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

/* Activar: borrar cachés viejas */
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

/* Fetch: estrategia por tipo de recurso */
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Avatares e imágenes de BD → cache-first (ya tienen Cache-Control del servidor)
  if (url.pathname.startsWith('/avatar/') ||
      url.pathname.startsWith('/curso/') ||
      url.pathname.startsWith('/leccion-imagen/') ||
      url.pathname === '/comunidad/banner') {
    e.respondWith(
      caches.open(CACHE).then(async cache => {
        const cached = await cache.match(e.request);
        if (cached) return cached;
        const resp = await fetch(e.request);
        if (resp.ok) cache.put(e.request, resp.clone());
        return resp;
      })
    );
    return;
  }

  // Archivos estáticos → cache-first
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.open(CACHE).then(async cache => {
        const cached = await cache.match(e.request);
        if (cached) return cached;
        const resp = await fetch(e.request);
        if (resp.ok) cache.put(e.request, resp.clone());
        return resp;
      })
    );
    return;
  }

  // Páginas HTML → network-first (contenido siempre fresco)
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).catch(() => caches.match('/'))
    );
    return;
  }

  // Resto → network
  e.respondWith(fetch(e.request));
});
