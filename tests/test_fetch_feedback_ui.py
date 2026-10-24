"""Echte Widgets, isolierte Datenbank, kontrollierte Abrufergebnisse."""
import json
import unittest
from unittest.mock import patch

from test_ui_accounts import WindowBase
from socialscraper import guicore as core, repo
from socialscraper.models import Video, Comment
from socialscraper.store import Store
from socialscraper.ui import logic, fetch_feedback as feedback

URL_A = 'https://www.youtube.com/watch?v=aaaaaaaaaaa'
URL_B = 'https://www.youtube.com/watch?v=bbbbbbbbbbb'


def event(url=URL_A, *, stage='result', saved=True, state='received', count=1, message='', uid=None):
    return feedback.PREFIX + json.dumps({'version': 1, 'stage': stage, 'url': url,
        'uid': uid or core.canonical_video(url)[0], 'platform': core.detect_platform(url),
        'video_saved': saved, 'comments_saved': count, 'comments_status': state, 'message': message})


class FeedbackTests(unittest.TestCase):
    def test_last_checkpoint_is_not_a_completed_result_and_is_not_counted_twice(self):
        events = feedback.read_events([('X', -1, [event(stage='checkpoint', count=0, state='partial'),
            event(stage='checkpoint', count=4, state='partial')])])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['comments_saved'], 4)
        self.assertFalse(feedback.complete(events[0]))

    def test_failed_completion_preserves_a_previously_confirmed_save(self):
        events = feedback.read_events([('X', 1, [event(stage='checkpoint', count=3, state='partial'),
            event(saved=False, count=0, state='unavailable', message='Verbindung abgebrochen.')])])
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]['video_saved'])
        self.assertEqual(events[0]['comments_saved'], 3)
        self.assertFalse(feedback.complete(events[0]))

    def test_invalid_events_are_ignored(self):
        rows = [feedback.PREFIX + '{kaputt', feedback.PREFIX + json.dumps([]),
                event(count=-1), event(state='guess'), event(saved=1), event(count=True)]
        self.assertEqual(feedback.read_events([('X', 0, rows)]), [])

    def test_retry_keeps_original_options_and_excludes_completed_aliases(self):
        command = ['scrape', URL_A, URL_B, '--backend', 'local', '--max-comments', '17']
        events = feedback.read_events([('X', 1, [event(url='https://youtu.be/aaaaaaaaaaa')])])
        self.assertEqual(feedback.retry_steps([('X', command)], events, [('X', 1, [])], False),
                         [('X', ['scrape', URL_B, '--backend', 'local', '--max-comments', '17'])])
        self.assertEqual(command[1:3], [URL_A, URL_B])


