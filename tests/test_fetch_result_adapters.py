"""Offline-Abnahme von Abrufstatus und gespeicherten Zwischenständen."""
import datetime as dt
import sys
import types
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from socialscraper.adapters.base import ScrapeContext, checkpoint
from socialscraper.adapters.instagram import InstagramAdapter
from socialscraper.adapters.tiktok import COMMENTS_JS, VIDEO_JS, TikTokAdapter
from socialscraper.adapters.youtube import YouTubeAdapter
from socialscraper.config import Settings
from socialscraper.fetch_result import FetchResult, normalize_result
from socialscraper.models import Comment, Video
from socialscraper.store import Store


class Aborted(BaseException):
    """Simuliert einen harten Abbruch ohne einen Prozess oder Browser zu beenden."""


class FakePage:
    def __init__(self, count=None, batches=(), selector_error=None, body="", session_states=()):
        self.raw = {"id": "101", "stats": {"commentCount": count, "playCount": 42}}
        self.batches = iter(batches)
        self.selector_error = selector_error
        self.body = body
        self.session_states = iter(session_states)
        self.selector_calls = 0

    def goto(self, *args, **kwargs):
        pass

    def wait_for_timeout(self, *args):
        pass

    def wait_for_selector(self, *args, **kwargs):
        self.selector_calls += 1
        if self.selector_error:
            raise self.selector_error

    def evaluate(self, expression):
        if expression == VIDEO_JS:
            return self.raw
        if expression == COMMENTS_JS:
            value = next(self.batches, [])
            if isinstance(value, BaseException):
                raise value
            return value
        if "top-login-button" in expression:
            return next(self.session_states, False)
        if "document.body" in expression:
            return self.body
        raise AssertionError(expression)


class FakePool:
    def __init__(self, page):
        self.page = page

    @contextmanager
    def acquire(self, *args):
        yield self.page


def context(limit=5, page=None, callback=None, **kwargs):
    return ScrapeContext(Settings(max_comments=limit, max_retries=1, impersonate=""), None, None,
                         sessions=FakePool(page) if page else None, checkpoint=callback, **kwargs)


def fetch_tiktok(page, ctx):
    with patch("socialscraper.adapters.tiktok.humanize.warmup"), \
         patch("socialscraper.adapters.tiktok.humanize.scroll"):
        return TikTokAdapter().fetch("https://www.tiktok.com/@u/video/101", ctx)


def ig_comment(cid, answers=()):
    return types.SimpleNamespace(id=cid, text="Kommentar", owner=types.SimpleNamespace(id=4, username="u"),
                                 created_at_utc=dt.datetime(2026, 10, 11), likes_count=1, answers=answers)


def ig_post(comments=(), count=3):
    return types.SimpleNamespace(shortcode="A", owner_username="owner", owner_id=1, caption="Video",
        date_utc=dt.datetime(2026, 10, 11), video_duration=4, video_view_count=42, likes=2,
        comments=count, caption_hashtags=[], caption_mentions=[], url="thumbnail", typename="GraphVideo",
        is_video=True, is_sponsored=False, get_comments=lambda: iter(comments))


def failing_iterator(error, first=()):
    yield from first
    raise error


class FetchResultTests(unittest.TestCase):
    def setUp(self):
        self.video = Video("youtube", "v", "https://youtu.be/v")
        self.comment = Comment("youtube", "v", "c", "Text")

    def test_result_remains_unpackable_and_explicit_status_survives_normalization(self):
        result = FetchResult(self.video, [self.comment], "partial", "Zeitüberschreitung")
        video, comments = result
        self.assertIs(video, self.video)
        self.assertEqual(comments, [self.comment])
        self.assertIs(normalize_result(result, 10), result)

    def test_old_adapters_are_conservative_for_unknown_or_positive_count(self):
        for count in (None, 3):
            self.video.comment_count = count
            self.assertEqual(normalize_result((self.video, []), 5).comments_status, "unavailable")

    def test_legacy_metadata_only_and_platform_zero_have_different_status(self):
        self.video.comment_count = 0
        self.assertEqual(normalize_result((self.video, []), 0).comments_status, "not_requested")
        self.assertEqual(normalize_result((self.video, []), 5).comments_status, "empty")

    def test_legacy_comments_are_received_and_missing_video_is_not_empty(self):
        self.assertEqual(normalize_result((self.video, [self.comment]), 5).comments_status, "received")
        self.assertEqual(normalize_result((None, []), 5).comments_status, "unavailable")

    def test_no_callback_does_not_write_to_the_store(self):
        ctx = context()
        ctx.store = Mock()
        checkpoint(ctx, self.video, [self.comment])
        ctx.store.save_result.assert_not_called()

    def test_callback_gets_a_separate_list_snapshot(self):
        received = []
        ctx = context(callback=lambda video, comments: received.append(comments))
        comments = [self.comment]
        checkpoint(ctx, self.video, comments)
        comments.clear()
        self.assertEqual(received, [[self.comment]])


