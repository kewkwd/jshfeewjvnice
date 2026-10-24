"""Logik der Übersicht (ohne Fenster): Zustand einsammeln, Karten, Hinweise („Das braucht deine Aufmerksamkeit“), Erste Schritte."""
import datetime
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import channels, homelogic as hl, repo
from socialscraper.models import Comment, Video
from socialscraper.store import Store

NOW = datetime.datetime(2026, 10, 12, 12, 0, 0, tzinfo=datetime.timezone.utc)


def snap(**kw):
    """Ein gesammelter Zustand ohne Datenbank (alles ruhig); Tests ändern nur, was sie brauchen."""
    base = {'now': NOW, 'root': '.', 'error': None, 'db_exists': True, 'mode': 'aus',
            'service': {'running': True, 'stale': False, 'current': None, 'unavailable': False},
            'accounts': [], 'targets': [], 'channels': [], 'runs': [], 'due': 0,
            'counts': {'videos': 5, 'comments': 20, 'people': 4, 'new_comments': 0, 'new_authors': 0, 'new_videos': 0},
            'system_ok': True, 'ai_key': True, 'ai_auto': False, 'busy': None,
            'last_backup': NOW - datetime.timedelta(days=1), 'manual_runs': 1, 'has_platform_data': {}}
    base.update(kw)
    return base


def acc(id=1, platform='tiktok', label='Hauptkonto', login='saved', state='bereit', default=True, until=None, reason=None, in_rotation=True):
    return {'id': id, 'platform': platform, 'label': label, 'default': default, 'in_rotation': in_rotation, 'login': login,
            'state': state, 'until': until, 'reason': reason, 'text': 'bereit' if state == 'bereit' else f'Abkühlung bis 14:30 wegen {reason}'}


def channel(id=1, state='fetching', enabled=1, total=100, finished=20, failed=0, **extra):
    t = {'id': id, 'kind': 'channel', 'platform': 'youtube', 'value': '@kanal', 'display_name': 'Kanal', 'enabled': enabled,
         'backfill_state': state, 'next_run_at': None, 'pause_until': None, 'pause_reason': None,
         'progress': {'pending': total - finished - failed, 'running': 0, 'done': finished, 'failed': failed, 'skipped': 0,
                      'total': total, 'finished': finished}}
    t.update(extra)
    return t


def target(id=1, kind='hashtag', platform='tiktok', value='reisen', **extra):
    t = {'id': id, 'kind': kind, 'platform': platform, 'value': value, 'enabled': 1, 'max_comments': 20, 'next_run_at': None}
    t.update(extra)
    return t


def keys(items):
    return [i['key'].split(':')[0] for i in items]


