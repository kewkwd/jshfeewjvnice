"""Isolated interactive desktop test; never falls back to a direct connection."""
import argparse,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));os.chdir(ROOT)
parser=argparse.ArgumentParser()
parser.add_argument('--provider',choices=('gmail.com','web.de','gmx.net','outlook.com'),default='gmail.com')
parser.add_argument('--mailbox',action='store_true');parser.add_argument('--android',action='store_true')
args=parser.parse_args()
if args.android and args.provider!='gmail.com':parser.error('Android ist nur für Gmail verfügbar.')
destination=ROOT/'data/manual-proxy-tests'
destination.mkdir(parents=True,exist_ok=True)
os.environ.update(SS_REQUIRE_PROXY='1',SS_AUDIT_NETWORK='proxy',SS_NOTIFY_WEBHOOK='',
    SS_DB_PATH=str(destination/'scrape.db'),SS_PROFILES_DIR=str(destination/'profiles'),
    PYTHONUTF8='1',PYTHONUNBUFFERED='1')
from socialscraper.config import load
from socialscraper.net.testpolicy import enable
enable(load())
from socialscraper.gui import _windows_setup
from socialscraper.ui.app import App
_windows_setup()
if os.name=='nt':
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Socialscraper.ProxyTest')
app=App()
suffix='feste Proxy-Sitzung' if os.environ.get('SS_TEST_FIXED_PROXY')=='1' else 'Proxy-Testumgebung'
app.title('Socialscraper – '+args.provider+' – '+suffix)
app.select_tab('Postfächer')
app.tabs['Postfächer'].provider.set(args.provider)
if args.android:app.tabs['Postfächer'].route.set('Android (Gmail)')
if args.mailbox:
    def mailbox_finished(results,cancelled):
        app.tabs['Postfächer'].refresh()
        app.set_status('Testpostfach-Auftrag beendet. Ergebnis unter Postfächer und Probleme prüfen.')
    app.after(500,lambda:app.run_steps([
        ('Testpostfach registrieren',['mailkonto','neu',args.provider,'--modus','assisted','--weg','android' if args.android else 'browser'])],
        mailbox_finished))
app.mainloop()
