import json
import sqlite3
import pytest
from maintenance import backup_database, main
from research import ResearchStore
from flows import FlowStore
from test_research import fixture, NOW
from test_flows import record, batch

def test_backup_and_restore_preserve_reports_flows_and_wal(tmp_path):
    original=tmp_path/'source.db'
    research=ResearchStore(original,lambda:NOW)
    report=research.save_report(fixture())
    flows=FlowStore(original,lambda:NOW)
    flows.import_records(batch(record()),True)
    writer=sqlite3.connect(original)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('CREATE TABLE recovery_probe(value TEXT)')
    writer.execute("INSERT INTO recovery_probe VALUES ('committed after WAL enabled')")
    writer.commit()
    try:
        saved=backup_database(original,tmp_path/'backups'/'copy.db')
        assert saved['integrity']=='ok' and saved['bytes']>0
        backup_database(saved['path'],tmp_path/'restored.db')
    finally:writer.close()
    restored=ResearchStore(tmp_path/'restored.db',lambda:NOW)
    assert restored.report(report['id'])==report
    assert FlowStore(tmp_path/'restored.db',lambda:NOW).snapshot()==flows.snapshot()
    with restored.connection() as conn:
        assert conn.execute('SELECT value FROM recovery_probe').fetchone()[0]=='committed after WAL enabled'
    with pytest.raises(FileExistsError):backup_database(original,saved['path'])
    with pytest.raises(ValueError,match='differ'):backup_database(original,original)

def test_missing_or_corrupt_database_cannot_produce_backup(tmp_path):
    with pytest.raises(ValueError):backup_database(tmp_path/'missing',tmp_path/'out')
    corrupt=tmp_path/'corrupt';corrupt.write_text('not sqlite')
    with pytest.raises(sqlite3.DatabaseError):backup_database(corrupt,tmp_path/'out')
    assert not (tmp_path/'out').exists()

def test_cli_verifies_report_and_does_not_overwrite_backup(tmp_path,capsys):
    path=tmp_path/'original.db'
    report=ResearchStore(path,lambda:NOW).save_report(fixture())
    main(['--database',str(path),'verify-report',report['id']])
    assert json.loads(capsys.readouterr().out)['digest']==report['digest']
    output=tmp_path/'copy.db'
    main(['--database',str(path),'backup','--output',str(output)])
    assert json.loads(capsys.readouterr().out)['integrity']=='ok'
    with pytest.raises(SystemExit) as e:main(['--database',str(path),'backup','--output',str(output)])
    assert e.value.code==1
