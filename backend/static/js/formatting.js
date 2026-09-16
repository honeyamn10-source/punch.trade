// PUNCH NEXUS - Formatting Utilities
// Centralized number, date, and text formatting for consistency across the app

class Formatter {
  constructor(options = {}) {
    this.locale = options.locale || 'en-US';
    this.currency = options.currency || 'USD';
    this.timezone = options.timezone || 'UTC';
    
    // Cache formatters for performance
    this._formatters = new Map();
  }

  // ============================================================
  // Number Formatting
  // ============================================================

  number(value, options = {}) {
    if (value === null || value === undefined || isNaN(value)) {
      return options.fallback || '\u2014';
    }
    
    const opts = {
      minimumFractionDigits: options.decimals ?? 2,
      maximumFractionDigits: options.decimals ?? 2,
      useGrouping: options.grouping !== false,
      ...options
    };
    
    return this._getNumberFormat(opts).format(value);
  }

  compactNumber(value, options = {}) {
    if (value === null || value === undefined || isNaN(value)) {
      return options.fallback || '\u2014';
    }
    
    const opts = {
      notation: 'compact',
      compactDisplay: options.compactDisplay || 'short',
      maximumFractionDigits: options.decimals ?? 1,
      ...options
    };
    
    return this._getNumberFormat(opts).format(value);
  }

  percent(value, options = {}) {
    if (value === null || value === undefined || isNaN(value)) {
      return options.fallback || '\u2014';
    }
    
    const opts = {
      style: 'percent',
      minimumFractionDigits: options.decimals ?? 2,
      maximumFractionDigits: options.decimals ?? 2,
      ...options
    };
    
    return this._getNumberFormat(opts).format(value);
  }

  basisPoints(value, options = {}) {
    if (value === null || value === undefined || isNaN(value)) {
      return options.fallback || '\u2014';
    }
    
    // Convert decimal to basis points (0.0001 = 1 bp)
    const bps = value * 10000;
    const sign = bps >= 0 ? '+' : '';
    return `${sign}${this.number(bps, { decimals: options.decimals ?? 0 })}bp`;
  }

  pnl(value, options = {}) {
    if (value === null || value === undefined || isNaN(value)) {
      return options.fallback || '\u2014';
    }
    
    const formatted = this.number(Math.abs(value), options);
    const sign = value >= 0 ? '+' : '\u2212';
    const color = value >= 0 ? 'positive' : 'negative';
    
    return {
      text: `${sign}${formatted}`,
      color: color,
      raw: value
    };
  }

  // ============================================================
  // Currency / Money Formatting
  // ============================================================

  money(value, options = {}) {
    if (value === null || value === undefined || isNaN(value)) {
      return options.fallback || '\u2014';
    }
    
    const opts = {
      style: 'currency',
      currency: options.currency || this.currency,
      minimumFractionDigits: options.decimals ?? 2,
      maximumFractionDigits: options.decimals ?? 2,
      currencyDisplay: options.symbol ? 'symbol' : 'code',
      ...options
    };
    
    return this._getNumberFormat(opts).format(value);
  }

  // ============================================================
  // Quantity / Position Sizing
  // ============================================================

  quantity(value, options = {}) {
    if (value === null || value === undefined || isNaN(value)) {
      return options.fallback || '\u2014';
    }
    
    const precision = options.precision ?? 4;
    return this.number(value, { decimals: precision, ...options });
  }

  // ============================================================
  // Price Formatting
  // ============================================================

  price(value, options = {}) {
    if (value === null || value === undefined || isNaN(value)) {
      return options.fallback || '\u2014';
    }
    
    // Auto-determine decimals based on price magnitude
    let decimals = options.decimals;
    if (decimals === undefined) {
      if (value >= 10000) decimals = 2;
      else if (value >= 1000) decimals = 2;
      else if (value >= 100) decimals = 2;
      else if (value >= 10) decimals = 3;
      else if (value >= 1) decimals = 4;
      else if (value >= 0.1) decimals = 5;
      else if (value >= 0.01) decimals = 6;
      else decimals = 8;
    }
    
    return this.number(value, { decimals, ...options });
  }

  // ============================================================
  // Percentage Change / Returns
  // ============================================================

  change(value, options = {}) {
    if (value === null || value === undefined || isNaN(value)) {
      return options.fallback || '\u2014';
    }
    
    const sign = value >= 0 ? '+' : '';
    const formatted = this.percent(value, { decimals: options.decimals ?? 2 });
    
    return {
      text: `${sign}${formatted}`,
      color: value >= 0 ? 'positive' : 'negative',
      raw: value
    };
  }

