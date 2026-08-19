// PUNCH NEXUS - Router
// Client-side routing with history API, lazy loading, and route guards

class Router {
  constructor(options = {}) {
    this.routes = new Map(); // path -> route config
    this.currentRoute = null;
    this.previousRoute = null;
    this.basePath = options.basePath || '';
    this.notFoundHandler = options.notFoundHandler || null;
    this.beforeEachGuards = [];
    this.afterEachHooks = [];
    
    this._boundPopState = this._onPopState.bind(this);
    this._isNavigating = false;
    this._pendingNavigation = null;
    
    // Listen for browser navigation
    window.addEventListener('popstate', this._boundPopState);
    
    // Handle initial route
    this._handleInitialRoute();
  }

  addRoute(path, config) {
    const route = {
      path,
      component: config.component, // Component constructor or lazy loader
      title: config.title || '',
      meta: config.meta || {},
      guards: config.guards || [],
      children: config.children || [],
      params: this._extractParams(path)
    };
    
    this.routes.set(path, route);
    
    // Sort routes by specificity (exact matches first)
    this._sortRoutes();
    return this;
  }

  addRoutes(routes) {
    for (const [path, config] of Object.entries(routes)) {
      this.addRoute(path, config);
    }
    return this;
  }

  beforeEach(guard) {
    this.beforeEachGuards.push(guard);
  }

  afterEach(hook) {
    this.afterEachHooks.push(hook);
  }

  navigate(path, options = {}) {
    if (this._isNavigating) {
      return Promise.reject(new Error('Navigation already in progress'));
    }

    const fullPath = this.basePath + path;
    const currentPath = window.location.pathname + window.location.search;

    // Don't navigate if same path
    if (currentPath === fullPath && !options.replace) {
      return Promise.resolve(false);
    }

    return new Promise((resolve, reject) => {
      this._isNavigating = true;
      this._pendingNavigation = { resolve, reject, path: fullPath, options };

      // Run beforeEach guards
      this._runGuards(fullPath)
        .then(canProceed => {
          if (!canProceed) {
            this._isNavigating = false;
            return resolve(false);
          }

          // Update URL
          if (options.replace) {
            history.replaceState({ path: fullPath }, '', fullPath);
          } else {
            history.pushState({ path: fullPath }, '', fullPath);
          }

          // Handle navigation
          this._handleRouteChange(fullPath)
            .then(success => {
              this._isNavigating = false;
              this._pendingNavigation = null;
              resolve(success);
            })
            .catch(error => {
              this._isNavigating = false;
              this._pendingNavigation = null;
              reject(error);
            });
        })
        .catch(error => {
          this._isNavigating = false;
          this._pendingNavigation = null;
          reject(error);
        });
    });
  }

  back() {
    history.back();
  }

  forward() {
    history.forward();
  }

  go(delta) {
    history.go(delta);
  }

  getCurrentRoute() {
    return this.currentRoute;
  }

  getPreviousRoute() {
    return this.previousRoute;
  }

  // Navigation guards
  addBeforeEach(guard) {
    this.beforeEachGuards.push(guard);
  }

  addAfterEach(hook) {
    this.afterEachHooks.push(hook);
  }

  // Internal methods
  _extractParams(path) {
    const paramRegex = /:([^/]+)/g;
    const params = [];
    let match;
    while ((match = paramRegex.exec(path)) !== null) {
      params.push(match[1]);
    }
    return params;
  }

  _matchRoute(path) {
    for (const [routePath, route] of this.routes) {
      const regex = this._pathToRegex(routePath);
      const match = path.match(regex);
      if (match) {
        const params = {};
        route.params.forEach((param, index) => {
          params[param] = match[index + 1];
        });
        return { route, params, match };
      }
    }
    return null;
  }

  _pathToRegex(path) {
    const regexPath = path
      .replace(/\//g, '\\/')
      .replace(/:([^/]+)/g, '([^/]+)')
      .replace(/\*/g, '.*');
    return new RegExp(`^${regexPath}$`);
  }

  _sortRoutes() {
    // Convert to array, sort by specificity, rebuild map
    const routesArray = Array.from(this.routes.entries());
    routesArray.sort((a, b) => {
      // Exact paths first
      const aExact = !a[0].includes(':') && !a[0].includes('*');
      const bExact = !b[0].includes(':') && !b[0].includes('*');
      if (aExact !== bExact) return aExact ? -1 : 1;
      // Longer paths (more specific) first
      return b[0].length - a[0].length;
    });
    this.routes = new Map(routesArray);
  }

  _handleInitialRoute() {
    const path = window.location.pathname + window.location.search;
    this._handleRouteChange(window.location.pathname + window.location.search);
  }

  _onPopState(event) {
    const path = window.location.pathname + window.location.search;
    if (this._isNavigating) return; // Ignore if we triggered it
    this._handleRouteChange(path);
  }

  async _runGuards(path) {
    // Global beforeEach guards
    for (const guard of this.beforeEachGuards) {
      const result = await guard(path, this.currentRoute);
      if (result === false) return false;
      if (typeof result === 'string') {
        // Redirect
        this.navigate(result, { replace: true });
        return false;
      }
    }

    // Route-specific guards
    const match = this._matchRoute(path);
    if (match && match.route.guards) {
      for (const guard of match.route.guards) {
        const result = await guard(path, this.currentRoute, match.params);
        if (result === false) return false;
        if (typeof result === 'string') {
          this.navigate(result, { replace: true });
          return false;
        }
      }
    }
    return true;
  }

  async _handleRouteChange(path) {
    const match = this._matchRoute(path);
    
    if (!match) {
      if (this.notFoundHandler) {
        await this.notFoundHandler(path);
      }
      return false;
    }

    const { route, params } = match;
    
    // Update route state
    this.previousRoute = this.currentRoute;
    this.currentRoute = {
      path,
      name: route.path,
      params,
      meta: route.meta,
      component: route.component
    };

    // Run afterEach hooks
    for (const hook of this.afterEachHooks) {
      try {
        await hook(this.currentRoute, this.previousRoute);
      } catch (error) {
        console.error('afterEach hook error:', error);
      }
    }

    // Emit route change event
    window.dispatchEvent(new CustomEvent('routeChange', {
      detail: { route: this.currentRoute, previous: this.previousRoute }
    }));

    return true;
  }

  getCurrentPath() {
    return window.location.pathname + window.location.search;
  }

  getParams() {
    return this.currentRoute?.params || {};
  }

  getQuery() {
    return new URLSearchParams(window.location.search);
  }

  // Programmatic navigation helpers
  push(path) {
    return this.navigate(path);
  }

  replace(path) {
    return this.navigate(path, { replace: true });
  }

  // Route matching for components
  match(path) {
    return this._matchRoute(path);
  }

  // Cleanup
  destroy() {
    window.removeEventListener('popstate', this._boundPopState);
    this.routes.clear();
    this.beforeEachGuards = [];
    this.afterEachHooks = [];
  }
}

// Route builder helper
function defineRoutes(routesConfig) {
  const router = new Router();
  router.addRoutes(routesConfig);
  return router;
}

// Export for modules
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { Router, defineRoutes };
} else {
  window.Router = Router;
  window.defineRoutes = defineRoutes;
}