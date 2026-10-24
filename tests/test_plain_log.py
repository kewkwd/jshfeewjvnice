import json
import os
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from socialscraper.ui.plain_log import PlainLog


def event(**changes):
    data = dict(version=1, stage='result', platform='youtube', url='https://youtu.be/abcdefghijk',
                uid='youtube:abcdefghijk', video_saved=True, comments_saved=0,
                comments_status='not_requested', message='', success=True)
    data.update(changes)
    return '[SS_FETCH] ' + json.dumps(data)


class PlainLogTests(unittest.TestCase):
    def test_manual_captcha_instruction_preserves_the_next_button(self):
        from socialscraper.ui.plain_log import simple_message
        text = 'web.de: Bitte ein angezeigtes CAPTCHA selbst lösen und diesen Schritt im Fenster mit Jetzt starten bzw. Weiter bestätigen.'
        self.assertEqual(simple_message(text), text)

    def setUp(self):
        self.log = PlainLog()

    def test_metadata_does_not_claim_a_downloaded_video(self):
        text, tag = self.log.format(event())
        self.assertIn('Angaben zum Video', text)
        self.assertIn('Kommentare waren nicht ausgewählt', text)
        self.assertNotIn('abcdefghijk', text)
        self.assertNotIn('Video gespeichert', text)
        self.assertEqual(tag, 'OK')

    def test_zero_received_does_not_claim_an_empty_comment_section(self):
        text, tag = self.log.format(event(comments_status='unavailable', success=False,
            message='YouTube hat keine Kommentare geliefert. Das belegt keine leere Kommentarliste. Später erneut versuchen.'))
        self.assertIn('0 Kommentare', text)
        self.assertIn('wissen wir noch nicht', text)
        self.assertNotIn('hat keine Kommentare', text)
        self.assertEqual(tag, 'WARNING')
        text, tag = self.log.format(event(comments_status='empty'))
        self.assertIn('Die Plattform meldet:', text)
        self.assertEqual(tag, 'OK')

    def test_saved_partial_data_is_not_presented_as_complete(self):
        text, tag = self.log.format(event(comments_status='partial', comments_saved=3, success=False))
        self.assertIn('3 Kommentare sind gespeichert', text)
        self.assertIn('nicht vollständig', text)
        self.assertIn('Erneut versuchen', text)
        self.assertEqual(tag, 'WARNING')

    def test_checkpoint_is_saved_but_not_finished(self):
        text, tag = self.log.format(event(stage='checkpoint', comments_saved=2, comments_status='partial'))
        self.assertIn('2 Kommentare sind gespeichert', text)
        self.assertIn('läuft weiter', text)
        self.assertEqual(tag, 'MUTED')

    def test_video_numbers_stay_stable_across_checkpoints(self):
        first, _ = self.log.format(event(stage='checkpoint'))
        second, _ = self.log.format(event(url='https://youtu.be/other-video', uid='youtube:other-video'))
        final, _ = self.log.format(event())
        self.assertIn('Video 1:', first)
        self.assertIn('Video 2:', second)
        self.assertIn('Video 1:', final)

    def test_error_level_survives_removing_program_prefix(self):
        text, tag = self.log.format('04:15:37 ERROR   cli | Session für tiktok nicht verfügbar (State.ERROR)')
        self.assertEqual(tag, 'ERROR')
        self.assertIn('konnte nicht bestätigt', text)
        self.assertIn('Konten', text)
        for jargon in ('ERROR', 'cli', 'State', 'Session', '04:15:37'):
            self.assertNotIn(jargon, text)
        self.assertNotIn('abgelaufen', text)

    def test_missing_expired_and_unchecked_logins_stay_distinct(self):
        notices = {
            'session_missing': 'noch nicht angemeldet',
            'session_expired': 'abgelaufen',
            'session_error': 'konnte nicht geprüft',
        }
        for key, wording in notices.items():
            shown, tag = self.log.format(f'04:15:37 WARNING socialscraper.net.notify | [{key}] Keine gültigen Cookies für tiktok_main/tiktok; Login ausführen')
            self.assertIn(wording, shown)
            self.assertEqual(tag, 'WARNING')
            self.assertNotIn('Cookies', shown)
            self.assertNotIn('tiktok_main', shown)
            self.assertNotIn(key, shown)

    def test_infrastructure_and_debug_only_belong_in_details(self):
        self.assertIsNone(self.log.format('04:15:37 INFO socialscraper.adapters.youtube | YouTube-JavaScript: node 24.0')[0])
        self.assertIsNone(self.log.format('04:15:37 DEBUG cli | Rohmeldung: technical error')[0])
        text, tag = self.log.format('04:15:37 WARNING socialscraper.adapters.tiktok | kein Hydration-Blob für https://example.test')
        self.assertIn('keine Angaben', text)
        self.assertNotIn('Blob', text)
        self.assertEqual(tag, 'WARNING')

    def test_retry_explains_wait_without_function_names(self):
        text, tag = self.log.format('04:15:37 WARNING socialscraper.net.retry | _get_video fehlgeschlagen (1/3) - retry in 2.0s')
        self.assertIn('Versuch 1 von 3', text)
        self.assertIn('In 2 Sekunden', text)
        self.assertNotIn('_get_video', text)
        self.assertNotIn('retry', text)

    def test_collection_total_is_not_a_count_of_this_run(self):
        text, _ = self.log.format("{'videos': {'youtube': 20}, 'comments': {'youtube': 100}}")
        self.assertIn('In deiner Sammlung: 20 Videos, 100 Kommentare', text)

    def test_traceback_does_not_leak_frames_or_hide_later_status(self):
        self.assertIn('Problem', self.log.format('Traceback (most recent call last):')[0])
        self.assertIsNone(self.log.format('  File "x.py", line 2, in scrape')[0])
        self.assertIsNone(self.log.format('ValueError: internals')[0])
        self.assertIn('Fertig:', self.log.format('fertig: 1 ok, 0 fehler')[0])

    def test_certificate_failure_does_not_assert_interference(self):
        text, _ = self.log.format('04:15:37 ERROR cli | Die verschlüsselte Verbindung ist nicht vertrauenswürdig (Zertifikatsfehler). Ein Proxy oder Netzwerk greift in die Verbindung ein.')
        self.assertIn('konnte nicht geprüft', text)
        self.assertNotIn('greift', text)

    def test_malformed_machine_events_are_never_shown_as_success(self):
        for raw in ('[SS_FETCH] kaputt', '[SS_FETCH] {}', event(comments_saved=-1), event(video_saved='yes')):
            self.assertIsNone(self.log.format(raw)[0])

    def test_machine_events_do_not_leak_exception_names(self):
        for raw in (event(video_saved=False, message='Instagram-Beitrag nicht abrufbar (KeyError)'),
                    event(comments_status='unavailable', message='Der Abruf ist fehlgeschlagen (DownloadError). Später noch einmal versuchen.')):
            shown, _ = self.log.format(raw)
            self.assertNotIn('KeyError', shown)
            self.assertNotIn('DownloadError', shown)


