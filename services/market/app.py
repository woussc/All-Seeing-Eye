import os
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from models import Snapshot
from providers import PublicProviders
from service import MarketService, Store
from flows import FlowStore
from macro import MacroService
from research import ResearchStore

ROOT=Path(__file__).resolve().parent

@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(headers={'User-Agent':'AllSeeingEyeResearch/0.1'},timeout=5,limits=httpx.Limits(max_connections=16,max_keepalive_connections=12)) as client:
        store=Store(os.environ.get('MARKET_DB_PATH',str(ROOT/'data'/'market.sqlite3')))
        app.state.market=MarketService(store,PublicProviders(client))
        app.state.flows=FlowStore(store.path)
        app.state.macro=MacroService(store.path,client)
        app.state.research=ResearchStore(store.path)
        app.state.snapshot_lock=asyncio.Lock()
        app.state.capture_error=None
        task=asyncio.create_task(daily_capture()) if os.environ.get('MARKET_CAPTURE_DAILY','0')=='1' else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass

app=FastAPI(title='All-Seeing-Eye Market Research API',version='0.1.0',lifespan=lifespan)

@app.get('/health')
async def health():
    with app.state.market.store.connection() as conn:
        conn.execute('SELECT 1').fetchone()
    return {'status':'ok','storage':'sqlite','mode':'connected','feedHealth':'See /snapshot sources; service health does not imply provider availability',
        'dailyCapture':os.environ.get('MARKET_CAPTURE_DAILY','0')=='1','captureError':app.state.capture_error}

@app.get('/snapshot',response_model=Snapshot)
async def snapshot():
    return await full_snapshot()

async def full_snapshot():
    async with app.state.snapshot_lock:
        market,macro=await asyncio.gather(app.state.market.snapshot(),app.state.macro.snapshot())
        data=market.model_dump()
        context={'generatedAt':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
            'macro':macro,'flows':app.state.flows.snapshot(),'alerts':[]}
        context['alerts']=app.state.research.alerts(data,context)
        data['context']=context
        with app.state.market.store.connection() as conn:
            conn.execute('UPDATE snapshots SET payload=? WHERE id=?',(json.dumps(data,allow_nan=False),data['id']))
        return data

async def body(request,limit=1_000_000):
    if not request.headers.get('content-type','').startswith('application/json'):
        raise HTTPException(415,'Use an application/json request')
    payload=bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload)>limit: raise HTTPException(413,f'Request exceeds the {limit}-byte limit')
    try:
        return json.loads(payload)
    except (ValueError,UnicodeDecodeError):
        raise HTTPException(400,'Invalid JSON')

@app.post('/flows/import')
async def import_flows(request:Request):
    raw=await body(request)
    if not isinstance(raw,dict) or set(raw)!= {'document','commit'} or not isinstance(raw['commit'],bool):
        raise HTTPException(400,'Expected document and boolean commit fields')
    try:
        return app.state.flows.import_records(raw['document'],raw['commit'])
    except ValidationError as exc:
        messages=['.'.join(str(p) for p in e['loc'])+': '+e['msg'] for e in exc.errors()[:8]]
        raise HTTPException(422,'; '.join(messages))
    except ValueError as exc:
        raise HTTPException(422,str(exc))

@app.get('/reports')
async def reports():
    return {'reports':app.state.research.list_reports()}

@app.get('/reports/{identity}')
async def report(identity:str):
    try:return app.state.research.report(identity)
    except KeyError:raise HTTPException(404,'Report not found')
    except ValueError:raise HTTPException(500,'Stored report failed its integrity check')

@app.post('/reports')
async def archive(request:Request):
    raw=await body(request,4096)
    if not isinstance(raw,dict) or set(raw)!={'snapshotId'} or not isinstance(raw['snapshotId'],str):
        raise HTTPException(400,'Expected snapshotId')
    with app.state.market.store.connection() as conn:
        row=conn.execute('SELECT payload FROM snapshots WHERE id=?',(raw['snapshotId'],)).fetchone()
    if not row:raise HTTPException(409,'Snapshot is no longer in the short-term cache. Refresh the source data and archive again.')
    try:return app.state.research.save_report(json.loads(row[0]))
    except ValueError as exc:raise HTTPException(422,str(exc))

@app.post('/alerts/{identity}/acknowledge')
async def acknowledge(identity:str):
    try:return app.state.research.acknowledge(identity)
    except KeyError:raise HTTPException(404,'Alert not found')

async def capture_if_due(now=None):
    now=now or datetime.now(ZoneInfo('Asia/Singapore'))
    if now.tzinfo is None:raise ValueError('Capture clock must include a timezone')
    now=now.astimezone(ZoneInfo('Asia/Singapore'))
    with app.state.market.store.connection() as conn:
        existing=conn.execute("SELECT 1 FROM reports WHERE kind='daily' AND report_date=?",(now.date().isoformat(),)).fetchone()
    if now.hour<8 or existing:return False
    app.state.research.save_report(await full_snapshot(),kind='daily')
    return True

async def daily_capture():
    # Optional local worker; does not change the user's existing scheduled task.
    # Startup after 08:00 captures the current evidence, never a backdated 08:00 view.
    while True:
        try:
            await capture_if_due()
            app.state.capture_error=None
        except Exception:
            app.state.capture_error='Daily capture failed; prior reports are preserved. Inspect service logs.'
            import logging
            logging.getLogger(__name__).exception('Daily capture failed')
        await asyncio.sleep(60)
