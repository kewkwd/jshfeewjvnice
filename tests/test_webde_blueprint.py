"""Whole public mailbox workflow with real DOM and simulated IMAP transport.

Every website is a routed fixture. All unhandled requests are aborted and Chrome
has a dead loopback proxy. No real CAPTCHA, provider, user data or mailbox exists.
"""
import contextlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from urllib.parse import urlsplit,parse_qs
import unittest
from unittest.mock import patch, Mock

from socialscraper import accountvault, mailanbieter, mailbox, privacy_consent, registration_attempts
from socialscraper.backends.base import BrowserBackend, SessionHandle
from socialscraper.backends.local import LocalBackend
from socialscraper.config import Settings
from socialscraper.net.errors import FinalError
from socialscraper.net.proxy import Proxy

ADDRESS='blueprint@web.de'
PASSWORD='OFFLINE_FIXTURE_Only!7'
DETAILS={'vorname':'Offline','nachname':'Musterperson','geburtsdatum':'1995-01-01','geschlecht':'keine Angabe',
    'land':'DE','plz':'00000','ort':'Musterstadt','strasse':'Musterstrasse','hausnummer':'1','anrede':'Herr'}

REGISTRATION=r"""
<h1>OFFLINE TEST REGISTRATION</h1><main id="flow"></main>
<script>
const flow=document.getElementById('flow');
const expected=__EXPECTED__;
let stage='';
function recordStep(name){fetch('https://fixture.invalid/event?name='+encodeURIComponent(name));}
function render(next){
 stage=next;recordStep('screen:'+next);
 if(next==='name') flow.innerHTML=`<form id="person"><input name="firstName"><input name="lastName">
 <select name="birthDay"><option value="">Day</option><option value="1">1</option></select>
 <select name="birthMonth"><option value="">Month</option><option value="1">Januar</option></select>
 <input name="birthYear"><div id="captcha">OFFLINE manual challenge</div><button type="submit">Jetzt starten</button></form>`;
 if(next==='alias') flow.innerHTML=`<form id="alias-step"><div class="alias-wrap"><input id="alias" name="alias"></div>
 <input id="domain" readonly value="web.de"><button type="submit" data-testid="next-button">Weiter</button></form>`;
 if(next==='address') flow.innerHTML=`<form id="address"><select name="salutation"><option value="">Select</option><option>Herr</option></select>
 <input name="zip"><input name="city"><input name="street"><input name="houseNumber">
 <select name="country"><option value="">Country</option><option value="DE">Deutschland</option></select><button type="submit">Weiter</button></form>`;
 if(next==='password') flow.innerHTML=`<form id="password"><input type="password" name="password"><input type="password" name="confirmPassword">
 <label><input type="checkbox" name="agb">OFFLINE agreement fixture</label><button type="submit">Registrieren</button></form>`;
 flow.querySelector('form').onsubmit=e=>{
   e.preventDefault();
   if(stage==='name'){
     if(value('firstName')!=='Offline'||value('lastName')!=='Musterperson'||value('birthDay')!=='1'||value('birthMonth')!=='1'||value('birthYear')!=='1995')return;
     recordStep('name:verified');render('alias');
   } else if(stage==='alias'){
     if(value('alias')!==expected.alias)return;
     const wrap=flow.querySelector('.alias-wrap');
     if(!wrap.classList.contains('alias-step__form-element--available')){
       wrap.classList.add('alias-step__form-element--available');recordStep('alias:checked');
     } else {recordStep('alias:selected');render('address');}
   } else if(stage==='address'){
     if(value('zip')!=='00000'||value('city')!=='Musterstadt'||value('street')!=='Musterstrasse'||value('houseNumber')!=='1'||value('country')!=='DE'||value('salutation')!=='Herr')return;
     recordStep('address:verified');render('password');
   } else if(stage==='password'){
     if(value('password')!==expected.password||value('confirmPassword')!==expected.password||!flow.querySelector('[name=agb]').checked)return;
     recordStep('password:verified');recordStep('agreement:manual');
     flow.innerHTML='<h1>Dein Postfach ist bereit.</h1><p>'+expected.address+'</p>';
     recordStep('account:confirmed');stage='complete';
   }
 };
}
function value(name){return flow.querySelector('[name="'+name+'"]')?.value||'';}
// A test-only actor stands in for a human; production code never receives it.
setInterval(()=>{
 if(!expected.actor)return;
 if(stage==='name'&&value('firstName')==='Offline'&&value('lastName')==='Musterperson'&&value('birthDay')==='1'&&value('birthMonth')==='1'&&value('birthYear')==='1995'){
   flow.querySelector('#captcha')?.remove();recordStep('captcha:manual');flow.querySelector('button').click();
 }
 if(stage==='password'&&window.fixtureActorPasswordReady&&value('password')===expected.password&&value('confirmPassword')===expected.password){
   flow.querySelector('[name=agb]').checked=true;flow.querySelector('button').click();
 }
},150);
render('name');
</script>
"""

