"""Credentials edited in Windows text editors remain usable and private."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from socialscraper.net.credentials import get

class WindowsCredentialFilesTests(unittest.TestCase):
    def test_utf8_bom_and_unicode_credentials_are_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'credentials.json'
            path.write_text(json.dumps({'instagram':{'instagram_main':{'user':'Beispielä','password':'local-test-only-ä'}}}),encoding='utf-8-sig')
            with patch.dict(os.environ,{},clear=True):
                credentials=get('instagram','instagram_main',path)
            self.assertEqual(credentials.user,'Beispielä')
            self.assertEqual(credentials.password,'local-test-only-ä')
            self.assertNotIn(credentials.password,repr(credentials))

if __name__=='__main__':
    unittest.main()
