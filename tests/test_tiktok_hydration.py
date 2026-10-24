"""Exercise the extraction JavaScript against local HTML, without network access."""
import json
import unittest

from socialscraper.adapters.tiktok import COMMENTS_JS, VIDEO_JS


class TikTokHydrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from patchright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("Local browser checks require patchright")
        cls.engine = sync_playwright().start()
        try:
            cls.browser = cls.engine.chromium.launch(channel="chrome", headless=True)
        except Exception as error:
            cls.engine.stop()
            raise unittest.SkipTest("Local Chrome is not available") from error
        cls.page = cls.browser.new_page()
        cls.page.route("**/*", lambda route: route.abort())

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.engine.stop()

    def payload(self):
        return {"__DEFAULT_SCOPE__": {"webapp.video-detail": {"itemInfo": {"itemStruct": {
            "id": "123", "desc": "Public caption", "createTime": 100,
            "author": {"uniqueId": "public_user", "id": "9"},
            "stats": {"playCount": 1200, "commentCount": 5},
            "video": {"duration": 12}, "music": {"title": "Track"},
            "challenges": [{"title": "test"}]}}}}}

    def setUp(self):
        self.page.goto("about:blank")

    def assert_video(self):
        result = self.page.evaluate(VIDEO_JS)
        self.assertEqual(result["id"], "123")
        self.assertEqual(result["author"], "public_user")
        self.assertEqual(result["stats"]["playCount"], 1200)
        self.assertEqual(result["duration"], 12)
        self.assertEqual(result["challenges"], ["test"])

    def test_existing_window_object_is_preserved(self):
        self.page.evaluate("data => window.__UNIVERSAL_DATA_FOR_REHYDRATION__ = data", self.payload())
        self.assert_video()

    def test_script_element_window_property_is_parsed_as_json(self):
        self.page.set_content('<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
                              + json.dumps(self.payload()) + '</script>')
        self.assertTrue(self.page.evaluate("window.__UNIVERSAL_DATA_FOR_REHYDRATION__ instanceof HTMLScriptElement"))
        self.assert_video()

    def test_empty_window_object_falls_back_to_script_json(self):
        self.page.set_content('<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
                              + json.dumps(self.payload()) + '</script>')
        self.page.evaluate("window.__UNIVERSAL_DATA_FOR_REHYDRATION__ = {}")
        self.assert_video()

    def test_invalid_script_json_returns_no_video(self):
        self.page.set_content('<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{bad}</script>')
        self.assertIsNone(self.page.evaluate(VIDEO_JS))

    def test_missing_hydration_returns_no_video(self):
        self.page.set_content("<p>No hydration</p>")
        self.assertIsNone(self.page.evaluate(VIDEO_JS))

    def comment(self, user, text, likes, extra=""):
        return (f'<div class="item"{extra}><div class="content">'
                f'<a><span data-e2e="comment-username-1">{user}</span></a>'
                f'<p data-e2e="comment-level-1"><span>{text}</span></p>'
                f'<span data-e2e="comment-like-count">{likes}</span>'
                '<div class="replies"><span data-e2e="comment-username-2">r</span>'
                '<p data-e2e="comment-level-2">reply</p></div></div></div>')

    def test_comments_are_read_when_attribute_marks_the_text(self):
        self.page.set_content('<div id="list">' + self.comment("alice", "Erster", "1,2K", ' data-cid="77"')
                              + self.comment("bob", "Zweiter", "3") + '</div>')
        result = self.page.evaluate(COMMENTS_JS)
        self.assertEqual(result, [
            {"author": "alice", "text": "Erster", "likes": "1,2K", "id": "77"},
            {"author": "bob", "text": "Zweiter", "likes": "3", "id": None}])

    def test_comments_are_read_from_wrapper_layout(self):
        self.page.set_content('<div data-e2e="comment-level-1" id="generic">'
                              '<span data-e2e="comment-username-1">carol</span>'
                              '<p data-e2e="comment-level-1">Text</p>'
                              '<span data-e2e="comment-like-count">5</span></div>')
        self.assertEqual(self.page.evaluate(COMMENTS_JS),
                         [{"author": "carol", "text": "Text", "likes": "5", "id": None}])


if __name__ == "__main__":
    unittest.main()
