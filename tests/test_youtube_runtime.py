import importlib.metadata
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, patch

from socialscraper.adapters.youtube import (
    YouTubeAdapter, YouTubeSetupError, _check_youtube_packages,
    youtube_runtime_options,
)
from socialscraper.config import Settings


class RuntimeSelectionTests(unittest.TestCase):
    def run_options(self, versions):
        def find(name):
            return f'C:/Tools/{name}.exe' if name in versions else None

        def run(command, **kwargs):
            value = versions[command[0].rsplit('/', 1)[-1].split('.')[0]]
            if isinstance(value, Exception):
                raise value
            return subprocess.CompletedProcess(command, 0, value, '')

        with patch('socialscraper.adapters.youtube._check_youtube_packages'), \
             patch('socialscraper.adapters.youtube.shutil.which', side_effect=find), \
             patch('socialscraper.adapters.youtube.subprocess.run', side_effect=run) as probe:
            return youtube_runtime_options(), probe

    def test_node_is_explicitly_enabled_with_absolute_path_and_no_remote_components(self):
        options, probe = self.run_options({'node': 'v24.19.0\n'})
        self.assertEqual(options['js_runtimes'], {'node': {'path': 'C:/Tools/node.exe'}})
        self.assertEqual(options['remote_components'], [])
        probe.assert_called_once()
        self.assertEqual(probe.call_args.args[0], ['C:/Tools/node.exe', '--version'])
        self.assertEqual(probe.call_args.kwargs['timeout'], 5)
        self.assertNotIn('shell', probe.call_args.kwargs)

    def test_minimum_node_version_is_supported(self):
        options, _ = self.run_options({'node': 'v22.0.0'})
        self.assertIn('node', options['js_runtimes'])

    def test_old_node_falls_back_to_supported_deno(self):
        options, probe = self.run_options({'node': 'v20.19.0', 'deno': 'deno 2.3.0 (stable)\nv8 13.5'})
        self.assertIn('deno', options['js_runtimes'])
        self.assertEqual(probe.call_count, 2)

    def test_runtime_timeout_falls_back_without_waiting_again(self):
        options, _ = self.run_options({'node': subprocess.TimeoutExpired('node', 5),
                                      'deno': 'deno 2.9.5 (stable)'})
        self.assertIn('deno', options['js_runtimes'])

    def test_broken_node_binary_falls_back(self):
        options, _ = self.run_options({'node': OSError('cannot launch'), 'deno': 'deno 2.9.5'})
        self.assertIn('deno', options['js_runtimes'])

    def test_no_runtime_reports_an_actionable_setup_error(self):
        with self.assertRaisesRegex(YouTubeSetupError, 'Node.js ab 22 oder Deno ab 2.3'):
            self.run_options({})

    def test_old_runtimes_are_not_silently_accepted(self):
        with self.assertRaisesRegex(YouTubeSetupError, 'node 21.9.0 ist zu alt.*deno 2.2.9 ist zu alt'):
            self.run_options({'node': 'v21.9.0', 'deno': 'deno 2.2.9'})

    def test_unrecognized_runtime_version_is_rejected(self):
        with self.assertRaisesRegex(YouTubeSetupError, 'Version konnte nicht ermittelt werden'):
            self.run_options({'node': 'unexpected output'})

    def test_failed_version_process_is_rejected(self):
        with patch('socialscraper.adapters.youtube._check_youtube_packages'), \
             patch('socialscraper.adapters.youtube.shutil.which', side_effect=['node', None]), \
             patch('socialscraper.adapters.youtube.subprocess.run', return_value=
                   subprocess.CompletedProcess(['node'], 1, 'v24.19.0', 'error')):
            with self.assertRaises(YouTubeSetupError):
                youtube_runtime_options()


class PackageCompatibilityTests(unittest.TestCase):
    def check(self, yt='2026.8.19', ejs='0.8.0', requirement='yt-dlp-ejs==0.8.0; extra == "default"'):
        versions = {'yt-dlp': yt, 'yt-dlp-ejs': ejs}
        def version(name):
            if versions[name] is None:
                raise importlib.metadata.PackageNotFoundError(name)
            return versions[name]
        with patch('socialscraper.adapters.youtube.metadata.version', side_effect=version), \
             patch('socialscraper.adapters.youtube.metadata.requires',
                   return_value=[requirement] if requirement else []), \
             patch('socialscraper.adapters.youtube.importlib.import_module'):
            return _check_youtube_packages()

    def test_installed_ejs_must_match_the_installed_yt_dlp_requirement(self):
        self.assertIsNone(self.check())
        with self.assertRaisesRegex(YouTubeSetupError, 'passt nicht'):
            self.check(ejs='0.7.0')

    def test_future_supported_version_range_is_evaluated_from_metadata(self):
        self.assertIsNone(self.check(yt='2027.1.1', ejs='0.9.2',
                                    requirement='yt-dlp-ejs>=0.9,<1; extra == "default"'))

    def test_old_yt_dlp_is_rejected(self):
        with self.assertRaisesRegex(YouTubeSetupError, 'ist zu alt'):
            self.check(yt='2025.1.1')

    def test_missing_yt_dlp_has_a_clear_installation_message(self):
        with self.assertRaisesRegex(YouTubeSetupError, 'Komponenten fehlen.*requirements.txt'):
            self.check(yt=None)

    def test_missing_ejs_has_a_clear_installation_message(self):
        with self.assertRaisesRegex(YouTubeSetupError, 'Komponenten fehlen'):
            self.check(ejs=None)

    def test_missing_ejs_requirement_is_rejected(self):
        with self.assertRaisesRegex(YouTubeSetupError, 'passt nicht'):
            self.check(requirement=None)

    def test_unrelated_extra_does_not_replace_the_default_requirement(self):
        with patch('socialscraper.adapters.youtube.metadata.version', side_effect=['2026.8.19', '0.8.0']), \
             patch('socialscraper.adapters.youtube.metadata.requires', return_value=[
                 'yt-dlp-ejs==0.8.0; extra == "default"',
                 'yt-dlp-ejs==0.7.0; extra == "some-other-extra"']), \
             patch('socialscraper.adapters.youtube.importlib.import_module'):
            self.assertIsNone(_check_youtube_packages())

    def test_broken_ejs_install_is_rejected(self):
        with patch('socialscraper.adapters.youtube.metadata.version', side_effect=['2026.8.19', '0.8.0']), \
             patch('socialscraper.adapters.youtube.importlib.import_module', side_effect=ImportError('broken')):
            with self.assertRaisesRegex(YouTubeSetupError, 'Komponenten fehlen'):
                _check_youtube_packages()


