// PUNCH NEXUS - API Client
// Centralized API communication with error handling, retries, and request/response interceptors

class ApiClient {
  constructor(options = {}) {
    this.baseUrl = options.baseUrl || '';
    this.defaultHeaders = {
      'Content-Type': 'application/json',
      ...options.headers
    };
    this.token = options.token || null;
    this.timeout = options.timeout || 30000;
    this.retryConfig = {
      maxRetries: options.maxRetries || 3,
      retryDelay: options.retryDelay || 1000,
      retryStatuses: options.retryStatuses || [408, 429, 500, 502, 503, 504]
    };
    this.interceptors = {
      request: [],
      response: [],
      error: []
    };
  }

  setToken(token) {
    this.token = token;
  }

  clearToken() {
    this.token = null;
  }

  // Interceptor management
  addRequestInterceptor(fn) {
    this.interceptors.request.push(fn);
  }

  addResponseInterceptor(fn) {
    this.interceptors.response.push(fn);
  }

  addErrorInterceptor(fn) {
    this.interceptors.error.push(fn);
  }

  // Build request config
  async _buildRequestConfig(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint}`;
    const headers = { ...this.defaultHeaders, ...options.headers };

    if (this.token) {
      headers['X-Punch-Token'] = this.token;
    }

    // Add request ID for tracing (secure-context fallback included)
    headers['X-Request-Id'] = options.requestId || this._uuid();

    let config = {
      method: options.method || 'GET',
      headers,
      signal: options.signal,
      ...options
    };

    if (options.body && !(options.body instanceof FormData)) {
      config.body = JSON.stringify(options.body);
    } else if (options.body) {
      config.body = options.body;
      // Remove Content-Type for FormData - browser sets it with boundary
      delete headers['Content-Type'];
    }

    // Run request interceptors
    for (const interceptor of this.interceptors.request) {
      config = await interceptor(config) || config;
    }

    return { url, config };
  }

  _uuid() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    // Non-secure-context fallback (e.g. plain-HTTP LAN hosts)
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
      const r = Math.random() * 16 | 0;
      const v = c === 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  }

  // Execute request with retry logic
  async _executeWithRetry(url, config, attempt = 0) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeout);
    
    const finalConfig = {
      ...config,
      signal: config.signal || controller.signal
    };

    try {
      const response = await fetch(url, finalConfig);
      clearTimeout(timeoutId);
      return response;
    } catch (error) {
      clearTimeout(timeoutId);
      
      // Check if we should retry
      if (attempt < this.retryConfig.maxRetries && this._shouldRetry(error, config)) {
        await this._sleep(this.retryConfig.retryDelay * Math.pow(2, attempt));
        return this._executeWithRetry(url, config, attempt + 1);
      }
      throw error;
    }
  }

  _shouldRetry(error, config) {
    // Retry on network errors or specific status codes — but ONLY for
    // idempotent methods. Re-sending a POST order placement could double-fill.
    const method = (config.method || 'GET').toUpperCase();
    const isIdempotent = ['GET', 'HEAD', 'PUT', 'DELETE'].includes(method);
    const hasIdempotencyKey = !!(config.headers && config.headers['X-Idempotency-Key']);
    if (!isIdempotent && !hasIdempotencyKey) return false;

    if (error instanceof TypeError && error.message.includes('fetch')) {
      return true; // Network error
    }
    if (error.status && this.retryConfig.retryStatuses.includes(error.status)) {
      return true;
    }
    return false;
  }

  _sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  // Process response through interceptors
  async _processResponse(response) {
    let result = response;
    
    for (const interceptor of this.interceptors.response) {
      result = await interceptor(result) || result;
    }
    
    return result;
  }

  // Main request method
  async request(endpoint, options = {}) {
    const { url, config } = await this._buildRequestConfig(endpoint, options);
    config.url = url;
    
    try {
      const response = await this._executeWithRetry(config.url, config);
      
      // Handle error responses
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        const error = new ApiError(
          response.status,
          errorData.detail || errorData.message || response.statusText,
          errorData.code,
          errorData.requestId
        );
        error.response = response;
        error.data = errorData;
        
        // Run error interceptors
        for (const interceptor of this.interceptors.error) {
          try {
            await interceptor(error);
          } catch (e) {
            console.error('Error interceptor failed:', e);
          }
        }
        
        throw error;
      }

      // Parse response
      const contentType = response.headers.get('Content-Type');
      let data;
      if (contentType && contentType.includes('application/json')) {
        data = await response.json();
      } else {
        data = await response.text();
      }

      return await this._processResponse(data);
    } catch (error) {
      if (error instanceof ApiError) throw error;
      
      // Wrap network/other errors
      const apiError = new ApiError(
        0,
        error.message || 'Network error',
        'NETWORK_ERROR',
        null
      );
      apiError.originalError = error;
      throw apiError;
    }
  }

  // Convenience methods
  async get(endpoint, params = {}, options = {}) {
    const query = new URLSearchParams(params).toString();
    const url = query ? `${endpoint}?${query}` : endpoint;
    return this.request(url, { ...options, method: 'GET' });
  }

  async post(endpoint, body, options = {}) {
    return this.request(endpoint, { ...options, method: 'POST', body });
  }

  async put(endpoint, body, options = {}) {
    return this.request(endpoint, { ...options, method: 'PUT', body });
  }

  async patch(endpoint, body, options = {}) {
    return this.request(endpoint, { ...options, method: 'PATCH', body });
  }

  async delete(endpoint, options = {}) {
    return this.request(endpoint, { ...options, method: 'DELETE' });
  }

  // WebSocket helper
  createWebSocket(endpoint, protocols = []) {
    const url = `${this.baseUrl.replace(/^http/, 'ws')}${endpoint}`;
    const ws = new WebSocket(url, protocols);
    return ws;
  }
}

// Custom error class
class ApiError extends Error {
  constructor(status, message, code, requestId) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.timestamp = new Date().toISOString();
  }

  toJSON() {
    return {
      name: this.name,
      message: this.message,
      status: this.status,
      code: this.code,
      requestId: this.requestId,
      timestamp: this.timestamp
    };
  }
}

// Create default instance
const api = new ApiClient({
  baseUrl: '/api'
});

// Add default interceptors
api.addRequestInterceptor(async (config) => {
  // Add timestamp for cache busting on GET
  if (config.method === 'GET' && config.url && !config.url.includes('?')) {
    config.url += `?_t=${Date.now()}`;
  }
  return config;
});

api.addResponseInterceptor(async (data) => {
  // Handle paginated responses
  if (data && typeof data === 'object' && 'data' in data && Array.isArray(data.data)) {
    return data;
  }
  return data;
});

api.addErrorInterceptor(async (error) => {
  // Log errors to console in development
  if (typeof process !== 'undefined' && process.env && process.env.NODE_ENV === 'development') {
    console.error('[API Error]', error.toJSON());
  }
  
  // Handle auth errors
  if (error.status === 401) {
    // Token expired - could trigger refresh or redirect
    console.warn('Authentication expired');
  }
});

// Export for modules
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { ApiClient, ApiError, api };
} else {
  window.ApiClient = ApiClient;
  window.ApiError = ApiError;
  window.api = api;
}