// بنخزّن ملفات الشكل بس (CSS والأيقونات). بياناتك المالية ما بتنخزّن بالكاش أبدًا.
const CACHE = 'banke-static-v1';
const ASSETS = ['/static/style.css', '/static/chart.umd.js', '/static/icon.svg', '/static/icon-192.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method === 'GET' && url.origin === location.origin && url.pathname.startsWith('/static/')) {
    e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
  }
});
