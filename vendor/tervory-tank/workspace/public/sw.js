/* Public static app shell only. Authenticated HTML, API and client data are network-only. */
const CACHE = 'tervory-shell-v1';
const PUBLIC = ['/manifest.webmanifest', '/icons/tervory-180.png', '/icons/tervory-192.png', '/icons/tervory-512.png'];
function shellAsset(url) {
  return url.origin === self.location.origin && !url.search &&
    (PUBLIC.includes(url.pathname) || /^\/_next\/static\/[A-Za-z0-9_./%-]+\.(?:js|css|woff2?)$/.test(url.pathname));
}
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(PUBLIC)));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('tervory-shell-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET' || request.mode === 'navigate' || !shellAsset(new URL(request.url))) return;
  event.respondWith(caches.open(CACHE).then(async cache => {
    const saved = await cache.match(request);
    if (saved) return saved;
    const response = await fetch(request);
    if (response.ok && !response.redirected && response.type === 'basic' &&
        !/private|no-store/i.test(response.headers.get('Cache-Control') || '')) await cache.put(request, response.clone());
    return response;
  }));
});
