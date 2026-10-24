"""Run the program's requested mailbox-creation path in an isolated test store."""
import argparse,datetime,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from socialscraper import accountvault
from socialscraper.net.accountproxy import scrub

ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['configured'],default='configured');ap.add_argument('--provider',default='gmx.net');a=ap.parse_args()
dest=ROOT/'data/live-audit'/f'mail-{a.provider}-{a.mode}'
dest.mkdir(parents=True,exist_ok=True)
env=dict(os.environ,PYTHONUTF8='1',SS_DB_PATH=str(dest/'scrape.db'),SS_PROFILES_DIR=str(dest/'profiles'))
env.update(SS_REQUIRE_PROXY='1',SS_AUDIT_NETWORK='proxy')
cmd=[sys.executable,'-m','socialscraper','mailkonto','neu',a.provider,'--modus','assisted']
start=time.monotonic()
proc=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
try:
    log,_=proc.communicate(timeout=170)
    result={'exit_code':proc.returncode}
except subprocess.TimeoutExpired:
    subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
    log,_=proc.communicate();result={'timeout':True,'exit_code':proc.returncode}
saved=accountvault.load(dest/'scrape.db')
log=accountvault.scrub(scrub(log),saved)
(dest/'run.log').write_text(log,encoding='utf-8')
result.update(provider=a.provider,connection=a.mode,duration_seconds=round(time.monotonic()-start,2),checked_at=datetime.datetime.now().astimezone().isoformat(),saved_mailboxes=len(saved['postfaecher']))
(dest/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2));print(log)
