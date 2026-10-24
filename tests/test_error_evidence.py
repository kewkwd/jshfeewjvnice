"""Error guidance must distinguish observations from unproven causes."""
import unittest

from socialscraper import problemlogic
from socialscraper.net import errors


class ErrorEvidenceTests(unittest.TestCase):
    def message_with_unchanged_policy(self, raw, key):
        error = RuntimeError(raw)
        self.assertEqual(errors._classify(error), (key, False))
        self.assertFalse(errors.is_hopeless(error))
        message = errors.explain(error)
        wrapped = errors.wrap(error)
        self.assertIsInstance(wrapped, errors.NetworkError)
        self.assertTrue(wrapped.retryable)
        self.assertEqual(str(wrapped), message)
        return message

    def assert_no_direct_network_advice(self, message):
        # Check the suggested action, allowing e.g. a warning against direct access.
        network = r'(?:ander(?:es|en|em)\s+netz|(?:handy[- ]?)?hotspot|direktnetz|ohne\s+proxy)'
        action = r'(?:benutz\w*|nutz\w*|wechsel\w*|versuch\w*|probier\w*)'
        self.assertNotRegex(
            message.lower(),
            rf'{action}[^.!?]{{0,80}}{network}|{network}[^.!?]{{0,40}}{action}',
            'A generic provider error must not recommend leaving the configured proxy route.',
        )

    def test_security_block_does_not_establish_an_ip_cause(self):
        message = self.message_with_unchanged_policy(
            "You've been blocked by network security.", 'sperre'
        )
        with self.subTest('observable block remains described'):
            self.assertRegex(message.lower(), r'sperr|block|abgelehnt|verweigert')
        with self.subTest('no unsupported causal attribution'):
            self.assertNotRegex(
                message.lower(),
                r'liegt\s+an\s+(?:der\s+|deiner\s+)?(?:adresse|ip)'
                r'|(?:adresse|ip)[^.!?]{0,35}(?:ist|war)\s+(?:die\s+)?ursache',
            )
        with self.subTest('no hotspot or direct-network recommendation'):
            self.assert_no_direct_network_advice(message)

    def test_generic_certificate_failure_does_not_prove_proxy_interference(self):
        message = self.message_with_unchanged_policy(
            'certificate verify failed', 'zertifikat'
        )
        self.assertIn('zertifikat', message.lower())
        self.assertNotRegex(
            message.lower(),
            r'\b(?:proxy|netzwerk)(?:\s+(?:oder|und)\s+(?:proxy|netzwerk))?\s+'
            r'(?:greift\s+in|manipuliert|verändert|fängt\s+[^.!?]*ab)',
            'A certificate verification failure alone does not demonstrate interception.',
        )

    def test_captcha_requests_personal_confirmation_instead_of_network_switch(self):
        message = self.message_with_unchanged_policy('CAPTCHA required', 'captcha')
        self.assertIn('CAPTCHA', message)
        with self.subTest('confirmation is performed by the user'):
            self.assertRegex(message.lower(), r'\b(?:selbst|manuell|eigenständig|persönlich)\b')
            self.assertRegex(message.lower(), r'bestätig|lös(?:en|e)|abschließ')
        with self.subTest('no direct-network recommendation'):
            self.assert_no_direct_network_advice(message)

    def test_ui_security_block_does_not_turn_into_a_confirmed_network_ban(self):
        message = self.message_with_unchanged_policy('blocked by network security', 'sperre')
        head, todo, _target = problemlogic.explain(message)
        with self.subTest('the headline describes the rejected request'):
            self.assertRegex(head.lower(), r'abruf|anfrage|zugriff|sicherheits')
        with self.subTest('no established ban of the internet connection'):
            self.assertNotRegex(
                head.lower(),
                r'sperrt\s+(?:diese|die|deine)\s+internetverbindung'
                r'|internetverbindung\s+(?:ist\s+)?gesperrt',
            )
        with self.subTest('no hotspot or direct-network recommendation'):
            self.assert_no_direct_network_advice(todo)

    def test_ui_certificate_error_is_not_reclassified_as_proxy_failure(self):
        message = self.message_with_unchanged_policy('certificate verify failed', 'zertifikat')
        head, todo, _target = problemlogic.explain(message)
        with self.subTest('certificate failure remains visible'):
            self.assertIn('zertifikat', head.lower())
        with self.subTest('no invented proxy outage'):
            self.assertNotRegex(
                head.lower(),
                r'(?:proxy|verbindung)[^.!?]*(?:nicht erreichbar|passt nicht|ausfall)',
            )
        self.assert_no_direct_network_advice(todo)

    def test_ui_http403_does_not_establish_a_missing_login(self):
        message = self.message_with_unchanged_policy('HTTP error 403', '403')
        head, todo, _target = problemlogic.explain(message)
        with self.subTest('access refusal remains the observed error'):
            self.assertRegex(head.lower(), r'zugriff|abruf|403|verweigert')
        with self.subTest('checking login does not prove a missing login'):
            self.assertNotRegex(
                head.lower(), r'anmeldung[^.!?]{0,25}(?:fehlt|abgelaufen)'
            )
        self.assert_no_direct_network_advice(todo)

    def test_ui_proxy_failure_does_not_recommend_disabling_the_proxy(self):
        message = self.message_with_unchanged_policy('ERR_PROXY_CONNECTION_FAILED', 'proxy')
        head, todo, _target = problemlogic.explain(message)
        self.assertIn('proxy', head.lower())
        self.assertNotRegex(
            todo.lower(),
            r'modus[^.!?]{0,25}[„\"»]?aus\b'
            r'|(?:proxy|tor)[^.!?]{0,30}(?:abschalten|deaktivieren)',
        )
        self.assert_no_direct_network_advice(todo)


if __name__ == '__main__':
    unittest.main()