class TikTokResultTests(unittest.TestCase):
    def test_metadata_only_and_proven_zero_never_query_comments(self):
        for limit, count, expected in ((0, 8, "not_requested"), (5, 0, "empty")):
            with self.subTest(expected=expected):
                page, saved = FakePage(count=count), []
                result = fetch_tiktok(page, context(limit, page, lambda video, comments: saved.append((video, comments))))
                self.assertEqual(result.comments_status, expected)
                self.assertEqual(page.selector_calls, 0)
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0][1], [])

    def test_selector_timeout_preserves_metadata_and_does_not_report_zero(self):
        page = FakePage(count=10, selector_error=TimeoutError("timed out"))
        with Store(":memory:") as store:
            result = fetch_tiktok(page, context(page=page, callback=lambda v, c: store.save_result(v, c, None)))
            self.assertEqual(result.comments_status, "unavailable")
            self.assertIn("erneut", result.message.lower().replace("noch einmal", "erneut"))
            self.assertEqual(store.stats(), {"videos": {"tiktok": 1}, "comments": {}})

    def test_selector_timeout_with_captcha_has_concrete_next_step(self):
        page = FakePage(count=9, selector_error=TimeoutError("timed out"), body="Drag the slider CAPTCHA")
        result = fetch_tiktok(page, context(page=page))
        self.assertEqual(result.comments_status, "unavailable")
        self.assertIn("CAPTCHA", result.message)
        self.assertIn("Anmeldefenster", result.message)

    def test_missing_hydration_with_captcha_is_a_failed_video_result(self):
        page = FakePage(body="Verify to continue")
        page.raw = None
        result = fetch_tiktok(page, context(page=page))
        self.assertIsNone(result.video)
        self.assertIn("Sicherheitsabfrage", result.message)

    def test_no_rows_with_unknown_or_positive_count_is_unavailable(self):
        for count in (None, 5):
            page = FakePage(count=count)
            self.assertEqual(fetch_tiktok(page, context(page=page)).comments_status, "unavailable")

    def test_stalled_comment_loading_is_partial_for_known_and_unknown_total(self):
        for count in (100, None):
            with self.subTest(count=count):
                page = FakePage(count=count, batches=[[{"id": "c1", "text": "eins"}]])
                result = fetch_tiktok(page, context(limit=20, page=page))
                self.assertEqual(result.comments_status, "partial")
                self.assertEqual(len(result.comments), 1)
                self.assertIn("erneut", result.message)

    def test_scroll_round_limit_before_target_is_partial(self):
        batches = [[{"id": str(index), "text": str(index)}] for index in range(20)]
        page = FakePage(count=100, batches=batches)
        result = fetch_tiktok(page, context(limit=30, page=page))
        self.assertEqual(result.comments_status, "partial")
        self.assertEqual(len(result.comments), 20)

    def test_known_smaller_total_and_reached_limit_are_successful(self):
        for count, limit in ((1, 20), (100, 1)):
            with self.subTest(count=count, limit=limit):
                page = FakePage(count=count, batches=[[{"id": "c1", "text": "eins"}]])
                result = fetch_tiktok(page, context(limit=limit, page=page))
                self.assertEqual(result.comments_status, "received")

    def test_comment_timeout_returns_and_saves_known_comments(self):
        page = FakePage(count=5, batches=[[{"id": "c1", "text": "eins"}], TimeoutError("timed out")])
        with Store(":memory:") as store:
            result = fetch_tiktok(page, context(page=page, callback=lambda v, c: store.save_result(v, c, None)))
            self.assertEqual(result.comments_status, "partial")
            self.assertEqual([c.comment_id for c in result.comments], ["c1"])
            self.assertEqual(store.stats()["comments"], {"tiktok": 1})

    def test_session_loss_during_comments_keeps_already_saved_comment(self):
        page = FakePage(count=5, batches=[[{"id": "c1", "text": "eins"}]], session_states=[False, False, True])
        ctx = context(page=page, authenticated=True, require_auth=True)
        with patch("socialscraper.net.session_guard.invalidate") as invalidate, \
             patch("socialscraper.net.notify.notify"):
            result = fetch_tiktok(page, ctx)
        self.assertEqual(result.comments_status, "partial")
        self.assertIn("anmelden", result.message)
        self.assertFalse(ctx.authenticated)
        invalidate.assert_called_once_with("default", "tiktok")

    def test_optional_auth_loss_does_not_look_like_empty_success(self):
        page = FakePage(count=5, session_states=[True])
        ctx = context(page=page, authenticated=True)
        with patch("socialscraper.net.session_guard.invalidate"), patch("socialscraper.net.notify.notify"):
            result = fetch_tiktok(page, ctx)
        self.assertEqual(result.comments_status, "unavailable")
        self.assertIsNotNone(result.video)
        self.assertIn("anmelden", result.message)

    def test_hard_abort_retains_checkpointed_video_and_comment(self):
        page = FakePage(count=5, batches=[[{"id": "c1", "text": "eins"}], Aborted()])
        with Store(":memory:") as store:
            with self.assertRaises(Aborted):
                fetch_tiktok(page, context(page=page, callback=lambda v, c: store.save_result(v, c, None)))
            self.assertEqual(store.stats(), {"videos": {"tiktok": 1}, "comments": {"tiktok": 1}})

    def test_checkpoint_error_surfaces_instead_of_returning_fetch_success(self):
        page = FakePage(count=5)
        with self.assertRaisesRegex(OSError, "Speicherung"):
            fetch_tiktok(page, context(page=page, callback=Mock(side_effect=OSError("Speicherung fehlgeschlagen"))))


