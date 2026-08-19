// PUNCH NEXUS - WebSocket Manager
// Handles WebSocket connections with auto-reconnect, message routing, and state management

class WebSocketManager {
  constructor(options = {}) {
    this.url = options.url || '';
    this.protocols = options.protocols || [];
    this.reconnectInterval = options.reconnectInterval || 3000;
    this.maxReconnectAttempts = options.maxReconnectAttempts || 10;
    this.heartbeatInterval = options.heartbeatInterval || 30000;
    this.messageQueue = [];
    
    this.ws = null;
    this.state = 'disconnected'; // disconnected, connecting, connected, reconnecting, failed
    this.reconnectAttempts = 0;
    this.heartbeatTimer = null;
    this.messageId = 0;
    this.pendingRequests = new Map(); // messageId -> { resolve, reject, timeout }
    this._pendingConnect = null; // { resolve, reject, timeoutId }
    this._reconnectTimer = null;
    this._intentionalClose = false;
    
    // Event handlers
    this.handlers = new Map(); // eventType -> Set of handlers
    this.onOpenCallbacks = new Set();
    this.onCloseCallbacks = new Set();
    this.onErrorCallbacks = new Set();
    this.onMessageCallbacks = new Set();
    
    // Bind methods
    this._onOpen = this._onOpen.bind(this);
    this._onClose = this._onClose.bind(this);
    this._onError = this._onError.bind(this);
    this._onMessage = this._onMessage.bind(this);
  }

  connect(url = null) {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return Promise.resolve();
    }

    if (url) this.url = url;

    if (!this.url) {
      return Promise.reject(new Error('WebSocket URL not configured'));
    }

    this._setState('connecting');
    this._intentionalClose = false;
    