class FetchFeedbackWidgetsTests(WindowBase):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.tab_fetch import FetchTab
        from socialscraper.ui.tab_data import DataTab
        self.data = self.app.add('Gespeicherte Daten', DataTab)
        self.tab = self.app.add('Abrufen', FetchTab)
        self.tab.refresh(); self.app.update()

    def start(self, urls=URL_A):
        self.tab.links.insert('1.0', urls)
        self.tab.start_fetch()

    def persist(self, url=URL_A, count=0):
        platform = core.detect_platform(url)
        vid = core.canonical_video(url)[0].split(':', 1)[-1]
        video = Video(platform, vid, url, comment_count=count)
        with Store(self.db) as store:
            store.save_result(video, [Comment(platform, vid, f'{vid}-{i}', f'Text {i}') for i in range(count)])

    def done(self, lines, code=0, cancelled=False):
        steps, callback = self.app.steps.pop()
        callback([(label, code, list(lines)) for label, _cmd in steps], cancelled)

    def test_unavailable_comments_stay_visible_with_saved_results_and_retry(self):
        self.start(); self.persist()
        self.done([event(state='unavailable', count=0, message='Kommentare nicht abrufbar. Bitte unter „Konten“ anmelden.')], code=1)
        self.assertIn('unvollständig', self.tab.result.cget('text'))
        self.assertIn('Kommentare nicht abrufbar', self.tab.result.cget('text'))
        self.assertEqual(self.app.selected, [])
        self.assertEqual(self.tab.retry_btn.winfo_manager(), 'pack')
        self.assertEqual(self.tab.accounts_btn.winfo_manager(), 'pack')
        self.tab.open_results_btn.invoke()
        self.assertEqual(self.app.selected[-1], 'Gespeicherte Daten')
        self.assertEqual(self.data.tree.selection(), ('youtube:aaaaaaaaaaa',))

    def test_cancel_after_checkpoint_keeps_comments_and_groups_saved_video(self):
        group = logic.create_group(self.db, 'Ernte'); self.tab.refresh()
        self.tab.group_var.set('Ernte')
        self.start(); self.persist(count=3)
        self.done([event(stage='checkpoint', state='partial', count=3)], code=-1, cancelled=True)
        self.assertIn('Abgebrochen. 1 Video und 3 Kommentare bleiben gespeichert.', self.tab.result.cget('text'))
        self.assertEqual([row['uid'] for row in logic.query_videos(self.db, group=group['id'])], ['youtube:aaaaaaaaaaa'])
        self.assertEqual(len(logic.query_comments(self.db, 'youtube', 'aaaaaaaaaaa')), 3)
        self.assertEqual(self.app.selected, [])
        self.assertTrue(self.tab._retry_steps)

    def test_retry_only_unfinished_link_using_captured_comment_limit(self):
        self.tab.max_comments.set(17)
        self.start(URL_A + '\n' + URL_B); self.persist(); self.persist(URL_B)
        self.tab.max_comments.set(3)  # während des Laufs geändert
        self.done([event(), event(URL_B, state='unavailable', count=0, message='Zeitüberschreitung.')], code=1)
        self.tab.retry_btn.invoke()
        steps, _callback = self.app.steps[-1]
        cmd = steps[0][1]
        self.assertEqual(cmd[1:cmd.index('--backend')], [URL_B])
        self.assertEqual(cmd[cmd.index('--max-comments') + 1], '17')
        self.assertEqual(self.tab._pending['wanted'], 17)
        self.assertEqual(self.tab._pending['links'], {'youtube': [URL_B]})

    def test_only_metadata_was_requested_and_is_a_completed_result(self):
        self.tab.max_comments.set(0); self.start(); self.persist()
        self.done([event(state='not_requested', count=0)])
        self.assertIn('Nur Videodaten gespeichert', self.tab.result.cget('text'))
        self.assertFalse(self.tab._retry_steps)
        self.assertEqual(self.app.statuses[-1][1], 'ok')

    def test_platform_reported_zero_is_distinct_from_failed_comments(self):
        self.start(); self.persist()
        self.done([event(state='empty', count=0)])
        self.assertIn('Die Plattform meldet keine Kommentare', self.tab.result.cget('text'))
        self.assertNotIn('nicht abrufbar', self.tab.result.cget('text'))
        self.assertFalse(self.tab._retry_steps)

    def test_auth_failure_can_return_to_accounts_then_retry_same_account(self):
        url = 'https://www.tiktok.com/@u/video/123'
        self.tab.need_auth.set(True); self.start(url)
        self.done([event(url, saved=False, count=0, state='unavailable', message='Anmeldung fehlt.')], code=3)
        original = list(self.tab._retry_steps[0][1])
        with patch('tkinter.messagebox.showwarning'):
            self.tab.retry_btn.invoke()
        self.assertEqual(self.app.steps, [])
        self.tab.accounts_btn.invoke(); self.assertEqual(self.app.selected[-1], 'Konten')
        self.cookie('tiktok_main')
        self.tab.retry_btn.invoke()
        self.assertEqual(self.app.steps[-1][0][0][1], original)

    def test_missing_instagram_login_does_not_block_other_platform_in_mixed_run(self):
        with patch('tkinter.messagebox.askyesno') as ask:
            self.start(URL_A + '\nhttps://www.instagram.com/p/abc/')
        ask.assert_not_called()
        self.assertEqual(len(self.app.steps[-1][0]), 2)

    def test_double_start_keeps_pending_result_until_process_finishes(self):
        self.start(); plan = self.tab._pending
        self.app.job = object()
        with patch('tkinter.messagebox.showinfo'):
            self.tab.start_fetch(); self.tab.start_profile()
        self.assertIs(self.tab._pending, plan)
        self.assertEqual(len(self.app.steps), 1)
        self.app.job = None


if __name__ == '__main__':
    unittest.main()
