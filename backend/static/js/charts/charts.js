// PUNCH NEXUS - Chart Components
// Lightweight Charts integration with NEXUS styling

class ChartManager {
  constructor(container, options = {}) {
    this.container = typeof container === 'string' ? document.querySelector(container) : container;
    this.options = {
      width: options.width || this.container.clientWidth,
      height: options.height || 400,
      layout: {
        background: { color: '#0C131C' },
        textColor: '#E8EEF6',
        fontSize: 12,
        fontFamily: 'JetBrains Mono, monospace',
        attributionLogo: false
      },
      grid: {
        vertLines: { color: '#1D2937' },
        horzLines: { color: '#1D2937' }
      },
      crosshair: {
        mode: 1, // Normal
        vertLine: { color: '#38BDF8', width: 1, style: 2 },
        horzLine: { color: '#38BDF8', width: 1, style: 2 }
      },
      rightPriceScale: {
        borderColor: '#1D2937',
        scaleMargins: { top: 0.1, bottom: 0.1 }
      },
      timeScale: {
        borderColor: '#1D2937',
        timeVisible: true,
        secondsVisible: false
      },
      ...options
    };
    
    this.chart = null;
    this.series = new Map();
    this.markers = [];
    this._initialized = false;
  }

  async init() {
    if (this._initialized) return;
    
    // Load Lightweight Charts dynamically (local vendored copy first,
    // CDN fallback for stale caches)
    if (!window.LightweightCharts) {
      try {
        await this._loadLocalLibrary();
      } catch (error) {
        await this._loadLightweightCharts();
      }
    }
    
    this.chart = window.LightweightCharts.createChart(this.container, this.options);
    this._initialized = true;
    
    // Handle resize
    this._setupResizeObserver();
    
    return this;
  }

  _loadLocalLibrary() {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = '/static/vendor/lightweight-charts.js';
      script.onload = () => resolve();
      script.onerror = () => reject(new Error('Failed to load local Lightweight Charts'));
      document.head.appendChild(script);
    });
  }

  async _loadLightweightCharts() {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = 'https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js';
      script.onload = () => resolve();
      script.onerror = () => reject(new Error('Failed to load Lightweight Charts'));
      document.head.appendChild(script);
    });
  }

  _setupResizeObserver() {
    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        if (entry.target === this.container) {
          this.chart.applyOptions({
            width: entry.contentRect.width,
            height: entry.contentRect.height
          });
        }
      }
    });
    resizeObserver.observe(this.container);
  }

  // Series management
  addCandlestickSeries(options = {}) {
    const series = this.chart.addCandlestickSeries({
      upColor: '#2DD4BF',
      downColor: '#FB7185',
      borderVisible: false,
      wickUpColor: '#2DD4BF',
      wickDownColor: '#FB7185',
      ...options
    });
    this.series.set('candlestick', series);
    return series;
  }

  addLineSeries(options = {}) {
    const series = this.chart.addLineSeries({
      color: '#38BDF8',
      lineWidth: 2,
      ...options
    });
    this.series.set('line', series);
    return series;
  }

  addAreaSeries(options = {}) {
    const series = this.chart.addAreaSeries({
      topColor: 'rgba(56, 189, 248, 0.4)',
      bottomColor: 'rgba(56, 189, 248, 0)',
      lineColor: '#38BDF8',
      lineWidth: 2,
      ...options
    });
    this.series.set('area', series);
    return series;
  }

  addHistogramSeries(options = {}) {
    const series = this.chart.addHistogramSeries({
      color: '#38BDF8',
      base: 0,
      ...options
    });
    this.series.set('histogram', series);
    return series;
  }

  // Data management
  setData(seriesKey, data) {
    const series = this.series.get(seriesKey);
    if (series) {
      series.setData(data);
    }
  }

  updateData(seriesKey, data) {
    const series = this.series.get(seriesKey);
    if (series) {
      series.update(data);
    }
  }

  // Markers
  addMarkers(markers) {
    const series = this.series.get('candlestick') || this.series.get('line');
    if (series) {
      series.setMarkers(markers);
      this.markers = markers;
    }
  }

  clearMarkers() {
    const series = this.series.get('candlestick') || this.series.get('line');
    if (series) {
      series.setMarkers([]);
      this.markers = [];
    }
  }

  // Time scale
  fitContent() {
    this.chart.timeScale().fitContent();
  }

  scrollToPosition(position, animated = true) {
    this.chart.timeScale().scrollToPosition(position, animated);
  }

  // Cleanup
  destroy() {
    if (this.chart) {
      this.chart.remove();
      this.chart = null;
      this.series.clear();
      this.markers = [];
      this._initialized = false;
    }
  }
}

// Multi-chart layout manager
class ChartLayout {
  constructor(container, layout = 'single') {
    this.container = typeof container === 'string' ? document.querySelector(container) : container;
    this.layout = layout;
    this.charts = new Map();
    this._initialized = false;
  }

  async init() {
    this.container.classList.add('chart-layout');
    this.container.style.display = 'grid';
    this.container.style.gap = '16px';
    this._applyLayout();
    this._initialized = true;
  }

  _applyLayout() {
    switch (this.layout) {
      case 'single':
        this.container.style.gridTemplateColumns = '1fr';
        this.container.style.gridTemplateRows = '1fr';
        break;
      case 'horizontal':
        this.container.style.gridTemplateColumns = '1fr 1fr';
        this.container.style.gridTemplateRows = '1fr';
        break;
      case 'vertical':
        this.container.style.gridTemplateColumns = '1fr';
        this.container.style.gridTemplateRows = '1fr 1fr';
        break;
      case 'quad':
        this.container.style.gridTemplateColumns = '1fr 1fr';
        this.container.style.gridTemplateRows = '1fr 1fr';
        break;
      case 'main-side':
        this.container.style.gridTemplateColumns = '1fr 300px';
        this.container.style.gridTemplateRows = '1fr';
        break;
    }
  }

  async addChart(id, options = {}) {
    const chartContainer = document.createElement('div');
    chartContainer.className = 'chart-container';
    chartContainer.style.width = '100%';
    chartContainer.style.height = '100%';
    chartContainer.style.minHeight = '200px';
    this.container.appendChild(chartContainer);

    const chart = new ChartManager(chartContainer, options);
    await chart.init();
    this.charts.set(id, chart);
    return chart;
  }

  getChart(id) {
    return this.charts.get(id);
  }

  removeChart(id) {
    const chart = this.charts.get(id);
    if (chart) {
      chart.destroy();
      this.charts.delete(id);
    }
  }

  setLayout(layout) {
    this.layout = layout;
    this._applyLayout();
  }

  destroy() {
    for (const chart of this.charts.values()) {
      chart.destroy();
    }
    this.charts.clear();
  }
}

// Export for modules
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { ChartManager, ChartLayout };
} else {
  window.ChartManager = ChartManager;
  window.ChartLayout = ChartLayout;
}