class FixtureBackend(BrowserBackend):
    def __init__(self,browser,*,products=True,actor=True,foreign=False):
        self.browser=browser;self.products=products;self.actor=actor;self.foreign=foreign
        self.visits=[];self.events=[];self.opens=0;self.closed=0;self.unhandled=[]

    def open(self,identity,proxy,**kwargs):
        self.opens+=1
        context=self.browser.new_context(service_workers='block',accept_downloads=False)
        context.route('**/*',self.route)
        page=context.new_page();page.set_default_timeout(1500)
        page.on('pageerror',lambda error:self.events.append('fixture-js-error:'+str(error)))
        return SessionHandle(identity,proxy,'fixture',_context=context)

    def close(self,handle):
        self.closed+=1;handle._context.close()

    def route(self,route):
        url=route.request.url;self.visits.append(url)
        if urlsplit(url).hostname=='fixture.invalid' and urlsplit(url).path=='/event':
            self.events.append(parse_qs(urlsplit(url).query)['name'][0])
            route.fulfill(status=204,headers={'Access-Control-Allow-Origin':'*'})
            return
        if url=='https://www.google.de/':
            html='<form action="/search"><input name="q"><button>Search</button></form>'
        elif url=='https://www.google.de/search?q=web.de':
            html='<div id="captcha-form">OFFLINE challenge</div><section hidden><a href="https://web.de/"><h3>WEB.DE</h3></a></section><script>setTimeout(()=>{document.querySelector("#captcha-form").remove();document.querySelector("section").hidden=false;fetch("https://fixture.invalid/event?name=google:manual")},200)</script>'
        elif url=='https://web.de/':
            html='<script>location.replace("/consent-management/")</script>'
        elif url=='https://web.de/consent-management/':
            html='<p>Cookies</p><button onclick="location.href=\'/?consent=ok\'">Alle akzeptieren</button>'
        elif url=='https://web.de/?consent=ok':
            target='https://produkte.web.de/premium/tarifevergleich/' if self.products else 'https://web.de/email/tarifvergleich/'
            html='<a href="'+target+'">Tarifvergleich</a>'
        elif url in ('https://produkte.web.de/premium/tarifevergleich/','https://web.de/email/tarifvergleich/'):
            if self.foreign:
                html='<script>location.href="https://foreign.example/"</script>'
            else:
                html='<a href="https://register.web.de/?edition=paid">Premium</a><a href="https://register.web.de/">FreeMail</a>'
        elif url=='https://register.web.de/':
            html=REGISTRATION.replace('__EXPECTED__',json.dumps({'alias':'blueprint','address':ADDRESS,'password':PASSWORD,'actor':self.actor}))
        elif url=='https://foreign.example/':html='<p>Foreign destination fixture</p>'
        else:
            self.unhandled.append(url);route.abort();return
        route.fulfill(status=200,content_type='text/html',body=html)

class ImapTransport:
    """Only the IMAP transport is substituted; the application's reader runs."""
    calls=[];reject=False
    def __init__(self,host,port,**kwargs):self.calls.append('connect')
    def login(self,user,password):
        assert user==ADDRESS and password==PASSWORD
        self.calls.append('login');return 'OK',[]
    def select(self,folder,readonly=False):
        assert folder=='INBOX' and readonly
        self.calls.append('select');return 'OK',[]
    def search(self,*args):
        self.calls.append('search')
        return ('NO',[]) if self.reject else ('OK',[b'1'])
    def fetch(self,*args):
        self.calls.append('fetch')
        return 'OK',[(b'1 RFC822',b'From: test@example.invalid\r\nSubject: Offline blueprint\r\n\r\nFixture message')]
    def logout(self):self.calls.append('logout')

