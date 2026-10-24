"""A bounded real CLI scrape of one public URL; no account cookies are supplied."""
import argparse
import contextlib
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def run(url, platform, limit):
    destination = ROOT / 'data/live-check' / platform
    destination.mkdir(parents=True,exist_ok=True)
    environment = dict(os.environ)
    environment.update({'SS_DB_PATH':str(destination/'scrape.db'), 'SS_PROFILES_DIR':str(destination/'profiles'), 'SS_HEADLESS':'true'})
    command = [sys.executable,'-m','socialscraper','scrape',url,'--identity','public_test_'+platform,'--country','de','--max-comments',str(limit),'--export',str(destination/'export')]
    report = {'date':dt.datetime.now(dt.timezone.utc).isoformat(),'platform':platform,'url':url,'comment_limit':limit,'account_used':False}
    started=time.monotonic()
    try:
        result=subprocess.run(command,cwd=ROOT,env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
        (destination/'run.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        report['exit_code']=result.returncode
        if (destination/'scrape.db').is_file():
            with contextlib.closing(sqlite3.connect(destination/'scrape.db')) as database:   # der sqlite3-Kontext schließt nicht
                report['videos_saved']=database.execute('SELECT COUNT(*) FROM videos').fetchone()[0]
                report['comments_saved']=database.execute('SELECT COUNT(*) FROM comments').fetchone()[0]
                report['video_ids']=list(item[0] for item in database.execute('SELECT video_id FROM videos'))
                report['missing_ids']=database.execute('SELECT COUNT(*) FROM comments WHERE uid IS NULL OR comment_id IS NULL').fetchone()[0]
        report['passed']=result.returncode==0 and report.get('videos_saved')==1 and report.get('comments_saved',0)<=limit and report.get('missing_ids')==0
        report['comments_observed']=report.get('comments_saved',0)>0
    except subprocess.TimeoutExpired:
        report.update({'passed':False,'error':'Timeout after 180 seconds'})
    report['duration_seconds']=round(time.monotonic()-started,2)
    (destination/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return 0 if report['passed'] else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--platform',choices=['youtube','tiktok'],required=True)
    parser.add_argument('--max-comments',type=int,default=5)
    args=parser.parse_args()
    raise SystemExit(run(args.url,args.platform,args.max_comments))
