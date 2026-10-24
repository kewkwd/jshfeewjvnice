"""Round 38: cancellation must win over a late mailbox confirmation result."""
import contextlib
import socket
import unittest
from unittest.mock import MagicMock, patch

from socialscraper import mailbox


class MailboxCancellationTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for method in ('create_connection', 'getaddrinfo'):
            stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('External process blocked')))
        stack.enter_context(patch('socialscraper.operation.wait', side_effect=AssertionError('Unexpected wait')))
        stack.enter_context(patch.object(mailbox.time, 'sleep', side_effect=AssertionError('Real sleep blocked')))
        self.now = 100.0
        stack.enter_context(patch.object(mailbox.time, 'monotonic', side_effect=lambda: self.now))
        self.cancelled = False

    def stop(self):
        return self.cancelled

    def message(self, platform, text):
        return {'subject': platform + ' confirmation', 'text': text,
                'from': 'noreply@' + platform + '.com', 'stamp': 100.0}

    def box(self, message, *, cancel_on_fetch=False):
        box = mailbox.Mailbox()

        def fetch():
            self.now += 0.25  # A blocking transport finishes after the user cancels.
            if cancel_on_fetch:
                self.cancelled = True
            return [message]

        box.fetch = MagicMock(side_effect=fetch)
        return box

    def test_cancellation_during_fetch_rejects_an_otherwise_valid_tiktok_code(self):
        message = self.message('tiktok', 'Your TikTok verification code is 123456')
        box = self.box(message, cancel_on_fetch=True)
        with self.assertRaisesRegex(mailbox.MailboxError, 'Abgebrochen'):
            box.wait_for_code(platform='tiktok', timeout=10, stop=self.stop)
        box.fetch.assert_called_once()

    def test_cancellation_during_fetch_rejects_google_and_trustpilot_links(self):
        cases = [('google', 'https://accounts.google.com/verify?token=OFFLINE_TEST'),
                 ('trustpilot', 'https://www.trustpilot.com/confirm?token=OFFLINE_TEST')]
        for platform, link in cases:
            with self.subTest(platform=platform):
                self.cancelled = False
                box = self.box(self.message(platform, link), cancel_on_fetch=True)
                with self.assertRaisesRegex(mailbox.MailboxError, 'Abgebrochen'):
                    box.wait_for_link(platform=platform, timeout=10, stop=self.stop)
                box.fetch.assert_called_once()

    def test_existing_cancellation_prevents_the_first_fetch(self):
        self.cancelled = True
        for method, platform, text in (
                ('wait_for_code', 'tiktok', 'TikTok code 123456'),
                ('wait_for_link', 'google', 'https://accounts.google.com/verify?token=OFFLINE_TEST')):
            with self.subTest(method=method):
                box = self.box(self.message(platform, text))
                with self.assertRaisesRegex(mailbox.MailboxError, 'Abgebrochen'):
                    getattr(box, method)(platform=platform, timeout=10, stop=self.stop)
                box.fetch.assert_not_called()

    def test_matching_code_and_links_remain_successful_without_cancellation(self):
        cases = [('wait_for_code', 'tiktok', '123456', 'TikTok code 123456'),
                 ('wait_for_link', 'google', 'https://accounts.google.com/verify?token=OFFLINE_TEST', None),
                 ('wait_for_link', 'trustpilot', 'https://www.trustpilot.com/confirm?token=OFFLINE_TEST', None)]
        for method, platform, expected, text in cases:
            with self.subTest(method=method, platform=platform):
                message = self.message(platform, text or expected)
                box = self.box(message)
                result, found = getattr(box, method)(platform=platform, timeout=0, stop=self.stop)
                self.assertEqual(result, expected)
                self.assertIs(found, message)
                box.fetch.assert_called_once()

    def test_cancellation_during_search_prevents_success_and_further_message_searches(self):
        for first_matches in (False, True):
            with self.subTest(first_matches=first_matches):
                self.cancelled = False
                first = self.message('tiktok', 'TikTok code 111111' if first_matches else 'No verification data')
                second = self.message('tiktok', 'TikTok code 222222')
                box = mailbox.Mailbox()
                box.fetch = MagicMock(return_value=[first, second])
                original_search = mailbox.find_code

                def cancel_during_search(text, patterns):
                    self.cancelled = True
                    return original_search(text, patterns)

                with patch.object(mailbox, 'find_code', side_effect=cancel_during_search) as search:
                    with self.assertRaisesRegex(mailbox.MailboxError, 'Abgebrochen'):
                        box.wait_for_code(platform='tiktok', timeout=10, stop=self.stop)
                    search.assert_called_once()
                box.fetch.assert_called_once()

    def test_single_stop_signal_is_not_lost_after_fetch_or_search(self):
        for phase in ('fetch', 'search'):
            with self.subTest(phase=phase):
                pending = [False]
                message = self.message('tiktok', 'TikTok code 123456')
                box = mailbox.Mailbox()

                def stop_once():
                    signal, pending[0] = pending[0], False
                    return signal

                def fetch():
                    pending[0] = phase == 'fetch'
                    return [message]

                original_search = mailbox.find_code

                def search(text, patterns):
                    pending[0] = phase == 'search'
                    return original_search(text, patterns)

                box.fetch = MagicMock(side_effect=fetch)
                with patch.object(mailbox, 'find_code', side_effect=search) as finder:
                    with self.assertRaisesRegex(mailbox.MailboxError, 'Abgebrochen'):
                        box.wait_for_code(platform='tiktok', timeout=10, stop=stop_once)
                    self.assertEqual(finder.call_count, 1 if phase == 'search' else 0)
                box.fetch.assert_called_once()
                self.assertFalse(stop_once())

    def test_empty_or_failed_fetch_honors_stop_without_retry(self):
        for result in ('empty', 'temporary_error'):
            with self.subTest(result=result):
                pending = [False]
                box = mailbox.Mailbox()

                def stop_once():
                    signal, pending[0] = pending[0], False
                    return signal

                def fetch():
                    pending[0] = True
                    if result == 'temporary_error':
                        raise mailbox.MailboxError('Temporary offline fixture error')
                    return []

                box.fetch = MagicMock(side_effect=fetch)
                with self.assertRaisesRegex(mailbox.MailboxError, 'Abgebrochen'):
                    box.wait_for_code(platform='tiktok', timeout=10, stop=stop_once)
                box.fetch.assert_called_once()
                self.assertFalse(stop_once())


if __name__ == '__main__':
    unittest.main()