class AttentionTests(unittest.TestCase):
    def test_quiet_state_has_nothing_to_do(self):
        self.assertEqual(hl.attention(snap()), [])

    def test_tor_mode_offers_to_switch_to_aus(self):
        items = hl.attention(snap(mode='tor'))
        item = next(i for i in items if i['key'] == 'proxy_mode')
        self.assertEqual(item['action']['id'], 'set_mode')
        self.assertEqual(item['action']['value'], 'aus')
        self.assertEqual(item['action']['from'], 'tor')
        self.assertIn('Tor', item['title'])
        self.assertIn('Konten', item['title'])
        self.assertIn('Anmelden, Instagram, X und TikTok-Kommentare gehen in diesem Modus nicht.', item['text'])

    def test_free_proxy_mode_is_covered_too(self):
        item = next(i for i in hl.attention(snap(mode='frei')) if i['key'] == 'proxy_mode')
        self.assertIn('Kostenlose Proxys', item['title'])

    def test_proxy_mode_is_only_info_when_nothing_is_affected(self):
        item = next(i for i in hl.attention(snap(mode='tor')) if i['key'] == 'proxy_mode')
        self.assertEqual(item['level'], 'info')

    def test_proxy_mode_is_a_warning_when_targets_are_blocked(self):
        items = hl.attention(snap(mode='tor', targets=[target(kind='hashtag', platform='tiktok')]))
        item = next(i for i in items if i['key'] == 'proxy_mode')
        self.assertEqual(item['level'], 'warn')
        self.assertIn('1 Ziel ist dadurch blockiert', item['text'])

    def test_proxy_mode_is_a_warning_when_login_accounts_exist(self):
        items = hl.attention(snap(mode='tor', accounts=[acc(login='missing')]))
        self.assertEqual(next(i for i in items if i['key'] == 'proxy_mode')['level'], 'warn')

    def test_normal_mode_has_no_proxy_item(self):
        for mode in ('aus', 'liste'):
            self.assertNotIn('proxy_mode', [i['key'] for i in hl.attention(snap(mode=mode))])

    def test_missing_login_with_a_channel_asks_to_log_in(self):
        items = hl.attention(snap(accounts=[acc(login='missing')], channels=[channel()]))
        self.assertEqual(keys(items).count('login'), 0)       # Plattform des Kanals ist YouTube: kein Hinweis
        c = channel(platform='tiktok')
        items = hl.attention(snap(accounts=[acc(login='missing')], channels=[c]))
        item = next(i for i in items if i['key'] == 'login:tiktok')
        self.assertEqual(item['action'], {'id': 'login', 'label': 'Jetzt anmelden', 'account_id': 1})
        self.assertEqual(item['level'], 'warn')

    def test_missing_login_is_ignored_when_the_platform_is_not_used(self):
        self.assertEqual(hl.attention(snap(accounts=[acc(login='missing')])), [])

    def test_missing_login_with_stored_videos_of_that_platform(self):
        items = hl.attention(snap(accounts=[acc(login='missing')], has_platform_data={'tiktok': True}))
        self.assertIn('login:tiktok', [i['key'] for i in items])

    def test_no_login_hint_in_anonymous_mode_only_the_mode_item(self):
        items = hl.attention(snap(mode='tor', accounts=[acc(login='missing')], targets=[target()]))
        self.assertNotIn('login:tiktok', [i['key'] for i in items])

    def test_any_saved_account_of_the_platform_is_enough(self):
        accounts = [acc(1, login='missing'), acc(2, label='Zweit', login='saved', default=False)]
        items = hl.attention(snap(accounts=accounts, targets=[target()]))
        self.assertNotIn('login:tiktok', [i['key'] for i in items])

    def test_reddit_needs_no_login_hint(self):
        items = hl.attention(snap(accounts=[acc(platform='reddit', login='missing')], targets=[target(platform='reddit', kind='profile')]))
        self.assertEqual(items, [])

    def test_cooldown_account_is_info(self):
        until = NOW + datetime.timedelta(hours=3)
        accounts = [acc(1), acc(2, label='Zweit', state='abkuehlung', default=False, until=until, reason='CAPTCHA')]
        items = hl.attention(snap(accounts=accounts, targets=[target()]))
        item = next(i for i in items if i['key'].startswith('cool:'))
        self.assertEqual(item['level'], 'info')
        self.assertIn('Zweit', item['title'])
        self.assertEqual(item['action']['id'], 'goto_account')

    def test_no_account_free_is_a_warning_with_time(self):
        until = NOW + datetime.timedelta(hours=3)
        accounts = [acc(1, state='abkuehlung', until=until, reason='CAPTCHA')]
        items = hl.attention(snap(accounts=accounts, targets=[target()]))
        item = next(i for i in items if i['key'] == 'noacc:tiktok')
        self.assertEqual(item['level'], 'warn')
        self.assertIn('Kein TikTok-Konto frei', item['title'])
        self.assertIn('wieder dran', item['text'])

    def test_all_accounts_cooling_without_targets_is_only_the_cooldown_info(self):
        until = NOW + datetime.timedelta(hours=3)
        items = hl.attention(snap(accounts=[acc(1, state='abkuehlung', until=until, reason='CAPTCHA')]))
        self.assertEqual(keys(items), ['cool'])

    def test_paused_channel_names_reason_and_time(self):
        c = channel(state='paused', pause_until='2026-10-12T14:30:00Z', pause_reason='CAPTCHA')
        item = next(i for i in hl.attention(snap(channels=[c])) if i['key'].startswith('chan_pause'))
        self.assertIn('pausiert', item['title'])
        self.assertIn('wegen CAPTCHA', item['title'])
        self.assertEqual(item['action'], {'id': 'goto_channel', 'label': 'Details', 'target_id': 1})
        self.assertEqual(item['level'], 'warn')

    def test_user_paused_channel_can_be_resumed(self):
        item = next(i for i in hl.attention(snap(channels=[channel(enabled=0)])) if i['key'].startswith('chan_off'))
        self.assertEqual(item['action']['id'], 'resume_channel')

    def test_finished_channel_that_is_disabled_is_not_flagged(self):
        self.assertEqual(hl.attention(snap(channels=[channel(enabled=0, state='done', finished=100)])), [])

    def test_failed_channel_videos_can_be_retried(self):
        item = next(i for i in hl.attention(snap(channels=[channel(failed=3)])) if i['key'].startswith('chan_fail'))
        self.assertEqual(item['action']['id'], 'requeue_channel')
        self.assertIn('3 Videos', item['title'])

    def test_one_failed_video_uses_singular(self):
        item = next(i for i in hl.attention(snap(channels=[channel(failed=1)])) if i['key'].startswith('chan_fail'))
        self.assertIn('1 Video fehlgeschlagen', item['title'])

    def test_service_off_with_due_targets(self):
        item = next(i for i in hl.attention(snap(service={'running': False, 'stale': False}, due=3)) if i['key'] == 'service_off')
        self.assertIn('3 Ziele sind fällig', item['text'])
        self.assertEqual(item['action']['id'], 'start_service')

    def test_service_off_without_due_targets_is_quiet(self):
        self.assertEqual(hl.attention(snap(service={'running': False, 'stale': False}, due=0)), [])

    def test_service_stale(self):
        item = next(i for i in hl.attention(snap(service={'running': True, 'stale': True, 'age_s': 900})) if i['key'] == 'service_stale')
        self.assertEqual(item['action']['id'], 'stop_service')

    def test_service_missing_module_is_not_an_error(self):
        self.assertEqual(hl.attention(snap(service={'running': False, 'unavailable': True}, due=2)), [])

    def test_ai_key_missing_is_info_when_comments_exist(self):
        item = next(i for i in hl.attention(snap(ai_key=False)) if i['key'] == 'ai_key')
        self.assertEqual(item['level'], 'info')
        self.assertEqual(item['action']['id'], 'goto_ai')

    def test_ai_key_missing_is_a_warning_when_auto_is_on(self):
        item = next(i for i in hl.attention(snap(ai_key=False, ai_auto=True)) if i['key'] == 'ai_key')
        self.assertEqual(item['level'], 'warn')
        self.assertIn('automatische Stimmung', item['text'])

    def test_ai_key_missing_but_no_comments_is_quiet(self):
        counts = dict(snap()['counts'], comments=0, videos=0)
        self.assertEqual(hl.attention(snap(ai_key=False, counts=counts)), [])

    def test_backup_never(self):
        item = next(i for i in hl.attention(snap(last_backup=None)) if i['key'] == 'backup')
        self.assertIn('noch nie', item['title'])
        self.assertEqual(item['action']['id'], 'backup')

    def test_backup_old(self):
        item = next(i for i in hl.attention(snap(last_backup=NOW - datetime.timedelta(days=30))) if i['key'] == 'backup')
        self.assertIn('30 Tagen', item['title'])

    def test_recent_backup_is_quiet(self):
        self.assertEqual(hl.attention(snap(last_backup=NOW - datetime.timedelta(days=13))), [])

    def test_no_backup_hint_for_an_empty_database(self):
        counts = dict(snap()['counts'], videos=0, comments=0)
        self.assertEqual(hl.attention(snap(last_backup=None, counts=counts)), [])

    def test_system_check_failed_is_an_error(self):
        item = next(i for i in hl.attention(snap(system_ok=False)) if i['key'] == 'system')
        self.assertEqual(item['level'], 'err')

    def test_system_check_unknown_is_quiet(self):
        self.assertEqual(hl.attention(snap(system_ok=None)), [])

    def test_database_error_is_an_error(self):
        item = next(i for i in hl.attention(snap(error='Die Datenbank ist beschädigt oder keine Datenbank.')) if i['key'] == 'db_error')
        self.assertEqual(item['level'], 'err')

    def test_recent_failed_run_points_to_problems(self):
        run = {'id': 9, 'status': 'failed', 'target_id': 3, 'target_kind': 'profile', 'target_platform': 'reddit', 'target_value': 'abc',
               'finished_at': '2026-10-12T10:00:00Z', 'started_at': '2026-10-12T09:59:00Z', 'error': 'blocked by network security'}
        item = next(i for i in hl.attention(snap(runs=[run])) if i['key'].startswith('run_fail'))
        self.assertEqual(item['action']['id'], 'open_problems')
        self.assertIn('Sicherheitsprüfung', item['text'])

    def test_old_failed_run_is_ignored(self):
        run = {'id': 9, 'status': 'failed', 'target_id': 3, 'target_kind': 'profile', 'target_platform': 'reddit', 'target_value': 'abc',
               'finished_at': '2026-10-10T10:00:00Z', 'started_at': '2026-10-10T09:59:00Z', 'error': 'x'}
        self.assertEqual(hl.attention(snap(runs=[run])), [])

    def test_failed_run_followed_by_success_is_ignored(self):
        ok = {'id': 10, 'status': 'ok', 'target_id': 3, 'target_kind': 'profile', 'target_platform': 'reddit', 'target_value': 'abc',
              'finished_at': '2026-10-12T11:00:00Z', 'started_at': '2026-10-12T10:59:00Z', 'error': None}
        bad = dict(ok, id=9, status='failed', finished_at='2026-10-12T10:00:00Z', error='x')
        self.assertEqual(hl.attention(snap(runs=[ok, bad])), [])          # list_runs liefert die neuesten zuerst

    def test_order_is_errors_then_warnings_then_info(self):
        items = hl.attention(snap(system_ok=False, mode='tor', targets=[target()], ai_key=False))
        levels = [i['level'] for i in items]
        self.assertEqual(levels, sorted(levels, key=lambda l: hl.LEVEL_ORDER[l]))
        self.assertEqual(levels[0], 'err')

    def test_dismissed_items_are_hidden(self):
        items = hl.attention(snap(ai_key=False), dismissed={'ai_key'})
        self.assertEqual(items, [])

    def test_every_action_has_a_label_and_an_id(self):
        until = NOW + datetime.timedelta(hours=3)
        s = snap(mode='tor', ai_key=False, last_backup=None, service={'running': False}, due=2, system_ok=False,
                 accounts=[acc(1, login='missing'), acc(2, state='abkuehlung', until=until, reason='CAPTCHA', default=False)],
                 channels=[channel(state='paused', pause_until='2026-10-12T14:30:00Z', pause_reason='CAPTCHA', failed=2)],
                 targets=[target()], has_platform_data={'tiktok': True})
        for item in hl.attention(s):
            self.assertTrue(item['title'] and item['text'], item)
            self.assertTrue(item['action'] and item['action']['id'] and item['action']['label'], item)

    def test_texts_have_no_technical_words(self):
        s = snap(mode='tor', ai_key=False, last_backup=None, service={'running': False}, due=2, accounts=[acc(1, login='missing')],
                 targets=[target()])
        for item in hl.attention(s):
            for word in ('Traceback', 'Exception', 'None', 'NoneType', 'sqlite', 'errno'):
                self.assertNotIn(word, item['title'] + item['text'])


