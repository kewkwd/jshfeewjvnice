import datetime,json,os,sqlite3,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from socialscraper.net.accountproxy import scrub
dest=ROOT/'data/live-audit/youtube-channel-proxy-fixed';dest.mkdir(parents=True,exist_ok=True)
env=dict(os.environ,PYTHONUTF8='1',SS_DB_PATH=str(dest/'scrape.db'),SS_PROFILES_DIR=str(dest/'profiles'),SS_TARGET_TIMEOUT='150',SS_VIDEO_TIMEOUT='90')
env.update(SS_REQUIRE_PROXY='1',SS_AUDIT_NETWORK='proxy',SS_NOTIFY_WEBHOOK='')
results=[]
for args in [ ['channel','add','https://www.youtube.com/@jawed','--latest','1','--max-comments','3','--no-watch','--group','Live-Kanaltest'], ['run-target','1'] ]:
    start=time.monotonic();proc=subprocess.Popen([sys.executable,'-m','socialscraper',*args],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
    try:log,_=proc.communicate(timeout=170);code=proc.returncode
    except subprocess.TimeoutExpired:
        subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True);log,_=proc.communicate();code=proc.returncode
    log=scrub(log);(dest/f'command-{len(results)+1}.log').write_text(log,encoding='utf-8')
    results.append({'command':args,'exit_code':code,'duration_seconds':round(time.monotonic()-start,2)})
    print(json.dumps(results[-1]),flush=True)
    if code:break
db=sqlite3.connect((dest/'scrape.db').as_uri()+'?mode=ro',uri=True)
try:
    summary={'commands':results,'checked_at':datetime.datetime.now().astimezone().isoformat(),
        'videos':db.execute('SELECT COUNT(*) FROM videos').fetchone()[0],'comments':db.execute('SELECT COUNT(*) FROM comments').fetchone()[0],
        'items':db.execute('SELECT status,COUNT(*) FROM channel_items GROUP BY status').fetchall(),
        'runs':db.execute('SELECT status,videos_seen,videos_new,comments_new,error FROM runs').fetchall(),
        'channel':db.execute('SELECT display_name,backfill_state,list_done,total_found FROM targets').fetchall(),
        'grouped_videos':db.execute('SELECT COUNT(*) FROM group_videos').fetchone()[0]}
finally:db.close()
(dest/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
