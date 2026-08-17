"""Live market feed.

Three sources behind one interface:
- paper   : synthetic bars on a timer (smoke tests, no accounts)
- binance : polls public CCXT OHLCV, emits newly-completed candles
- kite    : KiteTicker websocket ticks accumulated into 1m candles

Every completed candle is pushed through the same engine path, so
strategies behave identically regardless of source. Broker calls that
would block the event loop (ccxt/kite REST) run via asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Dict, List, Optional

from . import config
from .engine import StrategyRunner


class CandleBuilder:
    """Accumulates ticks into candles of `interval` seconds."""

    def __init__(self, interval: float):
        self.interval = interval
        self._candle: Optional[dict] = None

    def add_tick(self, ts: float, price: float, volume: float = 0.0) -> Optional[dict]:
        """Returns a completed candle when one closes."""
        bucket = int(ts // self.interval) * self.interval
        if self._candle is None or self._candle["ts"] != bucket:
            done = self._candle
            self._candle = {"ts": bucket, "open": price, "high": price,
                            "low": price, "close": price, "volume": volume}
            return done
        c = self._candle
        c["high"] = max(c["high"], price)
        c["low"] = min(c["low"], price)
        c["close"] = price
        c["volume"] += volume
        return None


class LiveFeed:
    """Owns per-symbol bar series and feeds the strategy runners."""

    def __init__(self, broker, runners: Dict[str, StrategyRunner],
                 on_signal: Callable[[dict], None],
                 on_position_close: Optional[Callable[[dict], None]] = None,
                 candle_interval: float = 60.0):
        self.broker = broker
        self.runners = runners
        self.on_signal = on_signal
        self.on_position_close = on_position_close or (lambda p: None)
        self.candle_interval = candle_interval
        self.bars: Dict[str, List[dict]] = {}
        self.last_ts: Dict[str, float] = {}  # symbol -> last ingested bar ts
        self.last_error: Dict[str, str] = {}  # symbol -> last poll error
        self._tasks: List[asyncio.Task] = []
        self._last_ohlcv_ts: Dict[str, float] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._kite_ticker = None

    # ---- public ---------------------------------------------------------
    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self.broker.name == "paper":
            self._tasks.append(asyncio.create_task(self._paper_loop()))
        elif self.broker.name == "binance":
            self._tasks.append(asyncio.create_task(self._binance_loop()))
        elif self.broker.name == "kite":
            self._tasks.append(asyncio.create_task(self._kite_loop()))

    def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._kite_ticker is not None:
            try:
                self._kite_ticker.close()
            except Exception:
                pass

    def symbols(self) -> List[str]:
        return sorted({r.strategy["symbol"] for r in self.runners.values()})

    def health(self) -> List[dict]:
        """Per-symbol feed health for /api/system/health and the dashboard."""
        now = time.time()
        out = []
        for symbol in self.symbols():
            last = self.last_ts.get(symbol, 0.0)
            stale_after = (config.LIVE_FEED_STALE_AFTER
                           if self.broker.name != "paper" else config.FEED_STALE_AFTER)
            out.append({
                "symbol": symbol,
                "source": self.broker.name,
                "bars": len(self.bars.get(symbol, [])),
                "lastBarAgeSec": round(now - last, 1) if last else None,
                "stale": bool(last) and (now - last) > stale_after,
                "lastError": self.last_error.get(symbol),
            })
        return out

    # ---- candle ingestion -----------------------------------------------
    def ingest_bar(self, symbol: str, bar: dict) -> None:
        self.last_ts[symbol] = bar.get("ts", time.time())
        self.last_error.pop(symbol, None)
        series = self.bars.setdefault(symbol, [])
        series.append(bar)
        if len(series) > config.MAX_BARS_KEPT:
            del series[: len(series) - config.MAX_BARS_KEPT]
        self._evaluate(symbol, series)
        if self.broker.name == "paper":
            for closed in self.broker.on_bar(symbol, bar):
                self.on_position_close(closed)

    def _evaluate(self, symbol: str, series: List[dict]) -> None:
        for runner in self.runners.values():
            if runner.strategy["symbol"] != symbol:
                continue
            signal = runner.on_bar(series)
            if signal is not None:
                self.on_signal(signal.to_dict())

    # ---- paper ----------------------------------------------------------
    async def _paper_loop(self) -> None:
        for symbol in self.symbols():
            try:
                bars = await asyncio.to_thread(
                    self.broker.get_historical_bars, symbol, "5m", 30)
                if bars:
                    self.bars[symbol] = bars[-config.MAX_BARS_KEPT:]
                    self.last_ts[symbol] = bars[-1]["ts"]
            except Exception as e:
                self.last_error[symbol] = str(e)[:200]
        while True:
            await asyncio.sleep(config.BAR_SECONDS)
            for symbol in self.symbols():
                series = self.bars.get(symbol)
                if not series:
                    continue
                prev = series[-1]["close"]
                import random
                drift = 0.0003
                close = max(1.0, prev * (1 + drift + random.gauss(0, 0.006)))
                bar = {"ts": time.time(), "open": prev,
                       "high": max(prev, close) * (1 + abs(random.gauss(0, 0.003))),
                       "low": min(prev, close) * (1 - abs(random.gauss(0, 0.003))),
                       "close": close,
                       "volume": random.randint(1000, 40000)}
                self.ingest_bar(symbol, bar)

    # ---- binance --------------------------------------------------------
    async def _binance_loop(self) -> None:
        for symbol in self.symbols():
            try:
                bars = await asyncio.to_thread(
                    self.broker.get_historical_bars, symbol, "1m", 1)
                for b in bars:
                    if self._last_ohlcv_ts.get(symbol, 0) < b["ts"]:
                        self._last_ohlcv_ts[symbol] = b["ts"]
                if bars:
                    self._prime(symbol, bars[-config.MAX_BARS_KEPT:])
            except Exception as e:
                self.last_error[symbol] = str(e)[:200]
        while True:
            await asyncio.sleep(15)
            for symbol in self.symbols():
                try:
                    rows = await asyncio.to_thread(
                        self.broker.get_historical_bars, symbol, "1m", 1)
                    for b in rows:
                        if self._last_ohlcv_ts.get(symbol, 0) < b["ts"]:
                            self._last_ohlcv_ts[symbol] = b["ts"]
                            self.ingest_bar(symbol, b)
                except Exception as e:
                    self.last_error[symbol] = str(e)[:200]

    def _prime(self, symbol: str, bars: List[dict]) -> None:
        self.bars[symbol] = bars

    # ---- kite -----------------------------------------------------------
    async def _kite_loop(self) -> None:
        from kiteconnect import KiteTicker

        await asyncio.sleep(0.5)
        builders: Dict[str, CandleBuilder] = {}
        try:
            instruments = self.broker._kite.instruments("NSE")
        except Exception as e:
            print(f"[feed] kite instruments failed: {e}")
            return
        tokens = []
        for symbol in self.symbols():
            for ins in instruments:
                if ins.get("tradingsymbol") == symbol and ins.get("segment") == "NSE":
                    tokens.append(ins["instrument_token"])
                    builders[symbol] = CandleBuilder(self.candle_interval)
                    break
        if not tokens:
            print("[feed] no kite tokens matched — nothing to subscribe to")
            return
        # prime with recent candles so indicators are warm before live ticks
        for symbol in self.symbols():
            try:
                bars = await asyncio.to_thread(
                    self.broker.get_historical_bars, symbol, "1m", 1)
                if bars:
                    self.bars[symbol] = bars[-config.MAX_BARS_KEPT:]
            except Exception:
                pass

        def on_ticks(ws, ticks):
            for t in ticks:
                price = t.get("last_price")
                if price is None:
                    continue
                for symbol, builder in builders.items():
                    done = builder.add_tick(t.get("timestamp", time.time()) / 1000
                                            if t.get("timestamp") else time.time(),
                                            price, t.get("volume", 0))
                    if done is not None:
                        self._loop.call_soon_threadsafe(self.ingest_bar, symbol, done)

        def on_connect(ws, response):
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)

        def run():
            self._kite_ticker = KiteTicker(self.broker.api_key, self.broker.access_token)
            self._kite_ticker.on_ticks = on_ticks
            self._kite_ticker.on_connect = on_connect
            self._kite_ticker.connect(threaded=False)

        try:
            await asyncio.to_thread(run)
        except Exception as e:
            print(f"[feed] kite ticker error: {e}")