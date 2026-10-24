"""Read-only form inspection with the application's actual headed browser backend."""
import datetime,json,sys,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from socialscraper.backends.local import LocalBackend
from socialscraper.net.identity import IdentityManager
from socialscraper.net.proxy import ProxyPool
from socialscraper.signup import _dismiss,youtube,instagram,tiktok
from socialscraper.mailanbieter import outlook
os.environ.update(SS_REQUIRE_PROXY='1',SS_AUDIT_NETWORK='proxy')
from socialscraper.config import load
from socialscraper.net import proxymode, testpolicy
cfg=load()
testpolicy.enable(cfg)
dest=ROOT/'data/live-audit/registration-pages-proxy-fixed';dest.mkdir(parents=True,exist_ok=True)
im=IdentityManager(proxymode.make_pool(cfg),profiles_dir=str(dest/'profiles'))
pages={'google':(youtube.REGISTER_URL,youtube.SEL),'instagram':(instagram.REGISTER_URL,instagram.SEL),'outlook':(outlook.SIGNUP_URL,outlook.SEL),'tiktok':(tiktok.REGISTER_URL,tiktok.SEL)}
for name in sys.argv[1:]:
    backend=LocalBackend(profiles_dir=str(dest/'profiles'));handle=None
    url,selectors=pages[name]
    result={'name':name,'requested_url':url,'checked_at':datetime.datetime.now().astimezone().isoformat(),
            'actual_backend':True,'headless':False,'mobile':name=='tiktok','data_entered':False,'submitted':False}
    try:
        person=im.get('audit_registration_page_'+name,'de',name=='tiktok')
        handle=backend.open(person,im.proxy_for(person),headless=False,platform=None)
        page=backend.page(handle)
        response=page.goto(url,wait_until='domcontentloaded',timeout=35000)
        page.wait_for_timeout(5000);_dismiss(page)
        result.update(http_status=response.status if response else None,final_url=page.url,title=page.title(),body_excerpt=page.inner_text('body')[:1500])
        result['fields']={k:[{'selector':s,'main_frame_count':page.locator(s).count()} for s in sels] for k,sels in selectors.items()}
        result['inputs']=page.locator('input,select,textarea').evaluate_all('(xs)=>xs.map(x=>({tag:x.tagName,type:x.type,name:x.name,id:x.id,placeholder:x.placeholder,aria_label:x.getAttribute("aria-label")})).slice(0,40)')
        result['field_html']=page.locator('input').evaluate_all('(xs)=>xs.map(x=>x.outerHTML)')
        result['labels']=page.locator('label').evaluate_all('(xs)=>xs.map(x=>x.outerHTML)')
        page.screenshot(path=str(dest/f'{name}.png'),full_page=False)
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if handle:backend.close(handle)
        from socialscraper.cli import shutdown_runtime
        shutdown_runtime(backend,None)
    (dest/f'{name}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'name':name,'url':result.get('final_url'),'title':result.get('title'),
          'found_fields':[k for k,v in result.get('fields',{}).items() if any(x['main_frame_count'] for x in v)],'error':result.get('error')},ensure_ascii=False),flush=True)
