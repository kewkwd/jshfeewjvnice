import datetime as dt
import subprocess
import sys
import types
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from socialscraper.adapters import resolve
from socialscraper.adapters.base import ScrapeContext
from socialscraper.adapters.instagram import InstagramAdapter
from socialscraper.adapters.tiktok import COMMENTS_JS, VIDEO_JS, TikTokAdapter
from socialscraper.adapters.youtube import YouTubeAdapter
from socialscraper.config import Settings
from socialscraper.net.session_guard import SessionExpired
from socialscraper.util import comment_id, parse_short_number


class FakePage:
    def __init__(self, batches=None, links=None, logged_out=False):
        self.batches = batches or []
        self.links = links or []
        self.logged_out = logged_out
        self.batch_calls = 0
        self.selector_calls = 0
        self.urls = []

    def goto(self, url, **kwargs):
        self.urls.append(url)

    def wait_for_timeout(self, milliseconds):
        pass

    def wait_for_selector(self, selector, **kwargs):
        self.selector_calls += 1

    def evaluate(self, expression):
        if expression == VIDEO_JS:
            return {"id": "101", "stats": {"playCount": "3.4K"}}
        if expression == COMMENTS_JS:
            index = min(self.batch_calls, len(self.batches) - 1)
            self.batch_calls += 1
            return self.batches[index] if self.batches else []
        if expression == TikTokAdapter.PROFILE_JS:
            return self.links
        if "top-login-button" in expression:
            return self.logged_out
        raise AssertionError("Unexpected browser expression")


class FakePool:
    def __init__(self, page):
        self.page = page
        self.calls = []

    @contextmanager
    def acquire(self, *args):
        self.calls.append(args)
        yield self.page


def context(max_comments=10, page=None, **kwargs):
    return ScrapeContext(cfg=Settings(max_comments=max_comments), store=None, im=None,
                         sessions=FakePool(page) if page else None, **kwargs)


