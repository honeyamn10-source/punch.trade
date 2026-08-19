// PUNCH NEXUS - State Store
// Centralized state management with reactivity, persistence, and devtools

class Store {
  constructor(initialState = {}) {
    this._state = { ...initialState };
    this._listeners = new Map(); // key -> Set of callbacks
    this._subscriptions = new Map(); // componentId -> Map of keys -> callbacks
    this._middleware = [];
    this._history = [];
    this._historyIndex = -1;
    this._maxHistory = 100;
    this._isBatch = false;
    this._pendingChanges = new Map();
    
    // Persistence
    this._persistenceKey = 'nexus-state';
    this._persistEnabled = true;
    this._persistDebounce = null;
    
    // Load persisted state
    this._loadPersistedState();
  }

  // Get state value
  get(key) {
    const keys = key.split('.');
    let value = this._state;
    for (const k of keys) {
      if (value === undefined || value === null) return undefined;
      value = value[k];
    }
    return value;
  }

  // Set state value
  set(key, value) {
    const keys = key.split('.');
    const lastKey = keys.pop();
    
    let target = this._state;
    for (const k of keys) {
      if (!(k in target) || typeof target[k] !== 'object') {
        target[k] = {};
      }
      target = target[k];
    }
    
    const oldValue = target[lastKey];
    if (this._isEqual(oldValue, value)) return false;
    
    target[lastKey] = value;
    
    if (!this._isBatch) {
      this._notify(key, value, oldValue);
      this._saveToHistory(key, oldValue);
      this._schedulePersist();
    } else {
      this._pendingChanges.set(key, { oldValue, newValue: value });
    }
    
    return true;
  }

  // Update multiple keys at once
  update(updates) {
    this._isBatch = true;
    try {
      for (const [key, value] of Object.entries(updates)) {
        this.set(key, value);
      }
    } finally {
      this._isBatch = false;
      // Flush pending changes
      for (const [key, { oldValue, newValue }] of this._pendingChanges) {
        this._notify(key, newValue, oldValue);
        this._saveToHistory(key, oldValue);
      }
      this._pendingChanges.clear();
      this._schedulePersist();
    }
  }

  // Delete a key
  delete(key) {
    const keys = key.split('.');
    const lastKey = keys.pop();
    
    let target = this._state;
    for (const k of keys) {
      if (!(k in target) || typeof target[k] !== 'object') return false;
      target = target[k];
    }
    
    if (!(lastKey in target)) return false;
    
    const oldValue = target[lastKey];
    delete target[lastKey];
    
    if (!this._isBatch) {
      this._notify(key, undefined, oldValue);
      this._saveToHistory(key, oldValue);
      this._schedulePersist();
    }
    
    return true;
  }

  // Subscribe to changes
  subscribe(key, callback, componentId = null) {
    if (!this._listeners.has(key)) {
      this._listeners.set(key, new Set());
    }
    this._listeners.get(key).add(callback);
    
    // Track subscription for component cleanup
    if (componentId) {
      if (!this._subscriptions.has(componentId)) {
        this._subscriptions.set(componentId, new Map());
      }
      const compSubs = this._subscriptions.get(componentId);
      if (!compSubs.has(key)) {
        compSubs.set(key, new Set());
      }
      compSubs.get(key).add(callback);
    }
    
    // Return unsubscribe function
    return () => this.unsubscribe(key, callback);
  }

  unsubscribe(key, callback) {
    if (this._listeners.has(key)) {
      this._listeners.get(key).delete(callback);
    }
  }

  unsubscribeAll(componentId) {
    if (this._subscriptions.has(componentId)) {
      const compSubs = this._subscriptions.get(componentId);
      for (const [key, callbacks] of compSubs) {
        for (const callback of callbacks) {
          this.unsubscribe(key, callback);
        }
      }
      this._subscriptions.delete(componentId);
    }
  }

  // Select multiple keys at once
  select(keys) {
    const result = {};
    for (const key of keys) {
      result[key] = this.get(key);
    }
    return result;
  }

  // Computed/derived state
  compute(key, fn, deps = []) {
    const computeValue = () => fn(this.select(deps));
    let value = computeValue();
    
    const unsubscribers = deps.map(dep => 
      this.subscribe(dep, () => {
        const newValue = computeValue();
        if (!this._isEqual(this._state[key], newValue)) {
          this.set(key, newValue);
        }
      })
    );
    
    return {
      get value() { return this.get(key); },
      unsubscribe: () => unsubscribers.forEach(u => u())
    };
  }

  // Middleware
  use(middleware) {
    this._middleware.push(middleware);
  }

  // Undo/Redo
  undo() {
    if (this._historyIndex <= 0) return false;
    this._historyIndex--;
    const entry = this._history[this._historyIndex];
    this._state = JSON.parse(JSON.stringify(entry.state));
    this._notifyAll();
    return true;
  }

  redo() {
    if (this._historyIndex >= this._history.length - 1) return false;
    this._historyIndex++;
    const entry = this._history[this._historyIndex];
    this._state = JSON.parse(JSON.stringify(entry.state));
    this._notifyAll();
    return true;
  }

  canUndo() {
    return this._historyIndex > 0;
  }

  canRedo() {
    return this._historyIndex < this._history.length - 1;
  }

