"""Official Binance public archive loader (data.binance.vision) with SHA-256 verification and causal normalization."""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ase.schemas import Candle, MarketType

BINANCE_VISION_BASE = "https://data.binance.vision"


def _norm_ts_ms(raw: int) -> datetime:
    """Normalize Binance ms or 2025+ microsecond spot timestamps to UTC datetime."""
    if raw > 10**14:
        return datetime.fromtimestamp(raw / 1_000_000.0, tz=timezone.utc)
    return datetime.fromtimestamp(raw / 1_000.0, tz=timezone.utc)


def _verify_and_cache(url: str, cache_path: Path, client: httpx.Client) -> bytes | None:
    if cache_path.is_file():
        return cache_path.read_bytes()
    resp = client.get(url, timeout=20.0)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.content

    # Verify publisher SHA-256 checksum when available
    chk_resp = client.get(url + ".CHECKSUM", timeout=10.0)
    if chk_resp.status_code == 200:
        expected_hash = chk_resp.text.strip().split()[0].lower()
        actual_hash = hashlib.sha256(data).hexdigest().lower()
        if actual_hash != expected_hash:
            raise ValueError(f"SHA-256 mismatch for {url}: expected {expected_hash}, got {actual_hash}")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    return data


def _parse_klines_zip(raw_zip: bytes, timeframe_hours: int = 4) -> list[Candle]:
    candles: list[Candle] = []
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        for name in zf.namelist():
            if not name.endswith(".csv"):
                continue
            text = zf.read(name).decode("utf-8")
            reader = csv.reader(io.StringIO(text))
            for row in reader:
                if not row or not row[0].isdigit():
                    continue
                open_ts = _norm_ts_ms(int(row[0]))
                close_ts = open_ts + timedelta(hours=timeframe_hours)
                o, h, l, c, v = float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
                if o <= 0 or h <= 0 or l <= 0 or c <= 0:
                    continue
                # Guard against rare rounding ticks where high/low trails open/close by epsilon
                h = max(h, o, c)
                l = min(l, o, c)
                candles.append(
                    Candle(open_ts=open_ts, close_ts=close_ts, open=o, high=h, low=l, close=c, volume=max(0.0, v))
                )
    return candles


def _parse_funding_zip(raw_zip: bytes) -> dict[datetime, float]:
    rates: dict[datetime, float] = {}
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        for name in zf.namelist():
            if not name.endswith(".csv"):
                continue
            text = zf.read(name).decode("utf-8")
            reader = csv.reader(io.StringIO(text))
            for row in reader:
                if not row or not row[0].isdigit():
                    continue
                calc_ts = _norm_ts_ms(int(row[0]))
                # Snap to exact settlement hour boundary for 4h bar alignment
                snapped = calc_ts.replace(minute=0, second=0, microsecond=0)
                rates[snapped] = float(row[2])
    return rates


def load_binance_vision_4h(
    symbol: str,
    market_type: MarketType,
    months: list[str],
    *,
    daily_dates: list[str] | None = None,
    cache_dir: str | Path = "data/cache/binance_vision",
) -> tuple[list[Candle], dict[datetime, float]]:
    """Fetch verified 4h OHLCV candles (and funding rates for linear_perps) from data.binance.vision."""
    sym = symbol.upper()
    cache_root = Path(cache_dir)
    prefix = "data/spot" if market_type == "spot" else "data/futures/um"

    kline_urls: list[tuple[str, Path]] = []
    for ym in months:
        fname = f"{sym}-4h-{ym}.zip"
        url = f"{BINANCE_VISION_BASE}/{prefix}/monthly/klines/{sym}/4h/{fname}"
        kline_urls.append((url, cache_root / market_type / "klines" / fname))
    for day in daily_dates or []:
        fname = f"{sym}-4h-{day}.zip"
        url = f"{BINANCE_VISION_BASE}/{prefix}/daily/klines/{sym}/4h/{fname}"
        kline_urls.append((url, cache_root / market_type / "klines" / fname))

    funding_urls: list[tuple[str, Path]] = []
    if market_type == "linear_perps":
        for ym in months:
            fname = f"{sym}-fundingRate-{ym}.zip"
            url = f"{BINANCE_VISION_BASE}/data/futures/um/monthly/fundingRate/{sym}/{fname}"
            funding_urls.append((url, cache_root / "linear_perps" / "funding" / fname))

    all_candles: dict[datetime, Candle] = {}
    all_funding: dict[datetime, float] = {}

    with httpx.Client(headers={"User-Agent": "AllSeeingEye/0.1"}) as client:
        for url, path in kline_urls:
            raw = _verify_and_cache(url, path, client)
            if raw:
                for c in _parse_klines_zip(raw, timeframe_hours=4):
                    all_candles[c.open_ts] = c
        for url, path in funding_urls:
            raw = _verify_and_cache(url, path, client)
            if raw:
                all_funding.update(_parse_funding_zip(raw))

    ordered = [all_candles[k] for k in sorted(all_candles)]
    return ordered, all_funding


def load_universe_post_cutoff(
    symbols_with_markets: list[tuple[str, MarketType]],
    *,
    months: list[str] | None = None,
    daily_dates: list[str] | None = None,
    cache_dir: str | Path = "data/cache/binance_vision",
) -> tuple[dict[str, list[Candle]], dict[str, dict[datetime, float]]]:
    """Download post-March-2026 Binance archives in parallel for walk-forward backtests."""
    target_months = months or [
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08",
        "2026-09",
        "2026-10",
    ]
    target_days = daily_dates if daily_dates is not None else [f"2026-11-{d:02d}" for d in range(1, 24)]

    candles_by_symbol: dict[str, list[Candle]] = {}
    funding_by_symbol: dict[str, dict[datetime, float]] = {}

    def _fetch_one(item: tuple[str, MarketType]):
        sym, mtype = item
        return sym, load_binance_vision_4h(
            sym, mtype, target_months, daily_dates=target_days, cache_dir=cache_dir
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        for sym, (candles, funding) in pool.map(_fetch_one, symbols_with_markets):
            candles_by_symbol[sym] = candles
            funding_by_symbol[sym] = funding

    return candles_by_symbol, funding_by_symbol
