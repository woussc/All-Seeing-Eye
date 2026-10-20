"""Deterministic, no-model monitor export. Missing evidence never becomes a flow."""
import math
import re
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from calculations import parse_time

UNIVERSE = ('SPY', 'QQQ', 'IWM', 'RSP', 'IEF', 'TLT', 'LQD', 'HYG', 'GLD', 'BTC', 'ETH', 'SGOV')
JEV_STATUS = 'Jev: disabled; connection approval pending'


def cell(value):
    return re.sub(r'([\\`*_{}\[\]<>|])', r'\\\1', str(value)).replace('\n', ' ').replace('\r', ' ')


def link(label, url):
    try:
        parsed = urlsplit(url or '')
        if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
            return f'[{cell(label)}]({quote(url, safe="/:?&=%#")})'
    except ValueError:
        pass
    return cell(label)


def number(value, suffix='', signed=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return 'Unavailable'
    return format(value, '+,.2f' if signed else ',.2f') + suffix


def flow_value(window):
    if not window or window.get('status') not in ('complete', 'stale'):
        return 'Unavailable'
    value = window.get('netFlowUsd')
    return number(value, ' USD', signed=True) + (' (stale)' if window['status'] == 'stale' else '')


def aligned_quote(asset, cutoff):
    candidate = asset.get('commonClose') or {}
    return candidate if cutoff and candidate.get('asOf') == cutoff else {}


def trend(asset, cutoff):
    history = asset.get('history') or []
    if asset.get('status') not in ('live', 'delayed') or not history or history[-1]['time'] != cutoff:
        return 'Unclear'
    technicals = asset.get('technicals') or {}
    if technicals.get('asOf') != cutoff or technicals.get('methodVersion') != 'trend-v1':
        return 'Unclear (missing trend evidence)'
    return {'uptrend':'Uptrend (rule v1)','downtrend':'Downtrend (rule v1)','range':'Range (rule v1)'}.get(technicals.get('classification'),'Unclear')


def render_monitor(snapshot, previous=None, participation=None):
    context = snapshot.get('context') or {}
    flows = context.get('flows', {}).get('series', [])
    macro = context.get('macro', {}).get('series', [])
    assets = {a['symbol']: a for a in snapshot['assets']}
    sources = {s.get('id'): s for s in snapshot.get('sources', [])}
    cutoff = snapshot.get('commonCloseAt')
    generated = parse_time(snapshot['generatedAt'])
    session = parse_time(cutoff).astimezone(ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M %Z') if cutoff else 'Unavailable'
    baseline = previous if previous and parse_time(previous['generatedAt']) <= generated else None
    lines = ["# SHUAI's Market Rotation Monitor", '',
             f'Report time: {generated.astimezone(ZoneInfo("Asia/Singapore")).strftime("%Y-%m-%d %H:%M SGT")}. Common price cutoff: {session}.', '',
             'Measured fund-flow coverage is unavailable; price moves alone cannot establish capital rotation.' if not flows else
             f'{len(flows)} sourced flow series are retained in separate scopes; this coverage does not establish a global capital-rotation ranking.', '',
             '## Twelve-reference watchlist', '',
             '| Reference | Close / as-of (UTC) | 1d | 1w | 4w adjusted | 13w adjusted | Trend | Observed flow | Risk flag |',
             '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for symbol in UNIVERSE:
        asset = assets.get(symbol, {})
        q = aligned_quote(asset, cutoff)
        t = asset.get('technicals') or {}
        if not cutoff or t.get('asOf') != cutoff or q.get('price') is None:
            t = {}
        direct = [s for s in flows if s.get('category') == 'named-etf' and s.get('constituents') == [symbol]]
        flow = 'See scoped flow table' if direct else 'Unavailable'
        risk = 'No matching close' if q.get('price') is None else asset.get('status', 'unavailable')
        label = link(symbol, sources.get(asset.get('sourceId'), {}).get('url'))
        lines.append(f'| {label} | {number(q.get("price"), " USD")} / {cell(q.get("asOf") or "unavailable")} | {number(q.get("change1d"), "%", True)} | {number(q.get("change1w"), "%", True)} | {number(t.get("return4w"), "%", True)} | {number(t.get("return13w"), "%", True)} | {trend(asset, cutoff)} | {flow} | {cell(risk)} |')
    lines += ['', 'ETF 1d/1w columns use raw prices excluding distributions; 4w/13w use provider-adjusted closes over 20/65 sessions. These bases differ. Trend-v1 requires EMA alignment, five-bar EMA slopes, ADX and confirmed two-sided pivots to agree; its thresholds are descriptive, not a tested trading edge. Missing or stale evidence remains unclear. EMA levels are not cost bases or proven support.', '',
              '### Newer crypto activity (separate clock)', '']
    for symbol in ('BTC', 'ETH'):
        a = assets.get(symbol, {})
        t = a.get('technicals') or {}
        lines.append(f'- {symbol}: {number(a.get("price"), " USD")} at {cell(a.get("asOf") or "unavailable")}; {cell(a.get("status", "unavailable"))}. Separate UTC-bar 4w/13w returns: {number(t.get("return4w"), "%", True)} / {number(t.get("return13w"), "%", True)}, ending {cell(t.get("asOf") or "unavailable")}, over 28/91 days. No latest quote fills a missing common-close mark.')
    lines += ['', '## Measured flows (资金流向)', '']
    if not flows:
        lines.append('No sourced flow release is connected. Release status is unknown; this does not mean zero flows or that no publisher issued an update. ICI money-market asset changes, ETF AUM growth, crypto turnover and open interest must not be reported as net flows.')
    for categories, title in [({'named-etf'}, 'Named funds'), ({'bitcoin-etfs', 'ethereum-etfs'}, 'US spot crypto ETF baskets'), ({'gold-etfs', 'fund-category'}, 'Broader-scope context')]:
        selected = [s for s in flows if s.get('category') in categories]
        if not selected:
            continue
        lines += ['', f'### {title}', '', '| Series / scope / source | 1w flow | 4w flow | 13w flow | 1w / beginning AUM | Period end / publication / retrieval |', '| --- | --- | --- | --- | --- | --- |']
        for s in selected[:5]:
            windows = {w['weeks']: w for w in s['windows']}
            one = windows.get(1, {})
            normalized = number(one.get('normalizedPct'), '%', True) if one.get('status') in ('complete', 'stale') else 'Unavailable'
            label = link(s['label'], s['sourceUrl']) + ' / ' + cell(s['scope'])
            values = ' | '.join(flow_value(windows.get(w)) for w in (1, 4, 13))
            lines.append(f'| {label} | {values} | {normalized} | {cell(one.get("periodEnd"))} / {cell(s["latestPublishedAt"])} / {cell(s["importedAt"])} |')
        if len(selected) > 5:
            lines.append(f'{len(selected)-5} additional series are available in the archived JSON. No cross-scope sum or rank is computed.')
    if baseline and flows:
        old = {s['id']: s for s in (baseline.get('context') or {}).get('flows', {}).get('series', [])}
        changed = sum(old.get(s['id']) != s for s in flows)
        lines += ['', f'{changed} covered flow series differ from the previous captured state. Differences can be revisions, coverage or staleness changes; they do not by themselves prove a new publication.']
    lines += ['', '## Macro, positioning and participation', '',
              '| Macro evidence | Level | Change vs prior available observation | Observation / previous date | Status |',
              '| --- | --- | --- | --- | --- |']
    for s in macro:
        delta = s.get('change')
        if delta is not None and s['unit'] == 'percent':
            delta *= 100
        lines.append(f'| {link(s["label"], s.get("sourceUrl"))} | {number(s.get("value"), "%" if s["unit"] == "percent" else " bp")} | {number(delta, " bp", True)} | {cell(s.get("observedOn"))} / {cell(s.get("previousOn"))} | {cell(s["status"])} |')
    if not macro:
        lines.append('| Unavailable | Unavailable | Unavailable | Unavailable | unavailable |')
    lines += ['', 'Three review areas are shown without manufacturing three new developments:', '',
              '1. **Rates — fact:** the dated table records available observations. Changes are against the previous source observation, not necessarily since the previous report. Publication timestamps are unverified. **Hypothesis:** higher real yields can pressure duration and gold, but growth, inflation and currency effects can offset that relationship.',
              '2. **Participation — fact:** ' + (f'RSP minus SPY {participation[0]["RSP"]-participation[0]["SPY"]:+.2f} pp; IWM minus SPY {participation[0]["IWM"]-participation[0]["SPY"]:+.2f} pp across five aligned completed sessions ({participation[1][:10]} to {participation[2][:10]}), using adjusted prices. This is breadth (市场广度), not a fund-flow measurement.' if participation else 'Fresh aligned five-session SPY/RSP/IWM evidence is insufficient. No breadth conclusion is issued.'),
              '3. **Positioning — gap:** CFTC positioning, exchange funding and open interest are not connected. Leverage measures, when added, must retain venue and report dates and remain separate from net capital flows.', '',
              '## Three research priorities', '',
              '- **Equity participation:** compare SPY, RSP and IWM prices with comparable measured fund flows and fresh growth releases. The rotation hypothesis remains unconfirmed without flow evidence; broadening would be contradicted by persistent equal-weight and small-cap underperformance.',
              '- **Duration versus credit:** compare IEF/TLT with LQD/HYG, Treasury yields and sourced HY spreads. Price divergence alone cannot identify credit allocation. A duration interpretation weakens if spread changes explain the move; a flow interpretation needs measured subscriptions or redemptions.',
              '- **Gold versus digital assets:** compare GLD with BTC/ETH only at clearly labeled timestamps, alongside real yields and separately scoped ETF flows. Missing common marks or incompatible flow periods prevent confirmation. Divergent flows or an opposing real-yield backdrop would challenge a shared allocation hypothesis.', '',
              '## Catalysts, freshness and decision record', '',
              'Catalyst dates and consensus expectations are unavailable until an official release-calendar source is connected. CPI/PCE, jobs, claims, JOLTS, GDP, ISM/PMI, FOMC and equity fundamentals update only on actual releases; no unchanged historical release is presented as new.', '',
              ('Baseline: unavailable; no usable previous archived state.' if not baseline else f'Baseline: snapshot {cell(baseline["id"])} captured {cell(baseline["generatedAt"])}. This is a captured-state comparison, not a reconstruction of historical publication vintages.'), '',
              'Decision record: research observations only; no trade, size or position-management action. Missing flow, positioning, fundamentals, catalysts, DXY and credit-spread inputs limit conclusions. Prices retain source status; no synthetic value fills a provider outage. Archives persist locally only while this storage survives.', '',
              JEV_STATUS + '. No Jev request or model inference is performed.', '',
              f'Snapshot: {cell(snapshot["id"])}. Source observation/retrieval metadata and evidence references remain in the accompanying JSON archive.']
    return '\n'.join(lines) + '\n'
