import contextlib
import io
import pathlib
import tempfile
import unittest
from unittest.mock import patch, Mock
from socialscraper import cli
from socialscraper.config import Settings
from socialscraper.models import Video
from socialscraper.net.session_guard import State
import os
os.environ.setdefault('SS_PACE', 'off')  # keine Zufallspausen zwischen Abrufen in Tests

class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg=Settings(db_path=str(pathlib.Path(self.tmp.name)/'scrape.db'),notify_file='')
        self.adapter=Mock(platform='tiktok',needs_browser=True,needs_auth=True)
        self.adapter.fetch.return_value=(Video('tiktok','1','https://www.tiktok.com/@user/video/1',comment_count=0),[])

    def run_cli(self,args,state=State.OK):
        with patch.object(cli,'load',return_value=self.cfg),patch.object(cli,'resolve',return_value=self.adapter), \
             patch.object(cli,'_ctx_and_backend',return_value=(Mock(),Mock(),Mock())), \
             patch.object(cli,'ensure',return_value=state),contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_profile_has_scrape_flags_and_accepts_zero_comments(self):
        target=pathlib.Path(self.tmp.name)/'export'/'nested'
        with patch('socialscraper.adapters.tiktok.TikTokAdapter.profile_urls',return_value=['https://www.tiktok.com/@user/video/1']):
            code=self.run_cli(['profile','user','--max-comments','0','--no-snapshot','--export',str(target)])
        self.assertEqual(code,0);self.assertEqual(self.cfg.max_comments,0);self.assertFalse(self.cfg.snapshot)
        self.assertTrue((target/'videos.jsonl').exists())

    def test_require_auth_refuses_unknown_probe_before_fetch(self):
        self.assertEqual(self.run_cli(['scrape','https://www.tiktok.com/@user/video/1','--require-auth'],State.ERROR),3)
        self.adapter.fetch.assert_not_called()

    def test_public_mode_does_not_claim_authenticated(self):
        self.assertEqual(self.run_cli(['scrape','https://www.tiktok.com/@user/video/1'],State.MISSING),0)
        ctx=self.adapter.fetch.call_args.args[1];self.assertFalse(ctx.authenticated)

    def test_old_direct_url_command_still_works(self):
        self.assertEqual(self.run_cli(['https://www.tiktok.com/@user/video/1']),0)

    def test_configuration_require_auth_is_preserved(self):
        self.cfg.require_auth=True
        self.assertEqual(self.run_cli(['scrape','https://www.tiktok.com/@user/video/1'],State.MISSING),3)

    def test_login_passes_config_and_configures_notifications(self):
        with patch.object(cli,'load',return_value=self.cfg),patch.object(cli,'notify') as notify, \
             patch.object(cli,'_ctx_and_backend',return_value=(Mock(),Mock(),Mock())), \
             patch('socialscraper.login.login',return_value=True) as login:
            self.assertEqual(cli.main(['login','--mode','qr']),0)
            self.assertIs(login.call_args.kwargs['cfg'],self.cfg)
            notify.configure.assert_called_once_with(self.cfg)

    def test_probe_uses_actual_adapter_platform(self):
        instagram=Mock(platform='instagram',needs_browser=True,needs_auth=True)
        instagram.fetch.return_value=(Video('instagram','2','https://instagram.com/reel/2',comment_count=0),[])
        with patch.object(cli,'load',return_value=self.cfg),patch.object(cli,'resolve',side_effect=lambda u: instagram if 'instagram' in u else self.adapter), \
             patch.object(cli,'_ctx_and_backend',return_value=(Mock(),Mock(),Mock())), \
             patch.object(cli,'ensure',return_value=State.OK) as ensure,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['scrape','https://instagram.com/reel/2','https://www.tiktok.com/@user/video/1']),0)
            self.assertEqual([c.args[3] for c in ensure.call_args_list],['instagram','tiktok'])

class IdentityAndLogoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=pathlib.Path(self.tmp.name)
        self.cfg=Settings(db_path=str(root/'scrape.db'),notify_file='',profiles_dir=str(root/'profiles'))
        from socialscraper.net import cookies
        patcher=patch.object(cookies,'BASE',root/'cookies');patcher.start();self.addCleanup(patcher.stop)

    def args(self,argv):
        args=cli.parser().parse_args(argv);cli._resolve_identity(self.cfg,args);return args

    def test_saved_identity_supplies_country_and_device(self):
        from socialscraper.net.identity import IdentityManager
        IdentityManager(None,profiles_dir=self.cfg.profiles_dir).get('tiktok_main','de',False)
        args=self.args(['check','--identity','tiktok_main'])
        self.assertEqual((args.country,args.mobile),('de',False))

    def test_new_identity_defaults_and_explicit_values(self):
        args=self.args(['check','--identity','fresh'])
        self.assertEqual((args.country,args.mobile),('us',False))
        args=self.args(['check','--identity','fresh','--country','FR','--mobile'])
        self.assertEqual((args.country,args.mobile),('fr',True))

    def test_logout_removes_cookies_and_browser_profile(self):
        from socialscraper.net.cookies import CookieJar
        CookieJar.save('tiktok_main','tiktok',[{'name':'sessionid','value':'x','domain':'.tiktok.com'}])
        profile=pathlib.Path(self.cfg.profiles_dir)/'tiktok_main'/'tiktok';(profile/'Default').mkdir(parents=True)
        other=pathlib.Path(self.cfg.profiles_dir)/'tiktok_main'/'instagram';other.mkdir(parents=True)
        with patch.object(cli,'load',return_value=self.cfg),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['logout','--platform','tiktok']),0)
        self.assertFalse(profile.exists());self.assertTrue(other.exists())
        self.assertFalse(CookieJar.path('tiktok_main','tiktok').exists())

    def test_locked_profile_keeps_cookies(self):
        from socialscraper.net.cookies import CookieJar
        CookieJar.save('tiktok_main','tiktok',[{'name':'sessionid','value':'x','domain':'.tiktok.com'}])
        (pathlib.Path(self.cfg.profiles_dir)/'tiktok_main'/'tiktok').mkdir(parents=True)
        with patch.object(cli,'load',return_value=self.cfg),patch.object(cli.shutil,'rmtree',side_effect=PermissionError), \
             self.assertLogs(cli.log,'ERROR'):
            self.assertEqual(cli.main(['logout','--platform','tiktok']),1)
        self.assertTrue(CookieJar.path('tiktok_main','tiktok').exists())

if __name__=='__main__':unittest.main()
