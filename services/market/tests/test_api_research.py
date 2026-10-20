import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
import app as module
from test_market import Provider
from test_flows import NOW, record, batch

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('MARKET_DB_PATH',str(tmp_path/'api.db'))
    monkeypatch.setenv('MARKET_CAPTURE_DAILY','0')
    with TestClient(module.app) as client:
        module.app.state.market.provider=Provider(error=httpx.ProxyError('fixture blocked'))
        module.app.state.market.clock=lambda:NOW
        module.app.state.flows.clock=lambda:NOW
        module.app.state.research.clock=lambda:NOW
        async def macro():return {'generatedAt':NOW.isoformat().replace('+00:00','Z'),'series':[]}
        module.app.state.macro.snapshot=macro
        yield client

def test_source_snapshot_archives_idempotently_and_survives_cache_eviction(client):
    snapshot=client.get('/snapshot').json()
    assert 'context' in snapshot and len(snapshot['context']['alerts'])==3
    response=client.post('/reports',json={'snapshotId':snapshot['id']})
    assert response.status_code==200
    report=response.json()
    assert report['snapshot']==snapshot and report['briefing']['facts']
    assert client.post('/reports',json={'snapshotId':snapshot['id']}).json()==report
    summary=client.get('/reports').json()['reports']
    assert len(summary)==1 and 'snapshot' not in summary[0]
    with module.app.state.market.store.connection() as conn:conn.execute('DELETE FROM snapshots')
    assert client.get('/reports/'+report['id']).json()==report
    assert client.post('/reports',json={'snapshotId':snapshot['id']}).status_code==409

def test_flow_preview_commit_revision_and_snapshot_integration(client):
    document=batch(record())
    preview=client.post('/flows/import',json={'document':document,'commit':False})
    assert preview.status_code==200 and preview.json()['inserted']==1
    assert client.get('/snapshot').json()['context']['flows']['recordCount']==0
    result=client.post('/flows/import',json={'document':document,'commit':True})
    assert result.status_code==200
    snapshot=client.get('/snapshot').json()
    assert snapshot['context']['flows']['series'][0]['windows'][0]['netFlowUsd']==100
    assert client.post('/flows/import',json={'document':document,'commit':True}).json()['unchanged']==1
    conflict=batch(record(netFlowUsd=200.0))
    assert client.post('/flows/import',json={'document':conflict,'commit':True}).status_code==422

def test_api_rejects_invalid_and_oversized_mutations(client):
    assert client.post('/flows/import',json={'commit':'true','document':batch(record())}).status_code==400
    assert client.post('/flows/import',json={'commit':True,'document':batch(record(netFlowUsd=None))}).status_code==422
    assert client.post('/flows/import',content='invalid',headers={'content-type':'application/json'}).status_code==400
    assert client.post('/flows/import',content='{}').status_code==415
    assert client.post('/flows/import',content=' '*1_000_001,headers={'content-type':'application/json'}).status_code==413
    assert client.post('/reports',json={'snapshotId':'x'*5000}).status_code==413
    assert client.get('/reports/missing').status_code==404
    assert client.post('/reports',json={'snapshotId':'missing'}).status_code==409

def test_acknowledgment_survives_refresh_without_resolving_alert(client):
    snapshot=client.get('/snapshot').json()
    rule=snapshot['context']['alerts'][0]
    assert rule['state']=='active'
    acknowledged=client.post('/alerts/'+rule['id']+'/acknowledge',json={}).json()
    assert acknowledged['acknowledgedAt'] and acknowledged['state']=='active'
    refreshed=client.get('/snapshot').json()['context']['alerts'][0]
    assert refreshed['acknowledgedAt']==acknowledged['acknowledgedAt']
    assert refreshed['transitions']==rule['transitions']
    assert client.post('/alerts/missing/acknowledge',json={}).status_code==404

def test_daily_capture_is_once_per_singapore_day_and_not_backdated(client,monkeypatch):
    captured=[]
    async def full():
        from test_research import fixture
        snapshot=fixture('daily-'+str(len(captured)))
        captured.append(snapshot)
        return snapshot
    monkeypatch.setattr(module,'full_snapshot',full)
    assert asyncio.run(module.capture_if_due(datetime(2026,10,18,23,59,tzinfo=timezone.utc))) is False
    assert asyncio.run(module.capture_if_due(NOW)) is True
    assert asyncio.run(module.capture_if_due(NOW)) is False
    reports=client.get('/reports').json()['reports']
    assert len(captured)==1 and reports[0]['reportDate']=='2026-10-19'
    assert reports[0]['createdAt']==NOW.isoformat().replace('+00:00','Z')
    assert client.get('/health').json()['dailyCapture'] is False