class InstagramResultTests(unittest.TestCase):
    def read(self, post, ctx=None, limit=5):
        with patch("socialscraper.adapters.instagram.time.sleep"):
            return InstagramAdapter()._read_post(post, "https://instagram.com/reel/A/", limit, ctx)

    def test_metadata_only_and_platform_zero_do_not_query_comments(self):
        for limit, count, expected in ((0, 3, "not_requested"), (5, 0, "empty")):
            post = ig_post(count=count)
            post.get_comments = Mock(side_effect=AssertionError("Kommentare angefragt"))
            self.assertEqual(self.read(post, limit=limit).comments_status, expected)
            post.get_comments.assert_not_called()

    def test_unknown_empty_iterator_is_unavailable(self):
        self.assertEqual(self.read(ig_post(count=None)).comments_status, "unavailable")

    def test_parent_is_saved_before_failing_reply_request(self):
        post = ig_post([ig_comment(1, failing_iterator(TimeoutError("timed out")))])
        with Store(":memory:") as store:
            result = self.read(post, context(callback=lambda v, c: store.save_result(v, c, None)))
            self.assertEqual(result.comments_status, "partial")
            self.assertEqual(store.stats()["comments"], {"instagram": 1})

    def test_session_loss_marks_account_unconfirmed_and_preserves_metadata(self):
        class LoginRequiredException(Exception):
            pass
        post = ig_post(failing_iterator(LoginRequiredException("Please login")))
        ctx = context(authenticated=True)
        with patch("socialscraper.net.session_guard.invalidate") as invalidate:
            result = self.read(post, ctx)
        self.assertEqual(result.comments_status, "unavailable")
        self.assertIn("Anmeldung", result.message)
        self.assertFalse(ctx.authenticated)
        invalidate.assert_called_once_with("default", "instagram")

    def test_hard_abort_during_reply_request_retains_parent(self):
        post = ig_post([ig_comment(1, failing_iterator(Aborted()))])
        with Store(":memory:") as store:
            with self.assertRaises(Aborted):
                self.read(post, context(callback=lambda v, c: store.save_result(v, c, None)))
            self.assertEqual(store.stats(), {"videos": {"instagram": 1}, "comments": {"instagram": 1}})

    def test_success_is_received_without_claiming_full_inventory(self):
        result = self.read(ig_post([ig_comment(1, [ig_comment(2)])]), limit=2)
        self.assertEqual(result.comments_status, "received")
        self.assertEqual(len(result.comments), 2)

    def test_known_shortfall_after_iteration_is_partial(self):
        result = self.read(ig_post([ig_comment(1)], count=100), limit=20)
        self.assertEqual(result.comments_status, "partial")
        self.assertIn("erneut", result.message)

    def test_known_smaller_total_is_received(self):
        result = self.read(ig_post([ig_comment(1)], count=1), limit=20)
        self.assertEqual(result.comments_status, "received")


