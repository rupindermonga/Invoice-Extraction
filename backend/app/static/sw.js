// Finel AI Project Finance — Service Worker v3
// Strategy: cache-first for all static assets, network-first for API with offline queue

const CACHE = 'finel-pf-v3';
const OFFLINE_QUEUE_DB = 'finel-offline-queue';

// All static assets to pre-cache on install
const PRECACHE = [
  '/',
  '/static/js/app-2.js',
  '/static/js/alpine.min.js',
  '/static/js/tailwind.min.js',
  '/static/js/flatpickr.min.js',
  '/static/css/flatpickr.min.css',
  '/static/css/fontawesome/all.min.css',
  '/static/favicon.svg',
  '/static/manifest.json',
];

// ── Install: pre-cache all static assets ──────────────────────────────────────
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(PRECACHE.map(url => new Request(url, { cache: 'reload' }))))
      .then(() => self.skipWaiting())
      .catch(err => console.warn('[SW] Pre-cache partial failure:', err))
  );
});

// ── Activate: purge old caches ────────────────────────────────────────────────
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// ── Fetch: smart routing ───────────────────────────────────────────────────────
self.addEventListener('fetch', e => {
  const req = e.request;
  const url = new URL(req.url);

  // Skip non-GET for API — let them through, queue if offline
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/owner/') || url.pathname.startsWith('/bid/')) {
    if (req.method === 'GET') {
      // Network-first for API reads
      e.respondWith(
        fetch(req).catch(() =>
          new Response(JSON.stringify({ error: 'Offline — no cached data available', offline: true }), {
            status: 503, headers: { 'Content-Type': 'application/json' }
          })
        )
      );
    } else {
      // POST/PUT/DELETE: try network, queue if offline
      e.respondWith(
        fetch(req.clone()).catch(async () => {
          // Queue the request for background sync
          const body = await req.clone().text().catch(() => '{}');
          await _enqueue({
            url: req.url, method: req.method,
            headers: Object.fromEntries(req.headers.entries()),
            body, timestamp: Date.now(),
          });
          return new Response(JSON.stringify({ ok: true, queued: true, offline: true }), {
            status: 202, headers: { 'Content-Type': 'application/json' }
          });
        })
      );
    }
    return;
  }

  // Static assets: stale-while-revalidate
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(req).then(cached => {
        const networkFetch = fetch(req).then(res => {
          if (res.ok) caches.open(CACHE).then(c => c.put(req, res.clone()));
          return res;
        }).catch(() => cached); // fallback to cache if network fails
        return cached || networkFetch;
      })
    );
    return;
  }

  // HTML pages: network-first, fall back to cached /
  e.respondWith(
    fetch(req).catch(() =>
      caches.match(req).then(cached => cached || caches.match('/'))
    )
  );
});

// ── Background Sync: replay queued mutations ───────────────────────────────────
self.addEventListener('sync', e => {
  if (e.tag === 'finel-sync-queue') {
    e.waitUntil(_replayQueue());
  }
});

// ── Periodic Sync (if supported) ───────────────────────────────────────────────
self.addEventListener('periodicsync', e => {
  if (e.tag === 'finel-periodic-sync') {
    e.waitUntil(_replayQueue());
  }
});

// ── Push Notifications ─────────────────────────────────────────────────────────
self.addEventListener('push', e => {
  const data = e.data ? e.data.json() : { title: 'Finel AI', body: 'New notification' };
  e.waitUntil(
    self.registration.showNotification(data.title || 'Finel AI Projects', {
      body: data.body || '',
      icon: '/static/favicon.svg',
      badge: '/static/favicon.svg',
      tag: data.tag || 'finel-notification',
      data: data,
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: 'window' }).then(cls => {
      if (cls.length) return cls[0].focus();
      return clients.openWindow('/');
    })
  );
});

// ── IndexedDB Queue Helpers ────────────────────────────────────────────────────

function _openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(OFFLINE_QUEUE_DB, 1);
    req.onupgradeneeded = e => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains('requests')) {
        db.createObjectStore('requests', { keyPath: 'id', autoIncrement: true });
      }
    };
    req.onsuccess = e => resolve(e.target.result);
    req.onerror = e => reject(e.target.error);
  });
}

async function _enqueue(entry) {
  try {
    const db = await _openDB();
    const tx = db.transaction('requests', 'readwrite');
    tx.objectStore('requests').add(entry);
    await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = rej; });
    // Register background sync
    if (self.registration.sync) {
      await self.registration.sync.register('finel-sync-queue');
    }
  } catch (err) {
    console.warn('[SW] Queue write failed:', err);
  }
}

async function _replayQueue() {
  const db = await _openDB();
  const tx = db.transaction('requests', 'readwrite');
  const store = tx.objectStore('requests');
  const all = await new Promise((res, rej) => {
    const req = store.getAll();
    req.onsuccess = () => res(req.result);
    req.onerror = rej;
  });

  for (const entry of all) {
    try {
      const headers = new Headers(entry.headers || {});
      const res = await fetch(entry.url, {
        method: entry.method,
        headers,
        body: entry.method !== 'GET' ? entry.body : undefined,
      });
      if (res.ok) {
        // Remove from queue
        const delTx = db.transaction('requests', 'readwrite');
        delTx.objectStore('requests').delete(entry.id);
      }
    } catch (err) {
      console.warn('[SW] Replay failed for:', entry.url, err);
    }
  }

  // Notify open clients that sync completed
  const cls = await self.clients.matchAll();
  cls.forEach(c => c.postMessage({ type: 'SYNC_COMPLETE', replayed: all.length }));
}

// ── Message from client ────────────────────────────────────────────────────────
self.addEventListener('message', e => {
  if (e.data && e.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  if (e.data && e.data.type === 'GET_QUEUE_COUNT') {
    _openDB().then(db => {
      const tx = db.transaction('requests', 'readonly');
      const req = tx.objectStore('requests').count();
      req.onsuccess = () => e.source.postMessage({ type: 'QUEUE_COUNT', count: req.result });
    }).catch(() => e.source.postMessage({ type: 'QUEUE_COUNT', count: 0 }));
  }
});