@unittest.skipUnless(os.name == 'nt', 'Fensterwidgets nur unter Windows')
class LogWidgetTests(unittest.TestCase):
    def test_visible_log_is_simple_and_raw_details_remain_available(self):
        import tkinter as tk
        from socialscraper.ui import common
        from socialscraper.ui.app import App
        with tempfile.TemporaryDirectory() as folder:
            root = tk.Tk()
            root.withdraw()
            try:
                text_widget = tk.Text(root)
                harness = SimpleNamespace(log_text=text_widget, _plain_log=PlainLog(),
                                          _target_name=lambda _: None, _write_log=App._write_log)
                path = pathlib.Path(folder) / 'gui.log'
                error = '04:15:37 ERROR cli | Session für tiktok nicht verfügbar (State.ERROR)'
                debug = '04:15:37 DEBUG cli | original diagnostics'
                with patch.object(common, 'GUI_LOG', path):
                    App.log(harness, error)
                    App.log(harness, debug)
                    App.log(harness, event(comments_status='partial', comments_saved=3))
                visible = text_widget.get('1.0', 'end')
                self.assertIn('Konten', visible)
                self.assertNotIn('State.ERROR', visible)
                self.assertNotIn('original diagnostics', visible)
                self.assertIn('ERROR', text_widget.tag_names('1.9'))
                self.assertIn('WARNING', text_widget.tag_names('2.9'))
                self.assertEqual(path.read_text(encoding='utf-8'), visible.rstrip('\n') + '\n')
                details = path.with_name('gui-details.log').read_text(encoding='utf-8')
                self.assertIn(error, details)
                self.assertIn(debug, details)
                self.assertIn('[SS_FETCH]', details)
            finally:
                root.destroy()
