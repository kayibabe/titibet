/**
 * TiTiBet Service Worker
 *
 * Strategy:
 *  - /api/*         → Network-only (never cache live data)
 *  - /health        → Network-only
 *  - Navigation     → Network-first (always fetch fresh HTML so new JS bundles load)
 *  - Static assets  → Cache-first (Vite production assets are content-hashed)
 *
 * On install: pre-cache the manifest.
 * On activate: purge old caches.
 *
 * Bump CACHE_VERSION whenever a breaking change is deployed to force cache eviction.
 */

// CACHE_VERSION is injected by the Vite swVersion plugin at build time.
// Every production build gets a unique timestamp — no manual bumping needed.
const CACHE_VERSION = '__SW_VERSION__'

// ── Dev mode self-destruct ────────────────────────────────────────────────────
// When CACHE_VERSION still contains the unprocessed placeholder it means this
// SW file was NOT processed by the Vite build (dev mode, or accidentally served
// raw). Caching anything in dev causes stale-bundle problems: old JS chunks are
// served from the SW's Cache API even though the Vite dev server sends
// Cache-Control: no-store — those headers only affect the HTTP cache, not the
// SW's own Cache API.
//
// Fix: on first install after this code lands, skipWaiting() takes over
// immediately, then activate clears every cache and unregisters this SW so all
// subsequent requests go directly to the Vite dev server. A navigation() call
// reloads all open tabs so they pick up the fresh assets.
if (CACHE_VERSION === '__SW_VERSION__') {
  self.addEventListener('install', () => {
    self.skipWaiting()
  })

  self.addEventListener('activate', event => {
    event.waitUntil(
      caches.keys()
        .then(keys => Promise.all(keys.map(k => caches.delete(k))))
        .then(() => self.registration.unregister())
        .then(() => self.clients.matchAll({ type: 'window', includeUncontrolled: true }))
        .then(clients => clients.forEach(client => client.navigate(client.url)))
    )
  })

  // No fetch handler — every request passes straight through to the network.
  // Do NOT add any caching logic here.

} else {

// ── Production SW ─────────────────────────────────────────────────────────────

const PRECACHE_URLS = ['/manifest.json']

// Install — pre-cache the app shell
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then(cache => cache.addAll(PRECACHE_URLS))
  )
  self.skipWaiting()
})

// Activate — purge every cache that belongs to an old version
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(key => key !== CACHE_VERSION)
          .map(key => caches.delete(key))
      )
    )
  )
  self.clients.claim()
})

// Fetch — route requests
self.addEventListener('fetch', event => {
  const { request } = event
  const url = new URL(request.url)

  // Network-only: API calls and health checks
  if (url.pathname.startsWith('/api/') || url.pathname === '/health') {
    event.respondWith(fetch(request))
    return
  }

  // Network-first for page navigations — ensures the browser always gets the
  // latest index.html (and therefore the latest content-hashed JS/CSS URLs).
  // Without this, the old cached HTML points at old bundle filenames and the
  // stale UI is served even after a deploy.
  if (request.mode === 'navigate') {
    event.respondWith(
      caches.open(CACHE_VERSION).then(async cache => {
        try {
          const response = await fetch(request)
          cache.put(request, response.clone())
          return response
        } catch {
          // Offline fallback
          return cache.match('/') || new Response('Offline', { status: 503, statusText: 'Service Unavailable' })
        }
      })
    )
    return
  }

  // Cache-first for static assets (JS/CSS bundles have content hashes in prod)
  event.respondWith(
    caches.open(CACHE_VERSION).then(async cache => {
      const cached = await cache.match(request)
      if (cached) return cached

      try {
        const response = await fetch(request)
        if (response.ok && (url.origin === location.origin || response.type === 'basic')) {
          cache.put(request, response.clone())
        }
        return response
      } catch {
        return new Response('Offline', { status: 503, statusText: 'Service Unavailable' })
      }
    })
  )
})

} // end production SW
