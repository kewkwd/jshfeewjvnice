"""Inspect and exercise the date controls, without completing signup."""
import json, os, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));os.chdir(ROOT)
os.environ.update(SS_REQUIRE_PROXY='1',SS_AUDIT_NETWORK='proxy',SS_NOTIFY_WEBHOOK='')
sys.stdout.reconfigure(encoding='utf-8')
from socialscraper.config import load
from socialscraper.net import testpolicy, proxymode
from socialscraper.net.identity import IdentityManager
from socialscraper.backends.local import LocalBackend
from socialscraper.signup import _fill, _click, _select, _google_start
from socialscraper.mailanbieter.gmail import SEL, SIGNUP_URL
from types import SimpleNamespace
cfg=load();testpolicy.enable(cfg)
destination=ROOT/'data/live-audit/google-controls';destination.mkdir(parents=True,exist_ok=True)
im=IdentityManager(proxymode.make_pool(cfg),profiles_dir=str(destination/'profiles'))
backend=LocalBackend(profiles_dir=str(destination/'profiles'))
person=im.get('google_controls_proxy','de',False)
result={'proxy_required':True,'registration_completed':False}
try:
    with backend.session(person,im.proxy_for(person),headless=False,platform='mail') as handle:
        page=backend.page(handle)
        page.goto(SIGNUP_URL,wait_until='domcontentloaded',timeout=35000)
        ctx=SimpleNamespace(fill=lambda sels,value:_fill(page,sels,value),
                            click=lambda sels:_click(page,sels),warte=page.wait_for_timeout)
        _google_start(page,ctx,SEL['vorname'],'Social')
        _fill(page,SEL['nachname'],'Scraper');_click(page,SEL['weiter']);page.wait_for_timeout(2500)
        result['controls']=page.locator('select,[role="combobox"],[role="listbox"]').evaluate_all(
            '(xs)=>xs.map(x=>({tag:x.tagName,id:x.id,role:x.getAttribute("role"),ariaLabel:x.getAttribute("aria-label")}))')
        result['month_selected']=_select(page,SEL['monat'],2)
        result['gender_selected']=_select(page,SEL['geschlecht'],3)
        result['labels']={name:next((page.locator(s).first.inner_text() for s in sels if page.locator(s).count()),None)
                          for name,sels in (('month',SEL['monat']),('gender',SEL['geschlecht']))}
        if '--route' in sys.argv:
            _fill(page,SEL['tag'],'1');_fill(page,SEL['jahr'],'1995')
            _click(page,SEL['weiter']);page.wait_for_timeout(2500)
            result['route_controls']=page.locator('button,a,[role="button"]').evaluate_all(
                '(xs)=>xs.map(x=>({text:x.innerText,tag:x.tagName,role:x.getAttribute("role")}))')
            from socialscraper.mailanbieter.gmail import _credentials
            state={'username_submitted':False,'choice_selected':False}
            _credentials(page,ctx,state);page.wait_for_timeout(1500)
            result['creation_route_selected']=bool(state.get('email_route_selected'))
            result['route_path']=__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(page.url).path
            result['route_fields']=page.locator('input').evaluate_all('(xs)=>xs.map(x=>({name:x.name,id:x.id,type:x.type}))')
        # Stop here: no date submission, phone/CAPTCHA or final account creation.
except Exception as error:
    from socialscraper.problemlogic import scrub
    result['error']=scrub(type(error).__name__+': '+str(error))
finally:
    (ROOT.parents[1]/'outputs/google-formular-proxy-runde10.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False),flush=True)