class FetchSetupTests(unittest.TestCase):
    def ctx(self):
        return types.SimpleNamespace(cfg=Settings(max_comments=0, impersonate=''),
                                     im=None, identity='default', country='us', mobile=False)

    def test_missing_local_setup_never_retries_or_starts_yt_dlp(self):
        ydl = Mock()
        with patch.dict(sys.modules, {'yt_dlp': types.SimpleNamespace(YoutubeDL=ydl)}), \
             patch('socialscraper.adapters.youtube.youtube_runtime_options',
                   side_effect=YouTubeSetupError('setup missing')), \
             patch('socialscraper.net.retry.time.sleep') as sleep:
            with self.assertRaises(YouTubeSetupError):
                YouTubeAdapter().fetch('https://youtu.be/test', self.ctx())
            ydl.assert_not_called()
            sleep.assert_not_called()

    def test_runtime_options_reach_the_real_client_configuration(self):
        handle = Mock()
        handle.__enter__ = Mock(return_value=handle)
        handle.__exit__ = Mock(return_value=False)
        handle.extract_info.return_value = {'id': 'test'}
        handle.process_ie_result.side_effect = lambda result, **kw: result
        ydl = Mock(return_value=handle)
        options = {'js_runtimes': {'node': {'path': 'C:/Tools/node.exe'}}, 'remote_components': []}
        with patch.dict(sys.modules, {'yt_dlp': types.SimpleNamespace(YoutubeDL=ydl)}), \
             patch('socialscraper.adapters.youtube.youtube_runtime_options', return_value=options):
            video, comments = YouTubeAdapter().fetch('https://youtu.be/test', self.ctx())
        config = ydl.call_args.args[0]
        self.assertEqual(config['js_runtimes'], options['js_runtimes'])
        self.assertEqual(config['remote_components'], [])
        self.assertFalse(config['ignoreerrors'])
        self.assertTrue(config['skip_download'])
        self.assertEqual(video.video_id, 'test')
        self.assertEqual(comments, [])

    def test_empty_response_is_reported_as_failure(self):
        handle = Mock()
        handle.__enter__ = Mock(return_value=handle)
        handle.__exit__ = Mock(return_value=False)
        handle.extract_info.return_value = None
        with patch.dict(sys.modules, {'yt_dlp': types.SimpleNamespace(YoutubeDL=Mock(return_value=handle))}), \
             patch('socialscraper.adapters.youtube.youtube_runtime_options', return_value={}), \
             patch('socialscraper.net.retry.time.sleep') as sleep:
            with self.assertRaisesRegex(ValueError, 'keine Videodaten'):
                YouTubeAdapter().fetch('https://youtu.be/test', self.ctx())
            sleep.assert_not_called()

    def fetch_with_error(self, message):
        class DownloadError(Exception):
            pass
        handle = Mock()
        handle.__enter__ = Mock(return_value=handle)
        handle.__exit__ = Mock(return_value=False)
        handle.extract_info.side_effect = DownloadError(message)
        module = types.SimpleNamespace(YoutubeDL=Mock(return_value=handle),
                                       utils=types.SimpleNamespace(DownloadError=DownloadError))
        with patch.dict(sys.modules, {'yt_dlp': module}),              patch('socialscraper.adapters.youtube.youtube_runtime_options', return_value={}),              patch('socialscraper.net.retry.time.sleep') as sleep:
            try:
                YouTubeAdapter().fetch('https://youtu.be/test', self.ctx())
            except Exception as error:
                return error, sleep, handle
        self.fail('fetch did not fail')

    def test_unavailable_video_is_not_retried(self):
        from socialscraper.net.retry import PermanentFetchError
        error, sleep, handle = self.fetch_with_error('ERROR: [youtube] aaaaaaaaaaa: This video is unavailable')
        self.assertIsInstance(error, PermanentFetchError)
        sleep.assert_not_called()
        self.assertEqual(handle.extract_info.call_count, 1)

    def test_private_video_is_not_retried(self):
        from socialscraper.net.retry import PermanentFetchError
        error, sleep, handle = self.fetch_with_error('ERROR: [youtube] test: Private video. Sign in')
        self.assertIsInstance(error, PermanentFetchError)
        sleep.assert_not_called()
        self.assertEqual(handle.extract_info.call_count, 1)

    def test_transient_error_is_still_retried(self):
        error, sleep, handle = self.fetch_with_error('ERROR: Unable to download webpage: timed out')
        self.assertEqual(handle.extract_info.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


if __name__ == '__main__':
    unittest.main()