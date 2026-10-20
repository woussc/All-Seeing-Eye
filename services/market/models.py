from typing import Literal
from pydantic import BaseModel, ConfigDict

Status = Literal['demo', 'live', 'delayed', 'stale', 'unavailable']

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Point(Model):
    time: str
    value: float

class Quote(Model):
    price: float | None = None
    change1d: float | None = None
    change1w: float | None = None
    asOf: str | None = None

class Indicators(Model):
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    rsi14: float | None = None
    adx14: float | None = None
    atr14: float | None = None
    relativeVolume: float | None = None
    volatility: float | None = None

class Pivot(Model):
    time: str
    value: float
    confirmedAt: str

class Structure(Model):
    state: Literal['higher-highs-higher-lows','lower-highs-lower-lows','mixed','flat','unavailable']
    highs: list[Pivot]
    lows: list[Pivot]
    lookbackBars: int
    confirmationBars: Literal[2]

class TechnicalEvidence(Model):
    asOf: str
    basis: Literal['venue-daily-bars','provider-adjusted-daily-bars']
    windowUnit: Literal['calendar-days','sessions']
    return4w: float | None
    return13w: float | None
    ema20Slope5Pct: float | None
    ema50Slope5Pct: float | None
    ema200Slope5Pct: float | None
    atr14Pct: float | None
    structure: Structure
    classification: Literal['uptrend','downtrend','range','unclear']
    reason: str
    methodVersion: Literal['trend-v1']

class Asset(Quote):
    symbol: str
    name: str
    group: Literal['Equities', 'Rates & credit', 'Gold', 'Crypto', 'Cash']
    sourceId: str
    status: Status
    note: str
    commonClose: Quote | None = None
    history: list[Point] = []
    indicators: Indicators = Indicators()
    technicals: TechnicalEvidence | None = None

class Source(Model):
    id: str
    name: str
    status: Status
    lastObservation: str | None = None
    lastFetch: str | None = None
    cadence: str
    detail: str
    url: str

class Snapshot(Model):
    id: str
    mode: Literal['demo', 'connected']
    generatedAt: str
    commonCloseAt: str | None
    assets: list[Asset]
    sources: list[Source]
    context: dict | None = None
