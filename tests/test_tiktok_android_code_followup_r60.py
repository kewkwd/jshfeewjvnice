"""R60: Android signup must revisit an email-code step after manual CAPTCHA."""
import contextlib
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from socialscraper.android import tiktok_app


class TikTokAndroidCodeFollowup(unittest.TestCase):
    def _run(self, *, code_before_captcha):
        adb = MagicMock()
        sequence = iter(([True, False, False] if code_before_captcha else [True]))
        adb.warte_auf.side_effect = lambda **_kwargs: next(sequence, False)
        ctx = SimpleNamespace(email='fixture@example.invalid', password='FIXTURE_ONLY',
                              code_holen=MagicMock(return_value='000000'),
                              melden=MagicMock(), timeout=5, stop=lambda: False)
        detections = iter(([False, True, False] if code_before_captcha else [True, False]))

        with contextlib.ExitStack() as stack:
            for name in ('_wegklicken', '_feld_fuellen', '_weiter', '_geburtsdatum'):
                stack.enter_context(patch.object(tiktok_app, name))
            stack.enter_context(patch.object(tiktok_app.time, 'sleep'))
            stack.enter_context(patch.object(tiktok_app, '_tippe', return_value=True))
            stack.enter_context(patch.object(tiktok_app, '_raetsel', side_effect=lambda _adb: next(detections, False)))
            stack.enter_context(patch.object(tiktok_app, '_warte_manuell'))
            stack.enter_context(patch.object(tiktok_app, '_ist_angemeldet', return_value=False))
            result = tiktok_app.registrieren(adb, ctx)
        return adb, ctx, result

    def test_code_appearing_after_captcha_is_retrieved_and_submitted_once(self):
        adb, ctx, result = self._run(code_before_captcha=False)
        self.assertFalse(result)
        ctx.code_holen.assert_called_once()
        self.assertEqual(adb.warte_auf.call_args_list,
                         [unittest.mock.call(teil='Code', timeout=25)])

    def test_code_already_processed_before_captcha_is_not_retried(self):
        adb, ctx, result = self._run(code_before_captcha=True)
        self.assertFalse(result)
        ctx.code_holen.assert_called_once()
        self.assertEqual(adb.warte_auf.call_count, 1)
