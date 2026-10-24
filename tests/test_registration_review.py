"""The error handoff is finite, cancellable and never replays UI actions."""
from types import SimpleNamespace
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from socialscraper import registration_review, mailanbieter
from socialscraper.net.errors import FinalError
from socialscraper.config import Settings

class ReviewWindow(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.cfg=Settings(db_path=str(Path(temporary.name)/'data/test.db'))
    def fixture(self):
        clock=[0.0];page=Mock();page.is_closed.return_value=False
        ctx=SimpleNamespace(anbieter='web.de',melden=Mock(),check_cancelled=Mock(),
            warte=Mock(side_effect=lambda ms:clock.__setitem__(0,clock[0]+ms/1000)))
        return page,ctx,clock
    def test_duration_is_bounded_without_form_actions(self):
        page,ctx,clock=self.fixture()
        with patch.object(registration_review.time,'monotonic',side_effect=lambda:clock[0]):
            self.assertTrue(registration_review.hold(page,ctx,1))
        self.assertEqual(clock[0],1);self.assertEqual(ctx.warte.call_count,4)
        page.fill.assert_not_called();page.click.assert_not_called();page.goto.assert_not_called()
        self.assertTrue(any('review_required' in str(c) for c in ctx.melden.call_args_list))
    def test_already_closed_page_is_not_reported_as_open(self):
        page,ctx,clock=self.fixture();page.is_closed.return_value=True
        self.assertFalse(registration_review.hold(page,ctx,1))
        ctx.melden.assert_not_called();ctx.warte.assert_not_called()
    def test_cancellation_stops_immediately(self):
        page,ctx,clock=self.fixture();ctx.check_cancelled.side_effect=FinalError('Abgebrochen.')
        with self.assertRaisesRegex(FinalError,'Abgebrochen'):registration_review.hold(page,ctx,1)
        page.is_closed.assert_not_called();ctx.warte.assert_not_called()
    def test_closing_the_page_ends_hold(self):
        page,ctx,clock=self.fixture();page.is_closed.side_effect=[False,False,True]
        with patch.object(registration_review.time,'monotonic',side_effect=lambda:clock[0]):
            self.assertTrue(registration_review.hold(page,ctx,5))
        self.assertEqual(clock[0],.25)
    def test_invalid_durations_fail_before_external_work(self):
        for value in (-1,601,float('nan'),float('inf'),True,'300'):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                mailanbieter.create(self.cfg,'web.de',failure_window=value,backend=Mock())
    def test_noninteractive_or_android_flow_cannot_hold_browser(self):
        for opts in ({'mode':'auto'},{'route':'android'}):
            with self.assertRaisesRegex(ValueError,'begleitete Browserregistrierung'):
                mailanbieter.create(self.cfg,'web.de',failure_window=1,backend=Mock(),**opts)

    def test_page_closed_during_wait_ends_review_without_secondary_driver_error(self):
        page,ctx,clock=self.fixture()
        def close(ms):
            page.is_closed.return_value=True
            raise RuntimeError('Target page has been closed')
        ctx.warte.side_effect=close
        with patch.object(registration_review.time,'monotonic',side_effect=lambda:clock[0]):
            self.assertTrue(registration_review.hold(page,ctx,5))

    def test_cli_exposes_and_passes_bounded_review_time(self):
        import argparse
        from socialscraper.commands import mailkonto
        parser=argparse.ArgumentParser();mailkonto.add_arguments(parser)
        args=parser.parse_args(['neu','web.de','--fehlerfenster','300'])
        with patch.object(mailkonto.mailanbieter,'create',return_value={}) as create,patch('builtins.print'):
            self.assertEqual(mailkonto.run(args,self.cfg),0)
        self.assertEqual(create.call_args.kwargs['failure_window'],300)

    def test_visible_mailbox_flow_enables_diagnostic_window_only_for_browser(self):
        from socialscraper.ui import postfaecher
        from socialscraper import registration_data
        for route,provider,expected in (('Browser','web.de',True),('Android (Gmail)','gmail.com',False)):
            tab=SimpleNamespace(app=Mock(),route=SimpleNamespace(get=lambda:route),
                provider=SimpleNamespace(get=lambda:provider),refresh=Mock(),registration_details=Mock())
            with patch.object(postfaecher,'cfg_of',return_value=self.cfg), \
                 patch('socialscraper.ui.workflow_components.busy',return_value=False), \
                 patch.object(registration_data,'load',return_value={'vorname':'Test','nachname':'Fixture','geburtsdatum':'1995-01-01'}), \
                 patch.object(postfaecher.messagebox,'showerror') as error:
                postfaecher.PostfaecherTab.create(tab)
            error.assert_not_called()
            command=tab.app.run_steps.call_args.args[0][0][1]
            self.assertEqual('--fehlerfenster' in command,expected)