class CardTests(unittest.TestCase):
    def test_service_card_states(self):
        self.assertEqual(hl.service_card(snap(service={'running': False}))['value'], 'Aus')
        off = hl.service_card(snap(service={'running': False}, due=2))
        self.assertEqual((off['kind'], off['button'][0]), ('warn', 'start_service'))
        self.assertIn('2 Ziele sind fällig', off['detail'])
        on = hl.service_card(snap(service={'running': True, 'stale': False, 'current': None}))
        self.assertEqual((on['value'], on['kind'], on['button'][0]), ('An', 'ok', 'stop_service'))
        busy = hl.service_card(snap(service={'running': True, 'stale': False, 'current': {'kind': 'video'}}))
        self.assertEqual(busy['detail'], 'Arbeitet gerade.')
        stale = hl.service_card(snap(service={'running': True, 'stale': True}))
        self.assertEqual(stale['kind'], 'warn')

    def test_proxy_card(self):
        self.assertEqual(hl.proxy_card(snap(mode='aus'))['kind'], 'ok')
        card = hl.proxy_card(snap(mode='tor'))
        self.assertEqual((card['value'], card['kind']), ('Tor', 'warn'))
        self.assertIn('Konten sind gesperrt', card['detail'])

    def test_accounts_card_counts_per_platform(self):
        until = NOW + datetime.timedelta(hours=3)
        accounts = [acc(1), acc(2, label='Zweit', state='abkuehlung', until=until, reason='CAPTCHA', default=False),
                    acc(3, platform='instagram', login='missing'), acc(4, platform='reddit', login='missing', state='bereit')]
        card = hl.accounts_card(snap(accounts=accounts))
        self.assertIn('TikTok: 1 bereit · 1 in Pause', card['rows'])
        self.assertIn('Instagram: 0 bereit · 1 nicht angemeldet', card['rows'])
        self.assertIn('Reddit: 1 bereit', card['rows'])
        self.assertEqual(card['kind'], 'warn')

    def test_accounts_card_without_accounts_is_friendly(self):
        card = hl.accounts_card(snap(accounts=[]))
        self.assertEqual(card['value'], 'keine')
        self.assertIn('YouTube', card['detail'])

    def test_accounts_card_mentions_proxy_block(self):
        card = hl.accounts_card(snap(mode='tor', accounts=[acc(1)]))
        self.assertIn('Gesperrt durch den Proxy-Modus.', card['rows'])

    def test_channels_card_shows_only_work_in_progress(self):
        chans = [channel(1, state='fetching', total=138, finished=41, failed=3), channel(2, state='watching', total=40, finished=40),
                 channel(3, state='paused', pause_until='2026-10-12T14:30:00Z', pause_reason='CAPTCHA', total=60, finished=12)]
        card = hl.channels_card(snap(channels=chans))
        self.assertEqual([i['id'] for i in card['items']], [1, 3])
        self.assertAlmostEqual(card['items'][0]['frac'], 41 / 138)
        self.assertIn('41 von 138', card['items'][0]['count'])
        self.assertEqual(card['items'][1]['kind'], 'warn')
        self.assertEqual(card['watching'], 1)
        self.assertEqual(card['total'], 3)

    def test_channels_card_empty(self):
        card = hl.channels_card(snap(channels=[]))
        self.assertEqual((card['items'], card['total']), ([], 0))

    def test_channel_without_list_has_zero_progress(self):
        card = hl.channels_card(snap(channels=[channel(state='queued', total=0, finished=0)]))
        self.assertEqual(card['items'][0]['frac'], 0.0)

    def test_data_card(self):
        card = hl.data_card(snap(counts={'videos': 1234, 'comments': 5, 'people': 3, 'new_comments': 2, 'new_authors': 1, 'new_videos': 0}))
        self.assertEqual((card['videos'], card['comments'], card['people'], card['new_comments'], card['new_authors']), (1234, 5, 3, 2, 1))

    def test_last_run_card(self):
        self.assertEqual(hl.last_run_card(snap(runs=[]))['value'], 'noch keiner')
        ok = {'id': 1, 'status': 'ok', 'target_kind': 'hashtag', 'target_platform': 'tiktok', 'target_value': 'reisen',
              'finished_at': '2026-10-12T11:00:00Z', 'started_at': '2026-10-12T10:59:00Z', 'videos_new': 2, 'comments_new': 1}
        card = hl.last_run_card(snap(runs=[ok]))
        self.assertEqual((card['value'], card['kind']), ('geklappt', 'ok'))
        self.assertIn('2 neue Videos', card['detail'])
        self.assertIn('1 neuer Kommentar', card['detail'])
        failed = dict(ok, status='failed')
        self.assertEqual(hl.last_run_card(snap(runs=[failed]))['kind'], 'err')
        running = dict(ok, status='running')
        self.assertEqual(hl.last_run_card(snap(runs=[running]))['value'], 'läuft gerade')
        self.assertEqual(hl.last_run_card(snap(runs=[running, ok]))['value'], 'geklappt')

    def test_last_run_of_deleted_target_has_a_name(self):
        r = {'id': 1, 'status': 'partial', 'target_kind': None, 'label': 'hashtag youtube lofi', 'finished_at': '2026-10-12T11:00:00Z',
             'started_at': '2026-10-12T10:59:00Z', 'videos_new': 0, 'comments_new': 0}
        self.assertIn('Gelöschtes Ziel', hl.last_run_card(snap(runs=[r]))['detail'])


