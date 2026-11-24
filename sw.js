/* CJ Metas — service worker (v9.5)
   Guarda la app en el dispositivo para que abra sin internet.
   NO toca tus datos: esos viven en el almacenamiento del navegador (localStorage). */
const CACHE = 'cj-metas-v9.5';
const CORE = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png', './icon-maskable-512.png', './apple-touch-icon.png'];
// Recursos externos que la app usa: tipografías y la librería de exportar a Excel
const EXTERNAL = [
  'https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Sora:wght@600;700&display=swap',
  'https://cdn.jsdelivr.net/npm/xlsx-js-style@1.2.0/dist/xlsx.bundle.js'
];

self.addEventListener('install', e => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    await c.addAll(CORE);
    // Los externos se intentan guardar, pero si fallan no impiden la instalación
    await Promise.all(EXTERNAL.map(u => fetch(u, { mode: 'no-cors' }).then(r => c.put(u, r)).catch(() => {})));
    self.skipWaiting();
  })());
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => (k.startsWith('cj-metas-') || k.startsWith('afi-pro-cj-')) && k !== CACHE).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  const sameOrigin = url.origin === self.location.origin;
  // Solo tipografías y la librería de Excel. Las llamadas a Google Drive e inicio de sesión NUNCA se guardan en caché.
  const isExternalAsset = ['fonts.googleapis.com', 'fonts.gstatic.com', 'cdn.jsdelivr.net'].includes(url.hostname);
  if (!sameOrigin && !isExternalAsset) return;
  // v9.0: la tasa del dólar siempre va directo a internet (nunca de la copia guardada)
  if (url.pathname.includes('currency-api')) return;
  // Los datos de fondos (fondos.json) siempre van directo a internet: nunca se sirven desde la copia guardada
  if (sameOrigin && /fondos\.json$/.test(url.pathname)) return;

  // La página: primero internet (para recibir versiones nuevas); sin conexión, la copia guardada
  if (req.mode === 'navigate' || (sameOrigin && /\/(index\.html)?$/.test(url.pathname))) {
    e.respondWith((async () => {
      const c = await caches.open(CACHE);
      try {
        const res = await fetch(req, { cache: 'no-store' });
        if (res && res.ok) c.put('./index.html', res.clone());
        return res;
      } catch (err) {
        return (await c.match('./index.html')) || (await c.match('./')) || Response.error();
      }
    })());
    return;
  }

  // Lo demás (íconos, tipografías, librería de Excel): copia guardada primero, y se refresca por detrás
  e.respondWith((async () => {
    const c = await caches.open(CACHE);
    const hit = await c.match(req, { ignoreVary: true });
    const net = fetch(req).then(res => {
      if (res && (res.ok || res.type === 'opaque')) c.put(req, res.clone());
      return res;
    }).catch(() => null);
    return hit || (await net) || Response.error();
  })());
});
