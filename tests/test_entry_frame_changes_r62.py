"""A replaced CAPTCHA frame must not terminate a read-only entry scan."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from socialscraper import entry_rules
from socialscraper.net.errors import FinalError


def empty_nodes():
    return SimpleNamespace(count=lambda: 0)


class Page:
    def __init__(self):
        self.url = entry_rules.SEARCH_HOME
        self.parent_frame = None
        self.frames = [self]
        self.wait_for_timeout = Mock()
        self.visits = []
        self.searches = 0
        self.clicks = 0
        self.clock = 0
        self.on_search = lambda: None

    def get_by_role(self, *args, **kwargs):
        return empty_nodes()

    def goto(self, url, **kwargs):
        self.url = url
        self.visits.append(url)

    def locator(self, selector):
        if selector.startswith('textarea'):
            def submit(*args, **kwargs):
                self.searches += 1
                self.goto('https://www.google.de/search?q=web.de')
                self.on_search()
            node = SimpleNamespace(is_visible=lambda: True, fill=Mock(), press=submit)
        elif selector == 'a:has(h3)':
            def click(**kwargs):
                self.clicks += 1
                self.goto('https://web.de/')
            node = SimpleNamespace(is_visible=lambda: True, evaluate=lambda _: 'https://web.de/', click=click)
        else:
            return empty_nodes()
        return SimpleNamespace(count=lambda: 1, nth=lambda _: node)


class FrameChanges(unittest.TestCase):
    def fixture(self):
        page = Page()
        child = SimpleNamespace(parent_frame=page, frame_element=Mock())
        page.frames.append(child)
        return page, child

    def test_removed_frame_is_rechecked_without_partial_results(self):
        page, child = self.fixture()
        def detach():
            page.frames.remove(child)
            raise RuntimeError('Frame has been detached')
        child.frame_element.side_effect = detach
        self.assertEqual(list(entry_rules.visible_frames(page)), [page])
        self.assertEqual(page.wait_for_timeout.call_count, 1)

    def test_replacement_frame_is_included_even_if_frame_count_is_unchanged(self):
        page, child = self.fixture()
        replacement = SimpleNamespace(parent_frame=page,
            frame_element=lambda: SimpleNamespace(is_visible=lambda: True))
        def replace():
            page.frames = [page, replacement]
            return SimpleNamespace(is_visible=lambda: True)
        child.frame_element.side_effect = replace
        self.assertEqual(list(entry_rules.visible_frames(page)), [page, replacement])
        page.wait_for_timeout.assert_called_once()

    def test_permanent_read_failure_is_bounded_and_never_exposes_partial_frames(self):
        page, child = self.fixture()
        child.frame_element.side_effect = RuntimeError('unreadable')
        seen = []
        with self.assertRaisesRegex(FinalError, 'Seitenfenster'):
            for frame in entry_rules.visible_frames(page):seen.append(frame)
        self.assertEqual(seen, [])
        self.assertEqual(child.frame_element.call_count, 3)
        self.assertEqual(page.wait_for_timeout.call_count, 2)

    def test_hidden_ancestor_still_hides_nested_frame(self):
        page, child = self.fixture()
        child.frame_element.return_value = SimpleNamespace(is_visible=lambda: False)
        nested = SimpleNamespace(parent_frame=child,
            frame_element=lambda: SimpleNamespace(is_visible=lambda: True))
        page.frames.append(nested)
        self.assertEqual(list(entry_rules.visible_frames(page)), [page])
        page.wait_for_timeout.assert_not_called()

    def test_navigation_during_scan_cannot_release_old_frame_snapshot(self):
        page, child = self.fixture()
        def navigate():
            page.url = 'https://www.google.de/search?q=web.de'
            page.frames = [page]
            return SimpleNamespace(is_visible=lambda: True)
        child.frame_element.side_effect = navigate
        self.assertEqual(list(entry_rules.visible_frames(page)), [page])
        page.wait_for_timeout.assert_called_once()

    def test_closed_page_during_retry_reports_controlled_failure(self):
        page, child = self.fixture()
        child.frame_element.side_effect = RuntimeError('detached')
        page.wait_for_timeout.side_effect = RuntimeError('closed PRIVATE_DETAIL')
        with self.assertRaises(FinalError) as error:list(entry_rules.visible_frames(page))
        self.assertNotIn('PRIVATE_DETAIL', str(error.exception))

    def test_search_continues_after_challenge_frame_disappears_without_resubmission(self):
        page = Page()
        child = SimpleNamespace(parent_frame=page, frame_element=Mock())
        def detach():
            page.frames = [page]
            raise RuntimeError('Frame has been detached')
        child.frame_element.side_effect = detach
        page.on_search = lambda: page.frames.append(child)
        def wait(ms):page.clock += ms / 1000
        ctx = SimpleNamespace(timeout=5, check_cancelled=Mock(), hinweis=Mock(), warte=wait)
        with patch.object(entry_rules.time, 'monotonic', side_effect=lambda: page.clock):
            entry_rules.visit_home(page, 'web.de', ctx)
        self.assertEqual(page.visits, [entry_rules.SEARCH_HOME,
            'https://www.google.de/search?q=web.de', 'https://web.de/'])
        self.assertEqual(page.searches, 1)
        self.assertEqual(page.clicks, 1)
        self.assertLess(ctx.timeout, 5)