  // ============================================================
  // Date/Time Formatting
  // ============================================================

  date(value, options = {}) {
    if (!value) return options.fallback || '\u2014';
    
    const date = value instanceof Date ? value : new Date(value);
    if (isNaN(date.getTime())) return options.fallback || '\u2014';
    
    const opts = {
      year: 'numeric',
      month: options.month || 'short',
      day: options.day || 'numeric',
      timeZone: this.timezone,
      ...options
    };
    
    return new Intl.DateTimeFormat(this.locale, opts).format(date);
  }

  time(value, options = {}) {
    if (!value) return options.fallback || '\u2014';
    
    const date = value instanceof Date ? value : new Date(value);
    if (isNaN(date.getTime())) return options.fallback || '\u2014';
    
    const opts = {
      hour: options.hour || '2-digit',
      minute: options.minute || '2-digit',
      second: options.second || '2-digit',
      hour12: options.hour12 ?? false,
      timeZone: this.timezone,
      ...options
    };
    
    return new Intl.DateTimeFormat(this.locale, opts).format(date);
  }

  datetime(value, options = {}) {
    if (!value) return options.fallback || '\u2014';
    
    const date = value instanceof Date ? value : new Date(value);
    if (isNaN(date.getTime())) return options.fallback || '\u2014';
    
    const opts = {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: options.second ? '2-digit' : undefined,
      hour12: options.hour12 ?? false,
      timeZone: this.timezone,
      ...options
    };
    
    return new Intl.DateTimeFormat(this.locale, opts).format(date);
  }

  relativeTime(value, options = {}) {
    if (!value) return options.fallback || '\u2014';
    
    const date = value instanceof Date ? value : new Date(value);
    if (isNaN(date.getTime())) return options.fallback || '\u2014';
    
    const now = new Date();
    const diffMs = now - date;
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHour / 24);
    
    if (diffSec < 60) return `${diffSec}s ago`;
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHour < 24) return `${diffHour}h ago`;
    if (diffDay < 7) return `${diffDay}d ago`;
    
    return this.date(value, options);
  }

  duration(ms, options = {}) {
    if (ms === null || ms === undefined || isNaN(ms)) return options.fallback || '\u2014';
    
    const seconds = Math.floor(ms / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);
    
    if (options.compact) {
      if (days > 0) return `${days}d ${hours % 24}h`;
      if (hours > 0) return `${hours}h ${minutes % 60}m`;
      if (minutes > 0) return `${minutes}m ${seconds % 60}s`;
      return `${seconds}s`;
    }
    
    const parts = [];
    if (days > 0) parts.push(`${days}d`);
    if (hours > 0) parts.push(`${hours}h`);
    if (minutes > 0) parts.push(`${minutes}m`);
    if (seconds > 0 || parts.length === 0) parts.push(`${seconds}s`);
    
    return parts.join(' ');
  }

  // ============================================================
  // Text Formatting
  // ============================================================

  truncate(str, length = 50, suffix = '\u2026') {
    if (!str) return '';
    if (str.length <= length) return str;
    return str.slice(0, length - suffix.length) + suffix;
  }

  uppercase(str) {
    return str ? str.toUpperCase() : '';
  }

  lowercase(str) {
    return str ? str.toLowerCase() : '';
  }

  titleCase(str) {
    if (!str) return '';
    return str.replace(/\w\S*/g, txt => txt.charAt(0).toUpperCase() + txt.substr(1).toLowerCase());
  }

  // ============================================================
  // Symbol / Instrument Formatting
  // ============================================================

  symbol(symbol) {
    if (!symbol) return '\u2014';
    return symbol.toUpperCase();
  }

  escape(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  escapeSymbol(symbol) {
    return this.escape(this.symbol(symbol));
  }

  pair(base, quote) {
    if (!base || !quote) return '\u2014';
    return `${base.toUpperCase()}/${quote.toUpperCase()}`;
  }

  // ============================================================
  // Internal
  // ============================================================

  _getNumberFormat(options) {
    const key = JSON.stringify(options);
    if (!this._formatters.has(key)) {
      this._formatters.set(key, new Intl.NumberFormat(this.locale, options));
    }
    return this._formatters.get(key);
  }

  // Configuration
  setLocale(locale) {
    this.locale = locale;
    this._formatters.clear();
  }

  setCurrency(currency) {
    this.currency = currency;
    this._formatters.clear();
  }

  setTimezone(timezone) {
    this.timezone = timezone;
    this._formatters.clear();
  }
}

// Create default formatter instance
const fmt = new Formatter();

// Export for modules
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { Formatter, fmt };
} else {
  window.Formatter = Formatter;
  window.fmt = fmt;
}