class YouTubeResultTests(unittest.TestCase):
    def fetch(self, info, limit=5, warning="", callback=None):
        class FakeYDL:
            def __init__(self, options):
                self.options = options
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def extract_info(self, *args, **kwargs):
                if warning:
                    self.options["logger"].warning(warning)
                return info
            def process_ie_result(self, result, download=False):
                return result
        with patch.dict(sys.modules, {"yt_dlp": types.SimpleNamespace(YoutubeDL=FakeYDL)}), \
             patch("socialscraper.adapters.youtube.youtube_runtime_options", return_value={"js_runtimes": {"node": {}}}):
            return YouTubeAdapter().fetch("https://youtu.be/v", context(limit, callback=callback))

    def test_zero_request_proven_zero_and_missing_comments_are_distinct(self):
        for limit, count, expected in ((0, 3, "not_requested"), (5, 0, "empty"),
                                       (5, 3, "unavailable"), (5, None, "unavailable")):
            with self.subTest(expected=expected, count=count):
                self.assertEqual(self.fetch({"id": "v", "comment_count": count}, limit).comments_status, expected)

    def test_comment_warning_with_known_comments_is_partial_and_saved(self):
        saved = []
        result = self.fetch({"id": "v", "comments": [{"id": "c", "text": "Text"}]},
                            warning="Unable to download comments: timed out", callback=lambda v, c: saved.append(c))
        self.assertEqual(result.comments_status, "partial")
        self.assertEqual([len(batch) for batch in saved], [0, 1])

    def test_comment_warning_without_comments_is_unavailable_even_with_zero_count(self):
        result = self.fetch({"id": "v", "comment_count": 0}, warning="Could not retrieve comments")
        self.assertEqual(result.comments_status, "unavailable")

    def test_unrelated_warning_does_not_change_received_status(self):
        result = self.fetch({"id": "v", "comments": [{"id": "c", "text": "Text"}]}, warning="Thumbnail missing")
        self.assertEqual(result.comments_status, "received")

    def test_known_shortfall_is_partial_even_without_extractor_warning(self):
        result = self.fetch({"id": "v", "comment_count": 100,
                             "comments": [{"id": "c", "text": "Text"}]}, limit=20)
        self.assertEqual(result.comments_status, "partial")
        self.assertIn("erneut", result.message)

    def test_page_comment_count_survives_the_comment_extraction(self):
        """yt-dlp ersetzt comment_count nach dem Kommentarabruf durch die Zahl der geholten Kommentare (oder None);
        die Zahl der Seite muss aus dem unverarbeiteten Ergebnis gerettet werden."""
        def run(page_count, fetched, limit):
            class FakeYDL:
                def __init__(self, options):
                    pass
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def extract_info(self, url, download=False, process=True):
                    assert process is False, "erst unverarbeitet lesen"
                    return {"id": "v", "comment_count": page_count}
                def process_ie_result(self, result, download=False):
                    comments = [{"id": str(i), "text": "t"} for i in range(fetched)]
                    return {**result, "comments": comments, "comment_count": len(comments)}
            with patch.dict(sys.modules, {"yt_dlp": types.SimpleNamespace(YoutubeDL=FakeYDL)}), \
                 patch("socialscraper.adapters.youtube.youtube_runtime_options", return_value={"js_runtimes": {"node": {}}}):
                return YouTubeAdapter().fetch("https://youtu.be/v", context(limit))
        result = run(20000, 5, 5)
        self.assertEqual((result.video.comment_count, result.comments_status), (20000, "received"))
        result = run(20000, 3, 5)
        self.assertEqual((result.video.comment_count, result.comments_status), (20000, "partial"))
        # Ohne Zahl der Seite: die geholte Zahl gilt nur, wenn die Grenze nicht erreicht wurde.
        self.assertEqual(run(None, 5, 5).video.comment_count, None)
        self.assertEqual(run(None, 3, 5).video.comment_count, 3)

    def test_known_smaller_total_and_reached_limit_are_successful(self):
        for count, limit in ((1, 20), (100, 1)):
            result = self.fetch({"id": "v", "comment_count": count,
                                 "comments": [{"id": "c", "text": "Text"}]}, limit=limit)
            self.assertEqual(result.comments_status, "received")


if __name__ == "__main__":
    unittest.main()
