"""Host an owned browser window in the desktop UI, preserving its browser context.

No URL, cookies, credentials or browser instance is transferred between processes.
Only an owned native window handle is announced over the job's stdout channel.
"""
import json
import os

PREFIX = '[SS_BROWSER] '
_BROWSERS = {'local': ('chrome.exe', 'Chrome_WidgetWin_1'),
             'firefox': ('firefox.exe', 'MozillaWindowClass')}


def _browser_spec(backend):
    return _BROWSERS.get(backend) if isinstance(backend, str) else None

class Windows:
    def __init__(self):
        if os.name != 'nt':
            raise OSError('Eingebettete Browserfenster sind unter Windows verfügbar.')
        import ctypes
        from ctypes import wintypes as w
        self.c, self.w = ctypes, w
        self.api = ctypes.WinDLL('user32',use_last_error=True)
        declarations = {
            'IsWindow': ([w.HWND],w.BOOL),
            'IsWindowVisible': ([w.HWND],w.BOOL),
            'GetWindowThreadProcessId': ([w.HWND,ctypes.POINTER(w.DWORD)],w.DWORD),
            'GetClassNameW': ([w.HWND,w.LPWSTR,ctypes.c_int],ctypes.c_int),
            'GetParent': ([w.HWND],w.HWND),
            'SetParent': ([w.HWND,w.HWND],w.HWND),
            'GetWindowLongPtrW': ([w.HWND,ctypes.c_int],ctypes.c_ssize_t),
            'SetWindowLongPtrW': ([w.HWND,ctypes.c_int,ctypes.c_ssize_t],ctypes.c_ssize_t),
            'MoveWindow': ([w.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.BOOL],w.BOOL),
            'SetWindowPos': ([w.HWND,w.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.UINT],w.BOOL),
            'GetWindowRect': ([w.HWND,ctypes.POINTER(w.RECT)],w.BOOL),
            'ShowWindow': ([w.HWND,ctypes.c_int],w.BOOL),
        }
        for name,(args,result) in declarations.items():
            function=getattr(self.api,name);function.argtypes=args;function.restype=result

    def owner(self,handle):
        pid=self.w.DWORD()
        self.api.GetWindowThreadProcessId(handle,self.c.byref(pid))
        return int(pid.value)

    def browser(self,handle,backend='local'):
        spec=_browser_spec(backend)
        if spec is None:
            return False
        name=self.c.create_unicode_buffer(256)
        return bool(self.api.IsWindow(handle) and
            self.api.GetClassNameW(handle,name,len(name)) and name.value==spec[1])

    def visible(self,pids,backend='local'):
        if _browser_spec(backend) is None:
            return []
        found=[]
        callback_type=self.c.WINFUNCTYPE(self.w.BOOL,self.w.HWND,self.w.LPARAM)
        @callback_type
        def visit(handle,_):
            if self.api.IsWindowVisible(handle) and self.owner(handle) in pids and self.browser(handle,backend):
                found.append(int(handle))
            return True
        self.api.EnumWindows.argtypes=[callback_type,self.w.LPARAM]
        self.api.EnumWindows.restype=self.w.BOOL
        self.api.EnumWindows(visit,0)
        return found

    def parent(self,handle,parent):
        self.c.set_last_error(0)
        previous=self.api.SetParent(handle,parent)
        error=self.c.get_last_error()
        if not previous and error:
            raise self.c.WinError(error)
        return previous

def announce(handle,headless):
    if headless or os.environ.get('SS_EMBED_BROWSER')!='1' or os.name!='nt':
        return
    backend=getattr(handle,'backend','local')
    spec=_browser_spec(backend)
    if spec is None:
        return
    from .deadline import descendants
    native=Windows()
    allowed={pid for pid,name in descendants() if name==spec[0]}
    candidates=native.visible(allowed,backend)
    if len(candidates)!=1:
        # Never guess which of several independent sessions should be embedded.
        return
    window=candidates[0]
    owner=native.owner(window)
    if owner not in allowed or not native.browser(window,backend):
        return
    handle._native_window=window
    event={'version':1,'state':'open','hwnd':window,'pid':owner,'proxy':bool(handle.proxy)}
    if backend!='local':
        event['backend']=backend
    print(PREFIX+json.dumps(event),flush=True)

def closed(handle):
    window=getattr(handle,'_native_window',None)
    if window:
        print(PREFIX+json.dumps({'version':1,'state':'closed','hwnd':window}),flush=True)
        handle._native_window=None

def parse(line):
    if not line.startswith(PREFIX) or len(line)>2048:
        return None
    try:
        event=json.loads(line[len(PREFIX):])
        if (not isinstance(event,dict) or event.get('version')!=1 or
            event.get('state') not in ('open','closed') or
            type(event.get('hwnd')) is not int or event['hwnd']<=0 or
            _browser_spec(event.get('backend','local')) is None):
            return None
        if event['state']=='open' and (type(event.get('pid')) is not int or event['pid']<=0 or
                                      type(event.get('proxy')) is not bool):
            return None
        return event
    except (ValueError,TypeError):
        return None

def validate(event,job_pid,native=None):
    if event.get('state')!='open':
        return False
    backend=event.get('backend','local')
    spec=_browser_spec(backend)
    if spec is None:
        return False
    from .deadline import descendants
    allowed={pid for pid,name in descendants(job_pid) if name==spec[0]}
    native=native or Windows()
    return (event['pid'] in allowed and native.owner(event['hwnd'])==event['pid'] and
            native.browser(event['hwnd'],backend))

class EmbeddedWindow:
    def __init__(self,handle,parent,native=None):
        self.handle,self.native=handle,native or Windows()
        api=self.native.api;c=self.native.c;w=self.native.w
        self.old_parent=api.GetParent(handle)
        self.old_style=api.GetWindowLongPtrW(handle,-16)
        rect=w.RECT()
        if not api.GetWindowRect(handle,c.byref(rect)):
            raise OSError('Browserfenster ist nicht mehr verfügbar.')
        self.old_rect=(rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top)
        self.attached=False
        try:
            # Remove only the outer frame; the browser's page and controls stay intact.
            api.SetWindowLongPtrW(handle,-16,(self.old_style & ~0x80CF0000)|0x40000000)
            self.native.parent(handle,parent)
            self.attached=True
            api.SetWindowPos(handle,None,0,0,0,0,0x0037)  # FRAMECHANGED, NOMOVE, NOSIZE, NOZORDER
        except BaseException:
            api.SetWindowLongPtrW(handle,-16,self.old_style)
            raise

    def resize(self,width,height):
        if self.attached and self.native.api.IsWindow(self.handle):
            self.native.api.MoveWindow(self.handle,0,0,max(1,int(width)),max(1,int(height)),True)

    def detach(self):
        if not self.attached:
            return
        self.attached=False
        api=self.native.api
        if api.IsWindow(self.handle):
            self.native.parent(self.handle,self.old_parent)
            api.SetWindowLongPtrW(self.handle,-16,self.old_style)
            api.SetWindowPos(self.handle,None,*self.old_rect,0x0024)
            api.ShowWindow(self.handle,5)
