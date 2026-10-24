"""Round 34: confirmation data comes from message bodies, never attachments."""
import contextlib
from email import message_from_bytes
from email.message import EmailMessage
import socket
import unittest
from unittest.mock import MagicMock, patch

from socialscraper import mailbox


class MailContentTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for target in ('create_connection', 'getaddrinfo'):
            stack.enter_context(patch.object(socket, target, side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch('socialscraper.operation.wait', side_effect=AssertionError('Unexpected wait')))
        self.link = 'https://accounts.google.com/verify?token=test&step=1'
        self.html = '<p>Konto bestätigen:</p><a href="https://accounts.google.com/verify?token=test&amp;step=1">Weiter</a>'

    def outer(self, body):
        message = EmailMessage()
        message['From'] = 'noreply@google.com'
        message['Subject'] = 'Google confirmation'
        message.set_content(body)
        return message

    def eml(self, body, *, attachment=True):
        nested = self.outer(body)
        container = EmailMessage()
        container.set_type('message/rfc822')
        container.set_payload([nested])
        if attachment:
            container['Content-Disposition'] = 'attachment; filename="old.eml"'
        return container

    def box_for(self, message):
        parsed = message_from_bytes(message.as_bytes())
        box = mailbox.Mailbox()
        box.fetch = MagicMock(return_value=[{'subject': mailbox._subject(parsed),
            'text': mailbox._text_of(parsed), 'from': parsed.get('From') or ''}])
        return box

    def test_attached_eml_before_current_body_cannot_replace_current_code(self):
        message = self.outer('Google code 654321')
        message.make_mixed()
        body = message.get_payload()[0]
        message.set_payload([self.eml('Google code 111111'), body])
        value, found = self.box_for(message).wait_for_code(platform='google', timeout=0)
        self.assertEqual(value, '654321')
        self.assertNotIn('111111', found['text'])

    def test_nested_rfc822_without_attachment_header_cannot_supply_a_code(self):
        message = self.outer('This message has no confirmation code.')
        message.make_mixed()
        message.attach(self.eml('Google code 111111', attachment=False))
        with self.assertRaises(mailbox.MailboxError):
            self.box_for(message).wait_for_code(platform='google', timeout=0)

    def test_root_rfc822_without_attachment_header_has_no_body_for_confirmation(self):
        message = self.eml('Google code 111111', attachment=False)
        self.assertEqual(mailbox._text_of(message), '')

    def test_attached_nested_multipart_cannot_supply_a_confirmation_link(self):
        message = self.outer('This message has no confirmation link.')
        message.make_mixed()
        attachment = EmailMessage()
        attachment.make_mixed()
        attachment['Content-Disposition'] = 'attachment; filename="archive.mime"'
        alternative = EmailMessage()
        alternative.set_content('Archived message')
        alternative.add_alternative(self.html, subtype='html')
        attachment.attach(alternative)
        message.attach(attachment)
        with self.assertRaises(mailbox.MailboxError):
            self.box_for(message).wait_for_link(platform='google', timeout=0)

    def test_root_plain_text_attachment_is_ignored(self):
        message = self.outer('Google code 111111')
        message['Content-Disposition'] = 'attachment; filename="old.txt"'
        self.assertEqual(mailbox._text_of(message), '')

    def test_root_binary_payload_is_ignored(self):
        message = EmailMessage()
        message.set_content(b'Google code 111111', maintype='application', subtype='octet-stream')
        self.assertEqual(mailbox._text_of(message), '')
        message['Content-Disposition'] = 'attachment; filename="old.bin"'
        self.assertEqual(mailbox._text_of(message), '')

    def test_real_plain_and_html_alternatives_remain_searchable(self):
        plain = self.outer('Google code 654321')
        self.assertIn('654321', mailbox._text_of(plain))
        html = EmailMessage()
        html.set_content(self.html, subtype='html')
        self.assertIn(self.link, mailbox._text_of(html))
        plain.add_alternative(self.html, subtype='html')
        box = self.box_for(plain)
        code, _ = box.wait_for_code(platform='google', timeout=0)
        link, _ = box.wait_for_link(platform='google', timeout=0)
        self.assertEqual(code, '654321')
        self.assertEqual(link, self.link)

    def assert_temporary_html_link(self, *, text, html):
        box = mailbox.TempMailBox('test@example.test', 'test-only', basis='https://mail.example.test')
        listing = [{'id': 'confirmation', 'createdAt': '2026-10-15T00:00:00Z'}]
        detail = {'subject': 'Google confirmation', 'text': text, 'html': html,
                  'from': {'address': 'noreply@google.com'}}
        with patch.object(mailbox, '_json_post', return_value={'token': 'test-token'}) as post, \
                patch.object(mailbox, '_json_get', side_effect=[listing, detail]) as get:
            found, message = box.wait_for_link(platform='google', timeout=0)
        self.assertEqual(found, self.link)
        self.assertIn(self.link, message['text'])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(get.call_count, 2)
        if text:
            self.assertIn(text, message['text'])

    def test_temporary_html_only_message_preserves_href_confirmation_link(self):
        self.assert_temporary_html_link(text='', html=[self.html])

    def test_temporary_text_alternative_does_not_hide_html_confirmation_link(self):
        self.assert_temporary_html_link(text='Konto bestätigen', html=[self.html])

    def test_temporary_single_html_string_preserves_confirmation_link(self):
        self.assert_temporary_html_link(text='', html=self.html)


if __name__ == '__main__':
    unittest.main()