class AdapterSurfaceTests(unittest.TestCase):
    def test_importing_adapters_does_not_import_optional_clients(self):
        code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'yt_dlp', 'instaloader', 'patchright', 'nodriver', 'curl_cffi', 'rebrowser_playwright'}:
        raise AssertionError('Eager optional import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from socialscraper.adapters import resolve
assert resolve('https://youtu.be/123').platform == 'youtube'
"""
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_resolve_checks_actual_hostname(self):
        for url, platform in (
            ("https://WWW.YouTube.com/watch?v=1", "youtube"),
            ("https://youtu.be/1", "youtube"),
            ("https://vm.tiktok.com/short", "tiktok"),
            ("https://www.instagram.com/reel/Ab_C-1/", "instagram"),
        ):
            with self.subTest(url=url):
                self.assertEqual(resolve(url).platform, platform)
        for url in ("https://youtube.com.evil.test/", "https://evil.test/?youtube.com",
                    "https://instagram.com@evil.test/p/A/", "file://tiktok.com/x",
                    "https://tiktok.com:invalid/", "https://evil.test\\x.tiktok.com/", None):
            with self.subTest(url=url):
                self.assertIsNone(resolve(url))

    def test_auth_requirements_and_safe_context_default(self):
        self.assertFalse(YouTubeAdapter().needs_auth)
        self.assertTrue(TikTokAdapter().needs_auth)
        self.assertTrue(InstagramAdapter().needs_auth)
        self.assertFalse(InstagramAdapter().needs_browser)
        self.assertFalse(context().authenticated)

    def test_count_formats_keep_units_and_locales(self):
        for value, expected in (("1.2K", 1200), ("3,4 Mio.", 3400000),
                                ("2 Tsd.", 2000), ("1 234", 1234),
                                ("1\u202f234", 1234), ("1.234", 1234),
                                ("1,234", 1234), ("1,234.56", 1234),
                                ("1.234,56", 1234), ("1.234K", 1234),
                                ("1.1K", 1100), ("1.2 nonsense", None),
                                (float("nan"), None), ("", None), (None, None)):
            with self.subTest(value=value):
                self.assertEqual(parse_short_number(value), expected)

    def test_comment_fallback_is_stable_and_scoped_to_video(self):
        first = comment_id("1", "user", "same")
        self.assertEqual(first, comment_id("1", "user", "same"))
        self.assertNotEqual(first, comment_id("2", "user", "same"))
        self.assertEqual(comment_id("1", "user", "same", 0), "0")


class YouTubeTests(unittest.TestCase):
    def fetch(self, info, limit, cfg_context=None):
        calls = []
        class FakeYDL:
            def __init__(self, options):
                calls.append(options)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def extract_info(self, url, download, process=True):
                return info
            def process_ie_result(self, result, download=False):
                return result
        module = types.SimpleNamespace(YoutubeDL=FakeYDL)
        cfg_context = cfg_context or context(max_comments=limit)
        cfg_context.cfg.impersonate = ""
        with patch.dict(sys.modules, {"yt_dlp": module}), \
             patch("socialscraper.adapters.youtube.youtube_runtime_options", return_value={"js_runtimes": {"node": {}}}):
            result = YouTubeAdapter().fetch("https://youtu.be/v1", cfg_context)
        return result, calls

    def test_comment_limit_is_applied_to_client_and_result(self):
        result, calls = self.fetch({"id": "v1", "comments": [
            {"id": "a", "text": "one"}, {"id": "a", "text": "one"},
            {"id": "b", "text": "two", "parent": "a"},
            {"id": "c", "text": "three"}]}, 2)
        video, comments = result
        self.assertEqual([c.comment_id for c in comments], ["a", "b"])
        self.assertEqual(comments[1].parent_id, "a")
        self.assertEqual(calls[0]["extractor_args"]["youtube"]["max_comments"], ["2"])
        self.assertTrue(calls[0]["noplaylist"])

    def test_zero_comments_disables_collection(self):
        (video, comments), calls = self.fetch({"id": "v1", "comments": [{"id": "c"}]}, 0)
        self.assertEqual(comments, [])
        self.assertFalse(calls[0]["getcomments"])

    def test_missing_comment_id_is_stable_and_deduplicated(self):
        (video, comments), calls = self.fetch({"id": "v1", "comments": [
            {"author": "u", "text": "x"}, {"author": "u", "text": "x"}]}, 5)
        self.assertEqual(len(comments), 1)
        self.assertTrue(comments[0].comment_id.startswith("text:"))

    def test_playlist_result_is_rejected_without_retries(self):
        with self.assertRaisesRegex(ValueError, "einzelnes Video"):
            self.fetch({"id": "playlist", "entries": []}, 1)

    def test_configured_proxy_is_selected_for_identity_and_passed_to_client(self):
        ctx = context(max_comments=0, identity="yt_user", country="de", mobile=True)
        ctx.cfg.proxies = ["http://example.invalid:8080"]
        ctx.im = unittest.mock.Mock()
        selected = types.SimpleNamespace(url="http://selected.invalid:8080")
        ctx.im.proxy_for.return_value = selected
        result, calls = self.fetch({"id": "v1"}, 0, ctx)
        ctx.im.get.assert_called_once_with("yt_user", "de", True)
        ctx.im.proxy_for.assert_called_once_with(ctx.im.get.return_value)
        self.assertEqual(calls[0]["proxy"], selected.url)

    def test_impersonate_uses_documented_target_type(self):
        class FakeTarget:
            @classmethod
            def from_str(cls, text):
                result = cls()
                result.text = text
                return result
        modules = {"curl_cffi": types.ModuleType("curl_cffi"),
                   "yt_dlp": types.ModuleType("yt_dlp"),
                   "yt_dlp.networking": types.ModuleType("yt_dlp.networking"),
                   "yt_dlp.networking.impersonate": types.SimpleNamespace(ImpersonateTarget=FakeTarget)}
        cfg = Settings(impersonate="chrome")
        with patch.dict(sys.modules, modules):
            options = YouTubeAdapter()._opts(cfg)
        self.assertIsInstance(options["impersonate"], FakeTarget)
        self.assertEqual(options["impersonate"].text, "chrome")

    def test_missing_impersonation_dependency_warns_instead_of_silent_fallback(self):
        with patch.dict(sys.modules, {"curl_cffi": None}), \
             self.assertLogs("socialscraper.adapters.youtube", level="WARNING") as output:
            options = YouTubeAdapter()._opts(Settings(impersonate="chrome"))
        self.assertNotIn("impersonate", options)
        self.assertIn("curl_cffi", output.output[0])


class TikTokTests(unittest.TestCase):
    def run_fetch(self, page, limit=10, **kwargs):
        ctx = context(max_comments=limit, page=page, **kwargs)
        with patch("socialscraper.adapters.tiktok.humanize.warmup"), \
             patch("socialscraper.adapters.tiktok.humanize.scroll"):
            video, comments = TikTokAdapter().fetch("https://tiktok.com/@u/video/101", ctx)
        return video, comments, ctx

    def test_repeated_dom_rows_without_ids_are_deduplicated(self):
        one = {"author": "u", "text": " same ", "likes": "3,4 Mio."}
        other = {"author": "v", "text": "second", "likes": "2K"}
        page = FakePage(batches=[[one], [one, other], [one, other]])
        video, comments, ctx = self.run_fetch(page)
        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[0].like_count, 3400000)
        self.assertEqual(video.view_count, 3400)
        self.assertLess(page.batch_calls, 20)
        self.assertFalse(ctx.sessions.calls[0][3])

    def test_explicit_ids_keep_distinct_identical_comments(self):
        page = FakePage(batches=[[{"id": "a", "author": "u", "text": "x"},
                                 {"id": "b", "author": "u", "text": "x"},
                                 {"id": "c", "author": "u", "text": "x"}]])
        video, comments, ctx = self.run_fetch(page, limit=2, mobile=True)
        self.assertEqual([c.comment_id for c in comments], ["a", "b"])
        self.assertTrue(ctx.sessions.calls[0][3])

    def test_stored_url_is_final_page_without_tracking(self):
        page = FakePage()
        page.url = "https://www.tiktok.com/@u/video/101?is_from_webapp=1&sender_device=pc"
        video, comments, ctx = self.run_fetch(page, limit=0)
        self.assertEqual(video.url, "https://www.tiktok.com/@u/video/101")

    def test_zero_comments_does_not_wait_for_selector(self):
        page = FakePage()
        video, comments, ctx = self.run_fetch(page, limit=0)
        self.assertEqual(comments, [])
        self.assertEqual(page.selector_calls, 0)

    def test_runtime_session_loss_warns_and_invalidates(self):
        page = FakePage(logged_out=True)
        with patch("socialscraper.net.session_guard.invalidate") as invalidate, \
             patch("socialscraper.net.notify.notify") as notify:
            video, comments, ctx = self.run_fetch(page, limit=0, authenticated=True)
        self.assertFalse(ctx.authenticated)
        invalidate.assert_called_once_with("default", "tiktok")
        notify.assert_called_once()

    def test_required_auth_loss_aborts_without_retry(self):
        page = FakePage(logged_out=True)
        ctx = context(page=page, authenticated=True, require_auth=True)
        with patch("socialscraper.adapters.tiktok.humanize.warmup"), \
             patch("socialscraper.net.session_guard.invalidate"), \
             patch("socialscraper.net.notify.notify"), \
             patch("socialscraper.net.retry.time.sleep") as sleep:
            with self.assertRaises(SessionExpired):
                TikTokAdapter().fetch("https://tiktok.com/@u/video/101", ctx)
        self.assertFalse(ctx.authenticated)
        self.assertEqual(len(ctx.sessions.calls), 1)
        sleep.assert_not_called()

    def test_profile_keeps_only_requested_accounts_unique_videos(self):
        page = FakePage(links=["https://www.tiktok.com/@u/video/1?x=1",
                               "https://m.tiktok.com/@u/video/1",
                               "https://tiktok.com/@other/video/2",
                               "https://tiktok.com.evil.test/@u/video/3",
                               "https://tiktok.com/@u/video/4"])
        ctx = context(page=page)
        with patch("socialscraper.adapters.tiktok.humanize.warmup"), \
             patch("socialscraper.adapters.tiktok.humanize.scroll"):
            urls = TikTokAdapter().profile_urls("@u", ctx, limit=2)
        self.assertEqual(urls, ["https://www.tiktok.com/@u/video/1", "https://tiktok.com/@u/video/4"])

    def test_profile_zero_limit_needs_no_browser(self):
        self.assertEqual(TikTokAdapter().profile_urls("u", context(), limit=0), [])


class InstagramTests(unittest.TestCase):
    def comment(self, identity, answers=None):
        return types.SimpleNamespace(id=identity, text="text", owner=types.SimpleNamespace(id=9, username="u"),
                                     created_at_utc=dt.datetime(2024, 1, 1), likes_count="1.2K",
                                     answers=iter(answers or []))

    def post(self, comments):
        return types.SimpleNamespace(shortcode="A", owner_username="owner", owner_id=1,
                caption="caption", date_utc=dt.datetime(2024, 1, 1), video_duration=2.3,
                video_view_count="1.2K", likes="2.4K", comments="3,4 Mio.",
                caption_hashtags=[], caption_mentions=[], url="thumbnail", typename="GraphVideo",
                is_video=True, is_sponsored=False, get_comments=lambda: iter(comments))

    def test_shortcode_uses_post_path_and_rejects_profiles(self):
        self.assertEqual(InstagramAdapter.shortcode("https://www.instagram.com/reel/A?x=1#f"), "A")
        for url in ("https://instagram.com/u/", "https://evil.test/p/A/",
                    "https://instagram.com/p/A/comments/", "https://instagram.com/p/%2f/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                InstagramAdapter.shortcode(url)

    def test_answers_stop_exactly_at_limit_and_dates_are_utc(self):
        def answers():
            yield self.comment(2)
            raise AssertionError("Fetched a reply beyond max_comments")
        parent = self.comment(1)
        parent.answers = answers()
        post = self.post([parent])
        video, comments = InstagramAdapter()._read_post(post, "https://instagram.com/reel/A/?x=1", 2)
        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[1].parent_id, "1")
        self.assertEqual(comments[0].created_at, "2024-01-01T00:00:00+00:00")
        self.assertEqual(video.created_at, "2024-01-01T00:00:00+00:00")
        self.assertEqual(video.comment_count, 3400000)
        self.assertEqual(video.url, "https://instagram.com/reel/A/")

    def test_zero_limit_never_requests_comments(self):
        post = self.post([])
        post.get_comments = lambda: self.fail("Comments fetched despite zero limit")
        video, comments = InstagramAdapter()._read_post(post, "https://instagram.com/p/A/", 0)
        self.assertEqual(comments, [])

    def test_repeated_comment_ids_are_deduplicated(self):
        post = self.post([self.comment(1), self.comment(1), self.comment(2)])
        with patch("socialscraper.adapters.instagram.time.sleep"):
            video, comments = InstagramAdapter()._read_post(post, "https://instagram.com/p/A/", 2)
        self.assertEqual([c.comment_id for c in comments], ["1", "2"])

    def test_invalid_session_does_not_fetch_post_and_closes_client(self):
        loader = types.SimpleNamespace(load_session_from_file=lambda *a: None,
                                       test_login=lambda: None, close=unittest.mock.Mock())
        post_factory = unittest.mock.Mock()
        module = types.SimpleNamespace(Instaloader=lambda **kwargs: loader,
                                       Post=types.SimpleNamespace(from_shortcode=post_factory))
        ctx = context()
        ctx.cfg.ig_session_user = "user"
        with patch.dict(sys.modules, {"instaloader": module}), \
             patch("socialscraper.net.cookies.CookieJar.load", return_value=[]), \
             patch("socialscraper.net.retry.time.sleep") as sleep:
            with self.assertRaises(SessionExpired):
                InstagramAdapter().fetch("https://instagram.com/p/A/", ctx)
        post_factory.assert_not_called()
        loader.close.assert_called_once()
        sleep.assert_not_called()

    def test_missing_post_is_not_retried(self):
        class NotFound(Exception):
            pass
        exceptions = types.SimpleNamespace(QueryReturnedNotFoundException=NotFound,
                                           PrivateProfileNotFollowedException=KeyError,
                                           ProfileNotExistsException=KeyError,
                                           LoginRequiredException=KeyError)
        loader = types.SimpleNamespace(load_session_from_file=unittest.mock.Mock(),
                load_session=unittest.mock.Mock(), test_login=lambda: "confirmed_user",
                context=types.SimpleNamespace(username=None, _session=types.SimpleNamespace(proxies={})),
                close=unittest.mock.Mock())
        post_factory = unittest.mock.Mock(side_effect=NotFound("404"))
        module = types.SimpleNamespace(Instaloader=lambda **kwargs: loader, exceptions=exceptions,
                                       Post=types.SimpleNamespace(from_shortcode=post_factory))
        cookies = [{"name": "sessionid", "value": "s", "domain": ".instagram.com"},
                   {"name": "csrftoken", "value": "c", "domain": ".instagram.com"}]
        from socialscraper.net.retry import PermanentFetchError
        with patch.dict(sys.modules, {"instaloader": module}),              patch("socialscraper.net.cookies.CookieJar.load", return_value=cookies),              patch("socialscraper.net.retry.time.sleep") as sleep:
            with self.assertRaises(PermanentFetchError):
                InstagramAdapter().fetch("https://instagram.com/p/A/", context())
        post_factory.assert_called_once()
        sleep.assert_not_called()

    def test_browser_cookies_use_public_session_api_without_configured_user(self):
        # Import before patch.dict(sys.modules), which restores the module table.
        # Otherwise lazy Requests imports leave mixed CookieJar classes behind.
        import requests
        loader = types.SimpleNamespace(load_session_from_file=unittest.mock.Mock(),
                load_session=unittest.mock.Mock(), test_login=lambda: "confirmed_user",
                context=types.SimpleNamespace(username=None, _session=types.SimpleNamespace(proxies={}),
                                              get_json=unittest.mock.Mock(),get_anonymous_session=unittest.mock.Mock()),
                close=unittest.mock.Mock())
        post_factory = unittest.mock.Mock(return_value=self.post([]))
        module = types.SimpleNamespace(Instaloader=lambda **kwargs: loader,
                                       Post=types.SimpleNamespace(from_shortcode=post_factory))
        cookies = [{"name": "sessionid", "value": "fake-session", "domain": ".instagram.com"},
                   {"name": "csrftoken", "value": "fake-csrf", "domain": ".instagram.com"}]
        ctx = context(max_comments=0, mobile=True)
        ctx.im = unittest.mock.Mock()
        ctx.im.proxy_for.return_value = types.SimpleNamespace(url="http://example.invalid:8080")
        with patch.dict(sys.modules, {"instaloader": module}), \
             patch("socialscraper.net.cookies.CookieJar.load", return_value=cookies) as cookie_load:
            video, comments = InstagramAdapter().fetch("https://instagram.com/p/A/", ctx)
        cookie_load.assert_called_once_with("default", "instagram")
        loader.load_session_from_file.assert_not_called()
        loader.load_session.assert_called_once_with("", {"sessionid": "fake-session", "csrftoken": "fake-csrf"})
        self.assertEqual(loader.context.username, "confirmed_user")
        self.assertTrue(ctx.authenticated)
        ctx.im.get.assert_called_once_with("default", "us", True)
        self.assertEqual(loader.context._session.proxies["https"], "http://example.invalid:8080")
        post_factory.assert_called_once()
        loader.close.assert_called_once()

    def test_browser_cookie_user_mismatch_aborts_without_post_or_retry(self):
        loader = types.SimpleNamespace(
                load_session_from_file=unittest.mock.Mock(side_effect=FileNotFoundError),
                load_session=unittest.mock.Mock(), test_login=lambda: "other_user",
                context=types.SimpleNamespace(username=None), close=unittest.mock.Mock())
        post_factory = unittest.mock.Mock()
        module = types.SimpleNamespace(Instaloader=lambda **kwargs: loader,
                                       Post=types.SimpleNamespace(from_shortcode=post_factory))
        cookies = [{"name": "sessionid", "value": "fake-session", "domain": ".instagram.com"},
                   {"name": "csrftoken", "value": "fake-csrf", "domain": ".instagram.com"}]
        ctx = context()
        ctx.cfg.ig_session_user = "expected_user"
        with patch.dict(sys.modules, {"instaloader": module}), \
             patch("socialscraper.net.cookies.CookieJar.load", return_value=cookies), \
             patch("socialscraper.net.retry.time.sleep") as sleep:
            with self.assertRaisesRegex(SessionExpired, "anderen Benutzer"):
                InstagramAdapter().fetch("https://instagram.com/p/A/", ctx)
        self.assertFalse(ctx.authenticated)
        post_factory.assert_not_called()
        loader.close.assert_called_once()
        sleep.assert_not_called()

    def test_valid_instaloader_file_is_preferred_to_browser_cookies(self):
        loader = types.SimpleNamespace(load_session_from_file=unittest.mock.Mock(),
                load_session=unittest.mock.Mock(), test_login=lambda: "expected_user",
                context=types.SimpleNamespace(username=None), close=unittest.mock.Mock())
        module = types.SimpleNamespace(Instaloader=lambda **kwargs: loader,
                    Post=types.SimpleNamespace(from_shortcode=lambda *args: self.post([])))
        ctx = context(max_comments=0)
        ctx.cfg.ig_session_user = "expected_user"
        with patch.dict(sys.modules, {"instaloader": module}), \
             patch("socialscraper.net.cookies.CookieJar.load") as cookie_load:
            InstagramAdapter().fetch("https://instagram.com/p/A/", ctx)
        cookie_load.assert_not_called()
        loader.load_session.assert_not_called()
        loader.load_session_from_file.assert_called_once_with("expected_user", ctx.cfg.ig_session_file)


if __name__ == "__main__":
    unittest.main()