@unittest.skipUnless(os.environ.get('SS_RUN_WEBDE_DOM')=='1','offline whole-browser fixture disabled')
class WebdeBlueprint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from patchright.sync_api import sync_playwright
        cls.pw=sync_playwright().start()
        proxy={'server':'http://127.0.0.1:9'}
        cls.browser=cls.pw.chromium.launch(channel='chrome',headless=True,proxy=proxy,
            args=LocalBackend._proxy_args(proxy)+['--disable-background-networking'])
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.pw.stop()
    def setUp(self):
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.cfg=Settings(db_path=str(self.root/'data/test.db'),profiles_dir=str(self.root/'profiles'))
        privacy_consent.save(self.cfg.db_path,True)
        self.proxy=Proxy.parse('http://127.0.0.1:9')
        self.im=SimpleNamespace(get=lambda name,*args:SimpleNamespace(name=name),proxy_for=lambda person:self.proxy)
        ImapTransport.calls=[];ImapTransport.reject=False
        self.stack.enter_context(patch.object(mailbox.imaplib,'IMAP4_SSL',ImapTransport))
        self.mail_build=self.stack.enter_context(patch.object(mailbox,'build',wraps=mailbox.build))
        self.messages=[]
    def run_workflow(self,backend,**kwargs):
        original_fill=mailanbieter.MailCtx.fill
        def fill_then_allow_actor(ctx,selectors,value):
            result=original_fill(ctx,selectors,value)
            # The fixture's human actor submits only after the real final-field
            # fill (including credential tracking) has returned. A 150ms timer
            # alone could remove the form while that call was still running.
            if result and 'input[name="confirmPassword"]' in selectors:
                backend.page(ctx.handle).evaluate('window.fixtureActorPasswordReady=true',isolated_context=False)
            return result
        try:
            with patch.object(mailanbieter.MailCtx,'fill',fill_then_allow_actor):
                return mailanbieter.create(self.cfg,'web.de',adresse=ADDRESS,passwort=PASSWORD,
                    registrierungsdaten=DETAILS,backend=backend,im=self.im,mode='assisted',
                    timeout=int(os.environ.get('SS_BLUEPRINT_TIMEOUT','45')),melden=self.messages.append,**kwargs)
        except Exception as error:
            error.add_note('OFFLINE visits='+repr(backend.visits)+'; events='+repr(backend.events))
            raise
    def assert_complete(self,backend):
        self.assertEqual(len(accountvault.postfaecher(self.cfg.db_path)),1)
        self.assertEqual(registration_attempts._load(self.cfg.db_path)['drafts'],{})
        self.assertEqual(backend.opens,1);self.assertEqual(backend.closed,1)
        self.assertEqual(backend.unhandled,[])
        for event in ('google:manual','captcha:manual','name:verified','alias:checked','alias:selected','address:verified','password:verified','agreement:manual','account:confirmed'):
            self.assertIn(event,backend.events,event)
        self.assertEqual(ImapTransport.calls,['connect','login','select','search','fetch','logout'])
        self.assertEqual(self.mail_build.call_args.kwargs['proxy'].url,self.proxy.url)
        self.assertFalse(any('edition=paid' in url for url in backend.visits))
    def test_complete_current_products_path(self):
        backend=FixtureBackend(self.browser,products=True)
        self.run_workflow(backend);self.assert_complete(backend)
    def test_complete_legacy_tariff_path(self):
        backend=FixtureBackend(self.browser,products=False)
        self.run_workflow(backend);self.assert_complete(backend)
    def test_mail_failure_resumes_only_mail_without_reopening_browser(self):
        backend=FixtureBackend(self.browser,products=False)
        ImapTransport.reject=True
        with self.assertRaisesRegex(FinalError,'Mailabruf'):self.run_workflow(backend)
        self.assertEqual(accountvault.postfaecher(self.cfg.db_path),[])
        self.assertIsNotNone(registration_attempts.pending(self.cfg.db_path,'web.de',ADDRESS))
        ImapTransport.reject=False;ImapTransport.calls=[]
        self.run_workflow(backend);self.assert_complete(backend)
    def test_unfinished_manual_challenge_cancels_without_account_or_mail_read(self):
        backend=FixtureBackend(self.browser,products=False,actor=False)
        with self.assertRaisesRegex(RuntimeError,'Abgebrochen'):
            self.run_workflow(backend,stop=lambda:'screen:name' in backend.events)
        self.assertEqual(accountvault.postfaecher(self.cfg.db_path),[])
        self.assertEqual(ImapTransport.calls,[])
        self.assertEqual(backend.closed,1)
    def test_external_target_is_rejected_without_account_or_mail_read(self):
        backend=FixtureBackend(self.browser,products=False,foreign=True)
        with self.assertRaisesRegex(FinalError,'HTTPS'):self.run_workflow(backend)
        self.assertEqual(accountvault.postfaecher(self.cfg.db_path),[])
        self.assertEqual(ImapTransport.calls,[])

    def test_failed_entry_keeps_owned_window_for_review_then_requires_review(self):
        backend=FixtureBackend(self.browser,products=False,foreign=True)
        with self.assertRaisesRegex(FinalError,'HTTPS'):self.run_workflow(backend,failure_window=1)
        self.assertTrue(any('review_required' in m for m in self.messages))
        self.assertEqual(backend.closed,1);self.assertEqual(ImapTransport.calls,[])
        draft=next(iter(registration_attempts._load(self.cfg.db_path)['drafts'].values()))
        self.assertEqual(draft['status'],'needs_review')
        from socialscraper.failure_reports import latest
        self.assertIn('keine automatische neue Registrierung',latest(self.cfg.db_path,'web.de')['next'])
        self.assertEqual(accountvault.postfaecher(self.cfg.db_path),[])

    def test_cancel_during_review_never_creates_a_success(self):
        backend=FixtureBackend(self.browser,products=False,foreign=True)
        with self.assertRaisesRegex(FinalError,'Abgebrochen'):
            self.run_workflow(backend,failure_window=5,stop=lambda:any('review_required' in m for m in self.messages))
        self.assertEqual(backend.closed,1);self.assertEqual(ImapTransport.calls,[])
        self.assertEqual(accountvault.postfaecher(self.cfg.db_path),[])
