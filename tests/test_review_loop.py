"""Review regressions; no native process, window or external service is started."""
import tempfile,unittest,os,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
from socialscraper.backends.local import LocalBackend
from socialscraper.backends.base import SessionHandle
from socialscraper.android import sdk,nox
from socialscraper.net.errors import FinalError

class BrowserCleanup(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.backend=LocalBackend(profiles_dir=folder.name)
        self.pw=MagicMock();self.backend._pw=self.pw;self.backend._users=1
        self.ctx=MagicMock();self.bridge=MagicMock()
        self.handle=SessionHandle(None,None,'local',_pw=self.pw,_context=self.ctx)
        self.handle._proxy_bridge=self.bridge

    def assert_cleaned(self):
        self.ctx.close.assert_called_once();self.bridge.close.assert_called_once();self.pw.stop.assert_called_once()
        self.assertIsNone(self.handle._context);self.assertIsNone(self.handle._pw)
        self.assertIsNone(self.handle._proxy_bridge);self.assertEqual(self.backend._users,0)

    def test_closed_output_pipe_does_not_leave_browser_or_bridge_running(self):
        with patch('socialscraper.net.browser_window.closed',side_effect=BrokenPipeError('GUI closed')):
            with self.assertRaises(BrokenPipeError):self.backend.close(self.handle)
        self.assert_cleaned()

    def test_failed_bridge_close_does_not_skip_driver_release(self):
        self.bridge.close.side_effect=OSError('bridge close failed')
        with self.assertRaises(OSError):self.backend.close(self.handle)
        self.assert_cleaned()

    def test_primary_context_failure_survives_a_secondary_cleanup_failure(self):
        self.ctx.close.side_effect=RuntimeError('primary browser failure')
        self.bridge.close.side_effect=OSError('secondary bridge failure')
        with self.assertRaisesRegex(RuntimeError,'primary browser failure') as raised:self.backend.close(self.handle)
        self.assert_cleaned();self.assertIn('secondary bridge failure',str(getattr(raised.exception,'__notes__',[])))

class AndroidCleanup(unittest.TestCase):
    def test_missing_proxy_cannot_request_windows_approval_or_prepare_a_profile(self):
        from socialscraper.android import google_mail
        with tempfile.TemporaryDirectory() as directory:
            cfg=SimpleNamespace(profiles_dir=str(Path(directory)/'profiles'));ctx=MagicMock()
            with patch.dict(os.environ,{},clear=True),patch.object(google_mail,'Profile') as profile,patch.object(google_mail,'request_guard') as guard,patch.object(google_mail,'Session',side_effect=FinalError('Proxy fehlt')):
                with self.assertRaisesRegex(FinalError,'Proxy'):google_mail.run(cfg,ctx,None)
            profile.assert_not_called();guard.assert_not_called()

    def test_shutdown_wait_has_one_shared_deadline_even_when_native_calls_are_slow(self):
        clock=[0.0];profile=MagicMock();profile.name='Nox_1'
        def slow_verify(**kw):
            clock[0]+=kw.get('timeout',60);raise OSError('ownership query timed out')
        profile.verify.side_effect=slow_verify
        def native(*args,**kw):
            clock[0]+=kw.get('timeout',60);return SimpleNamespace(returncode=1)
        session=sdk.Session(profile,MagicMock());session.launched=True;session.observed=True
        with patch.object(sdk.time,'monotonic',side_effect=lambda:clock[0]),patch.object(sdk.time,'sleep',side_effect=lambda s:clock.__setitem__(0,clock[0]+s)),patch.object(sdk.subprocess,'run',side_effect=native):
            with self.assertRaises(FinalError):session.__exit__(None,None,None)
        self.assertLessEqual(clock[0],60.5)

    def test_slow_orderly_mail_cleanup_finishes_before_the_job_forces_a_kill(self):
        from socialscraper.guicore import Job
        job=Job(['mailkonto','neu','gmail.com'],lambda _:None,lambda _:None);clock=[0]
        job.proc=SimpleNamespace(poll=lambda:0 if clock[0]>=120 else None)
        try:
            with patch('socialscraper.guicore.time.monotonic',side_effect=lambda:clock[0]),patch('socialscraper.guicore.time.sleep',side_effect=lambda _:clock.__setitem__(0,clock[0]+5)):
                self.assertTrue(job._soft_cancel())
        finally:job.cancel_file.unlink(missing_ok=True)

    def test_registration_failure_remains_visible_when_cleanup_also_fails(self):
        session=sdk.Session(MagicMock(),MagicMock());session.bridge=MagicMock()
        session.bridge.close.side_effect=OSError('bridge close failed')
        original=FinalError('registration transport failed')
        with self.assertRaises(FinalError) as raised:session.__exit__(type(original),original,None)
        self.assertIn('registration transport failed',str(raised.exception))
        self.assertIn('bridge close failed',str(raised.exception));self.assertIs(raised.exception.__cause__,original)

    def test_interruption_type_survives_a_cleanup_failure(self):
        for original in (KeyboardInterrupt(),SystemExit(130)):
            with self.subTest(kind=type(original)):
                session=sdk.Session(MagicMock(),MagicMock());session.bridge=MagicMock()
                session.bridge.close.side_effect=OSError('bridge close failed')
                with self.assertRaises(type(original)) as raised:session.__exit__(type(original),original,None)
                self.assertIs(raised.exception,original)
                self.assertIn('bridge close failed',str(getattr(original,'__notes__',[])))

    def test_owned_paused_or_stuck_guest_is_powered_off(self):
        for state in ('running','paused','stuck'):
            for ending in ('','\n','\r\n'):
                with self.subTest(state=state,ending=ending),patch.object(nox,'command',return_value='VMState="'+state+'"'+ending) as command:
                    nox.stop(name='Nox_1')
                    self.assertEqual(command.call_count,2)
                    self.assertEqual(command.call_args.args,('controlvm','Nox_1','poweroff'))

    def test_powered_off_guest_is_not_started_or_changed(self):
        with patch.object(nox,'command',return_value='VMState="poweroff"') as command:nox.stop(name='Nox_1')
        self.assertEqual(command.call_count,1)

class NodriverCleanup(unittest.TestCase):
    def test_driver_failure_survives_a_secondary_bridge_failure(self):
        from socialscraper.backends.nodriver_engine import NodriverBackend
        backend=object.__new__(NodriverBackend);backend.runner=MagicMock()
        def fail(coroutine):
            coroutine.close();raise RuntimeError('primary driver failure')
        backend.runner.run.side_effect=fail
        handle=SessionHandle(None,None,'nodriver',_context=MagicMock(),_browser=MagicMock())
        handle._proxy_bridge=MagicMock();handle._proxy_bridge.close.side_effect=OSError('secondary bridge failure')
        with self.assertRaisesRegex(RuntimeError,'primary driver failure') as raised:backend.close(handle)
        self.assertIsNone(handle._proxy_bridge)
        self.assertIn('secondary bridge failure',str(getattr(raised.exception,'__notes__',[])))

class ManualCredentials(unittest.TestCase):
    def test_invalid_credentials_are_rejected_without_changing_or_sending_them(self):
        from socialscraper.android import google_mail
        for value in ('','A'*257,'FAKE\r\nVALUE',None,True):
            adb=MagicMock();ctx=SimpleNamespace(adresse='test@gmail.com',passwort='GENERATED',account_confirmed=True,password_filled=False)
            with self.assertRaises(ValueError):google_mail.apply_input(adb,{'action':'confirm_password','value':value},100,100,ctx)
            self.assertEqual(ctx.passwort,'GENERATED');adb.sh.assert_not_called();adb.tippen.assert_not_called()

    def test_confirmation_rejects_a_removed_or_different_target_account(self):
        from socialscraper.android import google_mail
        for raw in ('','Account {name=other@gmail.com, type=com.google}'):
            adb=MagicMock();adb.sh.return_value=raw
            ctx=SimpleNamespace(adresse='test@gmail.com',passwort='GENERATED',account_confirmed=True,password_filled=False)
            with self.assertRaises(ValueError):google_mail.apply_input(adb,{'action':'confirm_password','value':'LOCAL_FAKE_SECRET'},100,100,ctx)
            self.assertEqual(ctx.passwort,'GENERATED');self.assertFalse(getattr(ctx,'password_confirmed_by_user',False))

    def test_confirmation_is_rejected_until_the_target_account_exists(self):
        from socialscraper.android import google_mail
        adb=MagicMock();ctx=SimpleNamespace(adresse='test@gmail.com',passwort='GENERATED',account_confirmed=False,password_filled=False)
        with self.assertRaises(ValueError):google_mail.apply_input(adb,{'action':'confirm_password','value':'LOCAL_FAKE_SECRET'},100,100,ctx)
        self.assertEqual(ctx.passwort,'GENERATED');adb.sh.assert_not_called()

    def test_confirmation_checks_current_account_and_does_not_type_the_secret(self):
        from socialscraper.android import google_mail
        adb=MagicMock();adb.sh.return_value='Account {name=test@gmail.com, type=com.google}'
        ctx=SimpleNamespace(adresse='test@gmail.com',passwort='GENERATED',account_confirmed=True,password_filled=False)
        google_mail.apply_input(adb,{'action':'confirm_password','value':'LOCAL_FAKE_SECRET'},100,100,ctx)
        self.assertEqual(ctx.passwort,'LOCAL_FAKE_SECRET');self.assertTrue(ctx.password_confirmed_by_user)
        self.assertFalse(ctx.password_filled);adb.tippen.assert_not_called();adb.tap.assert_not_called()
        adb.sh.assert_called_once_with('dumpsys','account',timeout=5)
        google_mail.apply_input(adb,{'action':'text','value':'ordinary input'},100,100,ctx)
        self.assertFalse(ctx.password_confirmed_by_user)

    def test_manual_completion_waits_for_local_confirmation_instead_of_aborting(self):
        from socialscraper.android import google_mail
        from test_android_robustness import png
        with tempfile.TemporaryDirectory() as directory:
            clock=[0.0];adb=MagicMock();adb._cmd.return_value=png()
            def shell(*args,**kw):
                if args[:2]==('pm','path'):return 'package:google.apk'
                if args==('dumpsys','account'):return 'Account {name=test@gmail.com, type=com.google}'
                return ''
            adb.sh.side_effect=shell
            session=MagicMock();session.__enter__.return_value.adb=adb
            ctx=SimpleNamespace(timeout=5,stop=None,melden=MagicMock(),hinweis=MagicMock(),adresse='test@gmail.com',vorname='Test',nachname='Person',passwort='GENERATED',password_filled=False)
            cfg=SimpleNamespace(profiles_dir=str(Path(directory)/'profiles'));stages=[]
            def status(folder,stage,*args):
                stages.append(stage)
                if stage=='credentials_required':(folder/'inputs/confirm.json').write_text(json.dumps({'action':'confirm_password','value':'LOCAL_FAKE_SECRET'}))
            with patch.dict(os.environ,{},clear=True),patch.object(google_mail,'Profile'),patch.object(google_mail,'request_guard'),patch.object(google_mail,'Session',return_value=session),patch.object(google_mail,'event'),patch.object(google_mail,'observe_form',return_value=('manual',False)),patch.object(google_mail,'write_stage',side_effect=status),patch('socialscraper.android.language.ensure_german'),patch.object(google_mail.time,'monotonic',side_effect=lambda:clock[0]),patch.object(google_mail.time,'sleep',side_effect=lambda s:clock.__setitem__(0,clock[0]+s)):
                result=google_mail.run(cfg,ctx,MagicMock())
            self.assertEqual(result['adresse'],'test@gmail.com');self.assertIn('credentials_required',stages)
            self.assertEqual(ctx.passwort,'LOCAL_FAKE_SECRET');self.assertNotIn('LOCAL_FAKE_SECRET',str(ctx.melden.call_args_list)+str(ctx.hinweis.call_args_list)+str(result))
            session.__exit__.assert_called_once()

    def test_pool_stores_the_confirmed_password_not_the_original_generated_value(self):
        from socialscraper import mailanbieter,accountvault
        from socialscraper.android import google_mail
        from socialscraper.config import Settings
        with tempfile.TemporaryDirectory() as directory:
            cfg=Settings(db_path=str(Path(directory)/'scrape.db'),profiles_dir=str(Path(directory)/'profiles'))
            def registered(_cfg,ctx,_proxy):
                ctx.account_confirmed=True;ctx.password_confirmed_by_user=True
                ctx.passwort='LOCAL_FAKE_SECRET';return {'adresse':ctx.adresse,'hinweis':'IMAP benötigt gegebenenfalls ein separates App-Kennwort.'}
            with patch.object(google_mail,'run',side_effect=registered),patch.object(accountvault,'add_postfach') as save,patch('socialscraper.mailbox.build') as mailbox_build:
                mailbox_build.return_value.check_access.return_value=0
                manager=MagicMock()
                from socialscraper.net.proxy import Proxy
                manager.proxy_for.return_value=Proxy.parse('http://127.0.0.1:9')
                from profile_fixture import REGISTRATION
                result=mailanbieter.create(cfg,'gmail.com',adresse='test@gmail.com',passwort='GENERATED',route='android',im=manager,melden=MagicMock(),registrierungsdaten=REGISTRATION)
            self.assertEqual(save.call_args.args[1]['passwort'],'LOCAL_FAKE_SECRET')
            self.assertIn('IMAP benötigt gegebenenfalls ein separates App-Kennwort.',save.call_args.args[1]['notizen'])
            self.assertNotIn('LOCAL_FAKE_SECRET',str(result))

class InstagramTransport(unittest.TestCase):
    """Exercise the installed library through Requests' transport, without sockets."""

    def test_account_proxy_survives_library_session_copies_and_environment(self):
        import instaloader,requests
        from socialscraper.adapters.base import ScrapeContext
        from socialscraper.adapters.instagram import InstagramAdapter
        from socialscraper.channel_list import ListOptions
        from socialscraper.config import Settings
        from socialscraper.net.proxy import Proxy
        cookies=[{'name':'sessionid','value':'FAKE_SESSION','domain':'.instagram.com'},
                 {'name':'csrftoken','value':'FAKE_CSRF','domain':'.instagram.com'}]
        for operation in ('fetch','channel'):
            for source in ('cookies','file'):
                for environment in ('bypass','other_proxy'):
                    with self.subTest(operation=operation,source=source,environment=environment):
                        loader=instaloader.Instaloader(sleep=False,quiet=True,max_connection_attempts=1)
                        self.addCleanup(loader.close)
                        loader.context._rate_controller=MagicMock()
                        cfg=Settings(max_comments=0,max_retries=1,
                                     ig_session_user='test_user' if source=='file' else '')
                        im=MagicMock();im.proxy_for.return_value=Proxy.parse('socks5://127.0.0.1:31234')
                        ctx=ScrapeContext(cfg,None,im,identity='test_identity')
                        captured=[]
                        def send(adapter,request,**kwargs):
                            captured.append((request.url,dict(kwargs.get('proxies') or {})))
                            response=requests.Response();response.status_code=200;response.url=request.url
                            response._content=b'{"status":"ok","data":{"user":{"username":"test_user"}}}'
                            response.headers['Content-Type']='application/json'
                            return response
                        def read(*args):
                            # These paths each create a fresh Requests session internally.
                            loader.context.doc_id_graphql_query('fake_doc',{})
                            loader.context.get_iphone_json('api/v1/fake/',{})
                            with loader.context.anonymous_copy():
                                loader.context.get_json('fake/',{})
                                # A missing CSRF cookie also causes a direct GET on
                                # the new root session before get_json is called.
                                loader.context.doc_id_graphql_query('anonymous_doc',{})
                            return SimpleNamespace(mediacount=0,full_name='Test',is_private=False,get_posts=lambda:iter(()))
                        def load_file(*args):loader.load_session('test_user',{'sessionid':'FAKE_SESSION','csrftoken':'FAKE_CSRF'})
                        env={key:'' for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy','NO_PROXY','no_proxy')}
                        if environment=='bypass':env.update(NO_PROXY='*',no_proxy='*')
                        else:env.update(HTTPS_PROXY='http://127.0.0.1:31235',https_proxy='http://127.0.0.1:31235')
                        adapter=InstagramAdapter()
                        with patch.dict(os.environ,env),patch.object(instaloader,'Instaloader',return_value=loader),patch.object(loader,'load_session_from_file',side_effect=load_file),patch('socialscraper.net.cookies.CookieJar.load',return_value=cookies),patch.object(requests.adapters.HTTPAdapter,'send',send),patch.object(instaloader.Post,'from_shortcode',side_effect=read),patch.object(instaloader.Profile,'from_username',side_effect=read),patch.object(adapter,'_read_post',return_value='POST'):
                            if operation=='fetch':self.assertEqual(adapter.fetch('https://instagram.com/p/A/',ctx),'POST')
                            else:self.assertTrue(adapter.channel_items('test_profile',ctx,ListOptions()).exhausted)
                        self.assertEqual(len(captured),6)
                        for url,routes in captured:
                            self.assertEqual(routes.get('https'),'socks5h://127.0.0.1:31234',url)
                        self.assertTrue(ctx.authenticated)

    def test_proxy_failure_cannot_fall_back_to_a_direct_post_request(self):
        import instaloader,requests
        from socialscraper.adapters.base import ScrapeContext
        from socialscraper.adapters.instagram import InstagramAdapter
        from socialscraper.config import Settings
        from socialscraper.net.proxy import Proxy
        from socialscraper.net.errors import NetworkError
        from socialscraper.net.session_guard import SessionExpired
        from socialscraper import rotation
        loader=instaloader.Instaloader(sleep=False,quiet=True,max_connection_attempts=1)
        self.addCleanup(loader.close);loader.context._rate_controller=MagicMock()
        im=MagicMock();im.proxy_for.return_value=Proxy.parse('http://127.0.0.1:31234')
        ctx=ScrapeContext(Settings(max_retries=1),None,im)
        cookies=[{'name':'sessionid','value':'FAKE_SESSION','domain':'.instagram.com'},
                 {'name':'csrftoken','value':'FAKE_CSRF','domain':'.instagram.com'}]
        captured=[]
        error=requests.exceptions.ProxyError('Unable to connect to proxy 127.0.0.1:31234: connection refused')
        def send(adapter,request,**kwargs):
            captured.append(kwargs.get('proxies'))
            raise error
        with patch.object(instaloader,'Instaloader',return_value=loader),patch('socialscraper.net.cookies.CookieJar.load',return_value=cookies),patch.object(requests.adapters.HTTPAdapter,'send',send),patch.object(instaloader.Post,'from_shortcode') as post:
            for error,expected in ((error,'proxy'),(requests.exceptions.ConnectTimeout('Connection timed out'),None),
                                   (requests.exceptions.SSLError('SSL certificate verify failed'),None)):
                with self.subTest(error=type(error).__name__):
                    with self.assertRaises(NetworkError) as raised:InstagramAdapter().fetch('https://instagram.com/p/A/',ctx)
                    self.assertNotIsInstance(raised.exception,SessionExpired)
                    self.assertEqual(rotation.classify(raised.exception,has_proxy=True),expected)
        self.assertEqual(len(captured),3)
        self.assertTrue(all(p=={'http':'http://127.0.0.1:31234','https':'http://127.0.0.1:31234'} for p in captured))
        post.assert_not_called();self.assertFalse(ctx.authenticated)

if __name__=='__main__':unittest.main()