class ChecklistTests(unittest.TestCase):
    def done(self, steps):
        return {s['key']: s['done'] for s in steps}

    def test_fresh_install(self):
        counts = dict(snap()['counts'], videos=0, comments=0)
        steps = hl.checklist(snap(system_ok=True, counts=counts, manual_runs=0, accounts=[acc(login='missing')]), {})
        self.assertEqual(self.done(steps), {'system': True, 'account': False, 'proxy': False, 'fetch': False, 'view': False})

    def test_ticks_come_from_real_state(self):
        steps = hl.checklist(snap(accounts=[acc(login='saved')]), {'proxy_ok': True, 'viewed_data': True})
        self.assertTrue(all(s['done'] for s in steps))

    def test_account_step_can_be_skipped(self):
        steps = hl.checklist(snap(accounts=[acc(login='missing')]), {'skip_account': True})
        self.assertTrue(self.done(steps)['account'])

    def test_account_step_done_by_reddit_login(self):
        steps = hl.checklist(snap(accounts=[acc(platform='reddit', login='saved')]), {})
        self.assertTrue(self.done(steps)['account'])

    def test_system_check_failed_and_pending(self):
        self.assertFalse(self.done(hl.checklist(snap(system_ok=False), {}))['system'])
        steps = hl.checklist(snap(system_ok=None), {})
        self.assertFalse(self.done(steps)['system'])
        self.assertIn('geprüft', steps[0]['text'])

    def test_view_step_needs_videos(self):
        counts = dict(snap()['counts'], videos=0)
        self.assertFalse(self.done(hl.checklist(snap(counts=counts), {'viewed_data': True}))['view'])

    def test_fetch_step_done_by_a_manual_run_even_without_videos(self):
        counts = dict(snap()['counts'], videos=0)
        self.assertTrue(self.done(hl.checklist(snap(counts=counts, manual_runs=2), {}))['fetch'])

    def test_proxy_step_text_names_the_mode(self):
        steps = hl.checklist(snap(mode='tor'), {})
        self.assertIn('Aktuell: Tor', steps[2]['text'])
        self.assertIn('Konten sind gesperrt', steps[2]['text'])
        self.assertEqual(steps[2]['skip']['id'], 'proxy_ok')

    def test_visibility(self):
        steps = hl.checklist(snap(accounts=[acc(login='missing')]), {})
        self.assertTrue(hl.checklist_visible(steps, {}))
        self.assertFalse(hl.checklist_visible(steps, {'checklist_hidden': True}))
        full = hl.checklist(snap(accounts=[acc()]), {'proxy_ok': True, 'viewed_data': True})
        self.assertFalse(hl.checklist_visible(full, {}))

    def test_five_steps_each_with_an_action(self):
        steps = hl.checklist(snap(system_ok=False, accounts=[acc(login='missing')]), {})
        self.assertEqual([s['key'] for s in steps], ['system', 'account', 'proxy', 'fetch', 'view'])
        for s in steps[1:]:
            self.assertTrue(s['action'] and s['action']['id'])