  // Persistence
  enablePersistence(key = 'nexus-state') {
    this._persistenceKey = key;
    this._persistEnabled = true;
    this._loadPersistedState();
  }

  disablePersistence() {
    this._persistEnabled = false;
  }

  clearPersistedState() {
    try {
      localStorage.removeItem(this._persistenceKey);
    } catch (e) {
      console.warn('Failed to clear persisted state:', e);
    }
  }

  // DevTools integration
  getState() {
    return { ...this._state };
  }

  setState(state) {
    this._state = { ...state };
    this._notifyAll();
    this._schedulePersist();
  }

  reset() {
    this._state = {};
    this._history = [];
    this._historyIndex = -1;
    this._notifyAll();
    this.clearPersistedState();
  }

  // Internal methods
  _notify(key, newValue, oldValue) {
    if (this._listeners.has(key)) {
      for (const callback of this._listeners.get(key)) {
        try {
          callback(newValue, oldValue, key);
        } catch (error) {
          console.error(`Store listener error for ${key}:`, error);
        }
      }
    }
    
    // Also notify parent keys
    const parts = key.split('.');
    for (let i = parts.length - 1; i > 0; i--) {
      const parentKey = parts.slice(0, i).join('.');
      if (this._listeners.has(parentKey)) {
        const parentValue = this.get(parentKey);
        for (const callback of this._listeners.get(parentKey)) {
          try {
            callback(parentValue, oldValue, parentKey);
          } catch (error) {
            console.error(`Store listener error for ${parentKey}:`, error);
          }
        }
      }
    }
  }

  _notifyAll() {
    for (const [key, callbacks] of this._listeners) {
      const value = this.get(key);
      for (const callback of callbacks) {
        try {
          callback(value, value, key);
        } catch (error) {
          console.error(`Store listener error for ${key}:`, error);
        }
      }
    }
  }

  _saveToHistory(key, oldValue) {
    // Only save to history for significant changes
    if (!this._isBatch) {
      const stateCopy = JSON.parse(JSON.stringify(this._state));
      this._history = this._history.slice(0, this._historyIndex + 1);
      this._history.push({ state: stateCopy, timestamp: Date.now() });
      if (this._history.length > this._maxHistory) {
        this._history.shift();
      } else {
        this._historyIndex++;
      }
    }
  }

  _isEqual(a, b) {
    if (a === b) return true;
    if (a === null || b === null) return false;
    if (typeof a !== 'object' || typeof b !== 'object') return false;
    return JSON.stringify(a) === JSON.stringify(b);
  }

  _schedulePersist() {
    if (!this._persistEnabled) return;
    
    if (this._persistDebounce) {
      clearTimeout(this._persistDebounce);
    }
    
    this._persistDebounce = setTimeout(() => {
      this._persistState();
    }, 500);
  }

  _persistState() {
    if (!this._persistEnabled) return;
    
    try {
      const data = JSON.stringify({
        state: this._state,
        timestamp: Date.now(),
        version: 1
      });
      localStorage.setItem(this._persistenceKey, data);
    } catch (error) {
      console.warn('Failed to persist state:', error);
    }
  }

  _loadPersistedState() {
    if (!this._persistEnabled) return;
    
    try {
      const data = localStorage.getItem(this._persistenceKey);
      if (data) {
        const parsed = JSON.parse(data);
        if (parsed.state && typeof parsed.state === 'object') {
          this._state = parsed.state;
        }
      }
    } catch (error) {
      console.warn('Failed to load persisted state:', error);
    }
  }
}

// Reactive proxy for easier usage
function reactive(initialState = {}) {
  const store = new Store(initialState);
  
  return new Proxy(store, {
    get(target, prop) {
      if (prop in target) {
        return target[prop];
      }
      return target.get(prop);
    },
    set(target, prop, value) {
      target.set(prop, value);
      return true;
    }
  });
}

// Create global store instance
const store = new Store({
  // System state
  system: {
    mode: 'RESEARCH',
    armed: false,
    token: null,
    connected: false,
    lastUpdate: null
  },
  // Market data
  market: {
    quotes: {},
    candles: {},
    providers: {},
    watchlist: [],
    regime: 'UNKNOWN'
  },
  // Signals & trading
  signals: {
    live: [],
    history: [],
    filters: {}
  },
  positions: {
    open: [],
    closed: []
  },
  orders: {
    pending: [],
    history: []
  },
  // Research
  research: {
    strategies: [],
    trials: [],
    currentStrategy: null,
    currentReport: null
  },
  portfolio: {
    allocation: {},
    metrics: {},
    risk: {}
  },
  risk: {
    dailyPnL: 0,
    openRisk: 0,
    drawdown: 0,
    limits: {}
  },
  // AI
  ai: {
    status: 'disconnected',
    model: null,
    context: null,
    history: []
  },
  // UI state
  ui: {
    activeTab: 'signals',
    sidebarOpen: true,
    contextPanelOpen: true,
    theme: 'dark',
    notifications: []
  },
  // WebSocket
  ws: {
    connected: false,
    latency: 0,
    lastMessage: null
  }
});

// Export for modules
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { Store, store, reactive };
} else {
  window.Store = Store;
  window.store = store;
  window.reactive = reactive;
}