"""Run the test suite with external Python connections blocked before imports."""
import sys, os, json
from pathlib import Path
BACKGROUND='--background' in sys.argv[1:]
if BACKGROUND:
    sys.argv.remove('--background')
    for flag in ('SS_RUN_EMBED_TESTS','SS_RUN_LOCAL_BROWSER_TESTS','SS_RUN_NETWORK_TESTS','SS_RUN_HUMANIZE_DOM','SS_RUN_TOR_TESTS'):
        os.environ.pop(flag,None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tests'))
from socialscraper.net.testpolicy import enable
enable(offline=True)
os.environ['SS_AUDIT_NETWORK']='offline'
# Local DOM fixtures launch real Chrome too. Restrict its native network before
# any test imports; Python's socket boundary does not cover Chrome processes.
from patchright.sync_api import BrowserType
def protected_launch(original):
    def launch(browser_type, *args, **kwargs):
        if BACKGROUND:raise unittest.SkipTest('Sichtbare/nativ gestartete Browserprüfung erst am Ende.')
        from urllib.parse import urlsplit
        existing=kwargs.get('proxy')
        if existing and urlsplit(existing.get('server','')).hostname not in ('localhost','127.0.0.1','::1'):
            raise ValueError('Externer Browserproxy ist im Offline-Test nicht erlaubt.')
        kwargs['proxy']=existing or {'server':'http://127.0.0.1:9','bypass':'localhost,127.0.0.1,[::1]'}
        kwargs['args']=[*kwargs.get('args',[]),'--disable-quic',
            '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
            '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1']
        return original(browser_type,*args,**kwargs)
    return launch
BrowserType.launch=protected_launch(BrowserType.launch)
BrowserType.launch_persistent_context=protected_launch(BrowserType.launch_persistent_context)
import unittest
import faulthandler
if BACKGROUND:
    import tkinter,subprocess
    def no_window(*args,**kwargs):
        raise unittest.SkipTest('Echte Windows-/Tk-Prüfung erst am Ende.')
    # Keep the classes available for subclasses and pure widget mocks, but stop
    # before the first Tcl window is constructed, including withdrawn windows.
    tkinter.Tk.__init__=no_window
    tkinter.Toplevel.__init__=no_window
    if os.name=='nt':
        OriginalPopen=subprocess.Popen
        class HiddenPopen(OriginalPopen):
            def __init__(self,*args,**kwargs):
                kwargs['creationflags']=kwargs.get('creationflags',0)|subprocess.CREATE_NO_WINDOW
                super().__init__(*args,**kwargs)
        subprocess.Popen=HiddenPopen
faulthandler.dump_traceback_later(60, repeat=True)
class RecordedResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.records={}
    def record(self,test,status):
        self.records[test.id()]=status
        destination=os.environ.get('SS_TEST_RECORD')
        if destination:
            Path(destination).write_text(json.dumps(self.records,indent=2),encoding='utf8')
    def addSuccess(self,test):
        super().addSuccess(test);self.record(test,'passed')
    def addFailure(self,test,error):
        super().addFailure(test,error);self.record(test,'failed')
    def addError(self,test,error):
        super().addError(test,error);self.record(test,'error')
    def addSkip(self,test,reason):
        super().addSkip(test,reason);self.record(test,'skipped: '+reason)
if __name__ == '__main__':
    names = sys.argv[1:]
    suite = (unittest.defaultTestLoader.loadTestsFromNames(names) if names
             else unittest.defaultTestLoader.discover('tests'))
    if BACKGROUND:
        def disable_native_helpers(group):
            for test in group:
                if isinstance(test,unittest.TestSuite):disable_native_helpers(test)
                else:
                    cls=test.__class__;module=cls.__module__;bases={base.__name__ for base in cls.__mro__}
                    native=(module.startswith('test_ui_') or module=='test_launcher' or
                        cls.__name__=='NativeRenderSurface' or
                        (module=='test_gui' and cls.__name__=='WindowTests') or
                        (module=='test_plain_log' and cls.__name__=='LogWidgetTests') or
                        (module=='test_reddit_x_integration' and 'WindowBase' in bases) or
                        (module=='test_korrekturen_fenster' and (bases & {'FensterBase','HauptfensterTests'})))
                    if native:
                        cls.__unittest_skip__=True
                        cls.__unittest_skip_why__='Echte Fenster-/Helferprüfung wird erst am Ende ausgeführt.'
        disable_native_helpers(suite)
    result = unittest.TextTestRunner(verbosity=2,resultclass=RecordedResult).run(suite)
    raise SystemExit(not result.wasSuccessful())