    return new Promise((resolve, reject) => {
      try {
        this.ws = new WebSocket(this.url);
        this.ws.binaryType = 'arraybuffer';
        
        this.ws.onopen = this._onOpen;
        this.ws.onclose = this._onClose;
        this.ws.onerror = this._onError;
        this.ws.onmessage = this._onMessage;
        
        // Timeout for connection
        const connectTimeout = setTimeout(() => {
          if (this.ws && this.ws.readyState !== WebSocket.OPEN) {
            this.ws.close();
            reject(new Error('Connection timeout'));
            this._pendingConnect = null;
          }
        }, 10000);

        this._pendingConnect = { resolve, reject, timeoutId: connectTimeout };
      } catch (error) {
        this._setState('failed');
        reject(error);
      }
    });
  }

  disconnect() {
    this._intentionalClose = true;
    this._clearHeartbeat();
    this._clearReconnectTimer();
    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }
    this._setState('disconnected');
    this.reconnectAttempts = 0;
    this._pendingConnect = null;
  }

  send(data) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      // Queue message for when connected
      this.messageQueue.push(data);
      return Promise.reject(new Error('WebSocket not connected'));
    }

    const message = typeof data === 'string' ? data : JSON.stringify(data);
    this.ws.send(message);
    return Promise.resolve();
  }

  // Request-response pattern
  async request(payload, timeout = 10000) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error('WebSocket not connected');
    }

    const messageId = ++this.messageId;
    const message = {
      id: messageId,
      ...payload,
      timestamp: Date.now()
    };

    return new Promise((resolve, reject) => {
      // Set up timeout
      const timeoutId = setTimeout(() => {
        this.pendingRequests.delete(messageId);
        reject(new Error('Request timeout'));
      }, timeout);

      this.pendingRequests.set(messageId, { resolve, reject, timeoutId });

      try {
        this.ws.send(JSON.stringify(message));
      } catch (error) {
        this.pendingRequests.delete(messageId);
        clearTimeout(timeoutId);
        reject(error);
      }
    });
  }

  subscribe(channel, handler) {
    return this.send({
      type: 'subscribe',
      channel,
      timestamp: Date.now()
    }).then(() => {
      this.on(`channel:${channel}`, handler);
    });
  }

  unsubscribe(channel) {
    this.off(`channel:${channel}`);
    return this.send({
      type: 'unsubscribe',
      channel,
      timestamp: Date.now()
    });
  }

  // Event handler management
  on(eventType, handler) {
    if (!this.handlers.has(eventType)) {
      this.handlers.set(eventType, new Set());
    }
    this.handlers.get(eventType).add(handler);
    
    // Return unsubscribe function
    return () => this.off(eventType, handler);
  }

  off(eventType, handler) {
    if (this.handlers.has(eventType)) {
      this.handlers.get(eventType).delete(handler);
    }
  }

  emit(eventType, data) {
    if (this.handlers.has(eventType)) {
      for (const handler of this.handlers.get(eventType)) {
        try {
          handler(data);
        } catch (error) {
          console.error(`Error in ${eventType} handler:`, error);
        }
      }
    }
    
    // Also emit to global message handlers
    for (const handler of this.onMessageCallbacks) {
      try {
        handler(eventType, data);
      } catch (error) {
        console.error('Error in global message handler:', error);
      }
    }
  }

  onOpen(handler) {
    this.onOpenCallbacks.add(handler);
    return () => this.onOpenCallbacks.delete(handler);
  }

  onClose(handler) {
    this.onCloseCallbacks.add(handler);
    return () => this.onCloseCallbacks.delete(handler);
  }

  onError(handler) {
    this.onErrorCallbacks.add(handler);
    return () => this.onErrorCallbacks.delete(handler);
  }

  onMessage(handler) {
    this.onMessageCallbacks.add(handler);
    return () => this.onMessageCallbacks.delete(handler);
  }

  // Internal event handlers
  _onOpen(event) {
    console.log('[WS] Connected');
    this._setState('connected');
    this.reconnectAttempts = 0;
    this._startHeartbeat();
    
    // Resolve the pending connect() promise
    if (this._pendingConnect) {
      clearTimeout(this._pendingConnect.timeoutId);
      this._pendingConnect.resolve();
      this._pendingConnect = null;
    }
    
    // Send queued messages
    while (this.messageQueue.length > 0) {
      const msg = this.messageQueue.shift();
      this.send(msg).catch(console.error);
    }

    for (const cb of this.onOpenCallbacks) {
      try { cb(event); } catch (e) { console.error('onOpen callback error:', e); }
    }
    this.emit('open', event);
  }

  _onClose(event) {
    console.log('[WS] Disconnected:', event.code, event.reason);
    this._clearHeartbeat();
    
    // Reject pending connect() promise (if it hasn't timed out already)
    if (this._pendingConnect) {
      clearTimeout(this._pendingConnect.timeoutId);
      this._pendingConnect.reject(new Error(`WebSocket closed (${event.code})`));
      this._pendingConnect = null;
    }

    const wasConnected = this.state === 'connected';
    this._setState('disconnected');
    
    // Reject pending requests
    for (const [id, { reject }] of this.pendingRequests) {
      reject(new Error('WebSocket closed'));
    }
    this.pendingRequests.clear();

    for (const cb of this.onCloseCallbacks) {
      try { cb(event); } catch (e) { console.error('onClose callback error:', e); }
    }
    this.emit('close', event);

    // Auto-reconnect unless intentionally closed or attempts exhausted
    if (!this._intentionalClose && this.reconnectAttempts < this.maxReconnectAttempts) {
      this._scheduleReconnect();
    } else if (!this._intentionalClose) {
      this._setState('failed');
    }
  }

  _onError(event) {
    console.error('[WS] Error:', event);
    for (const cb of this.onErrorCallbacks) {
      try { cb(event); } catch (e) { console.error('onError callback error:', e); }
    }
    this.emit('error', event);
  }

  _onMessage(event) {
    try {
      let data;
      if (event.data instanceof ArrayBuffer) {
        // Handle binary data if needed
        const decoder = new TextDecoder();
        data = JSON.parse(decoder.decode(event.data));
      } else {
        data = JSON.parse(event.data);
      }

      // Handle request responses
      if (data.id && this.pendingRequests.has(data.id)) {
        const { resolve, reject, timeoutId } = this.pendingRequests.get(data.id);
        clearTimeout(timeoutId);
        this.pendingRequests.delete(data.id);
        
        if (data.error) {
          reject(new Error(data.error.message || 'Request failed'));
        } else {
          resolve(data);
        }
        return;
      }

      // Handle channel messages
      if (data.channel) {
        this.emit(`channel:${data.channel}`, data.payload);
        return;
      }

      // Handle server events ({type, data} and {type, payload} shapes)
      if (data.type) {
        this.emit(data.type, data.data !== undefined ? data.data : (data.payload || data));
        return;
      }

      // Emit raw message for global handlers
      this.emit('message', data);
    } catch (error) {
      console.error('[WS] Message parse error:', error);
    }
  }

  _setState(state) {
    const oldState = this.state;
    this.state = state;
    if (oldState !== state) {
      this.emit('stateChange', { old: oldState, current: state });
    }
  }

  _startHeartbeat() {
    this._clearHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping', timestamp: Date.now() }));
      } else {
        this._clearHeartbeat();
      }
    }, this.heartbeatInterval);
  }

  _clearHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  _scheduleReconnect() {
    this._clearReconnectTimer();
    this._setState('reconnecting');
    this.reconnectAttempts++;
    
    const delay = Math.min(
      this.reconnectInterval * Math.pow(1.5, this.reconnectAttempts - 1),
      30000
    );
    
    console.log(`[WS] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
    
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null;
      if (this.state === 'reconnecting') {
        this.connect().catch(console.error);
      }
    }, delay);
  }

  _clearReconnectTimer() {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
  }

  // Status getters
  isConnected() {
    return this.state === 'connected';
  }

  getState() {
    return this.state;
  }

  getReconnectAttempts() {
    return this.reconnectAttempts;
  }

  // Cleanup
  destroy() {
    this.disconnect();
    this.handlers.clear();
    this.onOpenCallbacks.clear();
    this.onCloseCallbacks.clear();
    this.onErrorCallbacks.clear();
    this.onMessageCallbacks.clear();
  }
}

// Export for modules
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { WebSocketManager };
} else {
  window.WebSocketManager = WebSocketManager;
}