class CollectTests(unittest.TestCase):
    """`collect` gegen eine echte Wegwerf-Datenbank (nur Lesen)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / 'data').mkdir()
        os.environ.pop('SS_PROXY_MODE', None)
        p = patch.dict(os.environ, {'SS_DB_PATH': str(self.root / 'data' / 'scrape.db')})
        p.start()
        self.addCleanup(p.stop)
        self.db_path = self.root / 'data' / 'scrape.db'

    def seed(self):
        store = Store(str(self.db_path))
        db = store.db
        repo.ensure_default_accounts(db)
        now = repo.now()
        for i in range(3):
            v = Video(platform='youtube', video_id=f'vid{i}abcdefgh', url=f'https://example.test/{i}', author=f'autor{i}', author_id=f'a{i}')
            comments = [Comment(platform='youtube', video_id=v.video_id, comment_id=f'c{i}{j}', text='Hallo', author=f'schreiber{j}',
                                author_id=f's{j}') for j in range(4)]
            store.save_result(v, comments, repo.iso(now))
        db.execute("UPDATE comments SET first_seen_at=? WHERE comment_id LIKE 'c0%'", (repo.iso(now - datetime.timedelta(days=5)),))
        db.commit()
        t = repo.add_target(db, 'hashtag', 'tiktok', 'reisen', interval_min=60, max_comments=10)
        db.execute('UPDATE targets SET next_run_at=? WHERE id=?', (repo.iso(now - datetime.timedelta(minutes=5)), t['id']))
        ref = channels.parse_channel_link('https://www.youtube.com/@beispiel')
        ch = channels.create_channel(db, ref, comments=0)
        repo.add_channel_items(db, ch['id'], 'youtube', [{'url': f'https://example.test/c/{k}', 'uid': f'youtube:x{k}'} for k in range(10)])
        db.execute("UPDATE channel_items SET status='done' WHERE id<=4")
        repo.update_channel(db, ch['id'], backfill_state='fetching')
        rid = repo.start_run(db, t['id'], 'hashtag tiktok reisen', 'daemon')
        repo.finish_run(db, rid, 'failed', error='Menschentest (CAPTCHA) erkannt')
        db.commit()
        store.db.close()

    def test_no_database_gives_empty_values_and_creates_no_file(self):
        s = hl.collect(self.root, self.db_path, system_ok=True)
        self.assertFalse(s['db_exists'])
        self.assertEqual(s['counts']['videos'], 0)
        self.assertEqual(s['accounts'], [])
        self.assertFalse(self.db_path.exists())
        self.assertEqual(hl.attention(s), [])        # nichts zu tun, nichts zu sichern

    def test_collects_counts_targets_channels_and_runs(self):
        self.seed()
        s = hl.collect(self.root, self.db_path, system_ok=True)
        self.assertEqual(s['counts']['videos'], 3)
        self.assertEqual(s['counts']['comments'], 12)
        self.assertEqual(s['counts']['new_comments'], 8)         # vier Kommentare sind „alt“
        self.assertEqual(len(s['targets']), 1)
        self.assertEqual(len(s['channels']), 1)
        self.assertEqual(s['channels'][0]['progress']['finished'], 4)
        self.assertEqual(s['due'], 1)
        self.assertEqual(s['runs'][0]['status'], 'failed')
        self.assertEqual(len(s['accounts']), 4)
        self.assertEqual(s['has_platform_data'], {'youtube': True})
        self.assertGreaterEqual(s['counts']['people'], 4)

    def test_attention_from_a_real_snapshot(self):
        self.seed()
        s = hl.collect(self.root, self.db_path, system_ok=True)
        got = {i['key'].split(':')[0] for i in hl.attention(s)}
        self.assertIn('service_off', got)             # ein Ziel ist fällig, kein Dienst
        self.assertIn('ai_key', got)
        self.assertIn('backup', got)
        self.assertIn('run_fail', got)

    def test_accounts_logged_in_from_cookie_files(self):
        self.seed()
        cookies = self.root / 'data' / 'cookies'
        cookies.mkdir()
        (cookies / 'tiktok_main.tiktok.json').write_text(json.dumps([{'name': 'sessionid', 'value': 'x'}]), encoding='utf-8')
        s = hl.collect(self.root, self.db_path, system_ok=True)
        by = {(a['platform']): a for a in s['accounts']}
        self.assertEqual(by['tiktok']['login'], 'saved')
        self.assertEqual(by['instagram']['login'], 'missing')

    def test_proxy_mode_comes_from_settings(self):
        (self.root / 'settings.toml').write_text('proxy_mode = "tor"\n', encoding='utf-8')
        self.assertEqual(hl.collect(self.root, self.db_path)['mode'], 'tor')

    def test_backup_time_from_ui_state_and_backup_folder(self):
        self.seed()
        stamp = repo.iso(repo.now() - datetime.timedelta(days=2))
        s = hl.collect(self.root, self.db_path, ui_state={'last_backup_at': stamp})
        self.assertIsNotNone(s['last_backup'])
        folder = self.root / 'data' / 'backups'
        folder.mkdir()
        (folder / 'scrape-sicherung.db').write_bytes(b'x')
        s = hl.collect(self.root, self.db_path)
        self.assertIsNotNone(s['last_backup'])
        self.assertNotIn('backup', {i['key'] for i in hl.attention(s)})

    def test_bad_ui_state_value_is_ignored(self):
        s = hl.collect(self.root, self.db_path, ui_state={'last_backup_at': 'gestern'})
        self.assertIsNone(s['last_backup'])

    def test_broken_database_gives_a_friendly_error(self):
        self.db_path.write_bytes(b'das ist keine datenbank' * 50)
        s = hl.collect(self.root, self.db_path, system_ok=True)
        self.assertTrue(s['error'])
        self.assertNotIn('Traceback', s['error'])
        self.assertEqual(hl.attention(s)[0]['level'], 'err')

    def test_old_schema_without_channel_tables_still_loads(self):
        import sqlite3
        con = sqlite3.connect(self.db_path)
        con.execute('CREATE TABLE videos (uid TEXT, platform TEXT, first_seen_at TEXT)')
        con.execute("INSERT INTO videos VALUES ('youtube:a','youtube','2026-01-01T00:00:00Z')")
        con.commit(); con.close()
        s = hl.collect(self.root, self.db_path, system_ok=True)
        self.assertEqual(s['counts']['videos'], 1)
        self.assertEqual(s['channels'], [])

    def test_collect_does_not_modify_the_database(self):
        self.seed()
        before = self.db_path.read_bytes()
        hl.collect(self.root, self.db_path, system_ok=True)
        self.assertEqual(self.db_path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
