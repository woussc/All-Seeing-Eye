"""Local maintenance commands. Never overwrites a database or backup."""
import argparse
import json
import os
import sqlite3
from pathlib import Path

from flows import FlowStore
from research import ResearchStore

DEFAULT_DB=Path(__file__).resolve().parent/'data'/'market.sqlite3'

def existing_database(path):
    path=Path(path).resolve()
    if not path.is_file():raise ValueError('Source database does not exist')
    return path

def backup_database(source, output):
    """SQLite online backup includes committed WAL data in a standalone file."""
    source=existing_database(source)
    output=Path(output).absolute()
    if source==output.resolve():raise ValueError('Backup destination must differ from the source')
    output.parent.mkdir(parents=True,exist_ok=True)
    descriptor=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(descriptor)
    try:
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=5) as origin:
            with sqlite3.connect(output,timeout=5) as target:
                origin.backup(target,pages=256,sleep=0.05)
                result=target.execute('PRAGMA integrity_check').fetchall()
                if result!=[('ok',)]:raise ValueError('Backup failed SQLite integrity_check')
                # Backup has its own journal mode; no dependency on source sidecars.
                target.execute('PRAGMA journal_mode=DELETE')
        return {'status':'ok','path':str(output),'bytes':output.stat().st_size,'integrity':'ok'}
    except BaseException:
        output.unlink(missing_ok=True)
        raise

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',default=os.environ.get('MARKET_DB_PATH',str(DEFAULT_DB)))
    commands=parser.add_subparsers(dest='command',required=True)
    backup=commands.add_parser('backup');backup.add_argument('--output',required=True)
    restore=commands.add_parser('restore',help='Restore to a new database path; stop services before switching paths')
    restore.add_argument('--input',required=True);restore.add_argument('--output',required=True)
    flow=commands.add_parser('import-flows');flow.add_argument('--file',required=True);flow.add_argument('--commit',action='store_true')
    verify=commands.add_parser('verify-report');verify.add_argument('id')
    args=parser.parse_args(argv)
    try:
        if args.command=='backup':result=backup_database(args.database,args.output)
        elif args.command=='restore':result=backup_database(args.input,args.output)
        elif args.command=='import-flows':
            path=Path(args.file)
            if path.stat().st_size>900_000:raise ValueError('Import file exceeds 900 KB')
            raw=json.loads(path.read_text())
            result=FlowStore(existing_database(args.database)).import_records(raw,args.commit)
        else:
            report=ResearchStore(existing_database(args.database)).report(args.id)
            result={'status':'ok','id':report['id'],'digest':report['digest']}
    except (OSError,ValueError,KeyError,sqlite3.DatabaseError) as exc:
        parser.exit(1,f'Maintenance failed: {exc}\n')
    print(json.dumps(result))

if __name__=='__main__':main()
