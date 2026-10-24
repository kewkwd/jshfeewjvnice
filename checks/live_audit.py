"""Bounded public reads in isolated databases. No authenticated cookies supplied."""
import argparse, datetime, json, os, sqlite3, subprocess, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from socialscraper.net.accountproxy import scrub

def run(platform,url,mode,comments,label=None):
    if mode != 'configured':
        raise ValueError('Direkte externe Tests sind gesperrt.')
    dest=ROOT/'data/live-audit'/(label or f'{platform}-{mode}')
    dest.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,PYTHONUTF8='1',SS_DB_PATH=str(dest/'scrape.db'),SS_PROFILES_DIR=str(dest/'profiles'),
             SS_HEADLESS='true',SS_MAX_COMMENTS=str(comments),SS_MAX_RETRIES='1',SS_VIDEO_TIMEOUT='90',SS_TARGET_TIMEOUT='150')
    env.update(SS_REQUIRE_PROXY='1',SS_AUDIT_NETWORK='proxy',SS_NOTIFY_WEBHOOK='')
    args=[sys.executable,'-m','socialscraper','scrape',url,'--identity',f'audit_public_{platform}_{mode}',
          '--country','de','--max-comments',str(comments),'--export',str(dest/'export')]
    start=time.monotonic()
    proc=subprocess.Popen(args,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          text=True,encoding='utf-8',errors='replace')
    try:
        log,_=proc.communicate(timeout=170)
        result={'exit_code':proc.returncode}
    except subprocess.TimeoutExpired:
        subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
        log,_=proc.communicate()
        result={'timeout':True,'exit_code':proc.returncode}
    log=scrub(log)
    (dest/'run.log').write_text(log,encoding='utf-8')
    result.update(platform=platform,url=url,connection=mode,requested_comments=comments,
                  duration_seconds=round(time.monotonic()-start,2),checked_at=datetime.datetime.now().astimezone().isoformat(),account_used=False)
    path=dest/'scrape.db'
    if path.is_file():
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
        try:
            result['videos_saved']=db.execute('SELECT COUNT(*) FROM videos').fetchone()[0]
            result['comments_saved']=db.execute('SELECT COUNT(*) FROM comments').fetchone()[0]
            result['comments_with_missing_id']=db.execute("SELECT COUNT(*) FROM comments WHERE uid IS NULL OR comment_id IS NULL OR comment_id='' ").fetchone()[0]
            result['video_metadata']=db.execute('SELECT video_id,author,view_count,comment_count FROM videos').fetchall()
        except sqlite3.Error as error:
            result['database_error']=str(error)
        finally:db.close()
    result['fetch_events']=[json.loads(x[len('[SS_FETCH] '):]) for x in log.splitlines() if x.startswith('[SS_FETCH] ')]
    (dest/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    print('LOG TAIL:\n'+'\n'.join(log.splitlines()[-12:]))
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('platform');ap.add_argument('url');ap.add_argument('--mode',choices=['configured'],default='configured');ap.add_argument('--comments',type=int,default=5);ap.add_argument('--label')
    a=ap.parse_args();run(a.platform,a.url,a.mode,a.comments,a.label)
