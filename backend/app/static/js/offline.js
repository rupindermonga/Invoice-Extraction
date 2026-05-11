// Finel AI Offline Manager — IndexedDB + Service Worker integration

const FinelOffline = {
  isOnline: navigator.onLine,
  queueCount: 0,
  _listeners: [],

  init() {
    // Register service worker
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js', { scope: '/' })
        .then(reg => {
          console.log('[Offline] SW registered, scope:', reg.scope);
          // Listen for sync complete messages
          navigator.serviceWorker.addEventListener('message', e => {
            if (e.data.type === 'SYNC_COMPLETE') {
              this._notifyListeners('sync_complete', e.data);
              this.getQueueCount();
            }
            if (e.data.type === 'QUEUE_COUNT') {
              this.queueCount = e.data.count;
              this._notifyListeners('queue_update', { count: e.data.count });
            }
          });
          // Register periodic background sync if supported
          if (reg.periodicSync) {
            reg.periodicSync.register('finel-periodic-sync', { minInterval: 5 * 60 * 1000 })
              .catch(() => {}); // fails silently if not permitted
          }
        })
        .catch(err => console.warn('[Offline] SW registration failed:', err));
    }

    // Online/offline event listeners
    window.addEventListener('online', () => {
      this.isOnline = true;
      this._notifyListeners('online', {});
      this._triggerSync();
    });
    window.addEventListener('offline', () => {
      this.isOnline = false;
      this._notifyListeners('offline', {});
    });

    // Initial queue count
    this.getQueueCount();
  },

  getQueueCount() {
    if (navigator.serviceWorker.controller) {
      navigator.serviceWorker.controller.postMessage({ type: 'GET_QUEUE_COUNT' });
    }
  },

  async _triggerSync() {
    if ('serviceWorker' in navigator && navigator.serviceWorker.ready) {
      const reg = await navigator.serviceWorker.ready;
      if (reg.sync) {
        reg.sync.register('finel-sync-queue').catch(() => {});
      }
    }
  },

  on(event, callback) {
    this._listeners.push({ event, callback });
    return () => { this._listeners = this._listeners.filter(l => l.callback !== callback); };
  },

  _notifyListeners(event, data) {
    this._listeners.filter(l => l.event === event || l.event === '*')
      .forEach(l => l.callback(data));
  },

  // Save data to IndexedDB for offline access
  async saveForOffline(storeName, data) {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open('finel-offline-data', 1);
      req.onupgradeneeded = e => {
        const db = e.target.result;
        ['projects','tasks','daily_logs','timecards','safety_incidents'].forEach(name => {
          if (!db.objectStoreNames.contains(name)) {
            db.createObjectStore(name, { keyPath: 'id' });
          }
        });
      };
      req.onsuccess = e => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains(storeName)) { resolve(); return; }
        const tx = db.transaction(storeName, 'readwrite');
        const store = tx.objectStore(storeName);
        if (Array.isArray(data)) {
          data.forEach(item => store.put(item));
        } else {
          store.put(data);
        }
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      };
      req.onerror = () => reject(req.error);
    });
  },

  // Read from IndexedDB (for offline fallback)
  async readOffline(storeName, projectId) {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open('finel-offline-data', 1);
      req.onsuccess = e => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains(storeName)) { resolve([]); return; }
        const tx = db.transaction(storeName, 'readonly');
        const store = tx.objectStore(storeName);
        const all = store.getAll();
        all.onsuccess = () => {
          const results = projectId
            ? all.result.filter(r => r.project_id === projectId)
            : all.result;
          resolve(results);
        };
        all.onerror = () => reject(all.error);
      };
      req.onerror = () => reject(req.error);
    });
  },
};

// Auto-init when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => FinelOffline.init());
} else {
  FinelOffline.init();
}

window.FinelOffline = FinelOffline;
