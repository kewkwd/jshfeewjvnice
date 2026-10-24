"""Private native surface required by the installed Nox frontend.

The Google screen and controls remain in the main application's ADB dialog.
This helper creates its own HWND; it never adopts another application's window.
"""
import ctypes,json,os,shutil,subprocess,sys,tempfile,time
from pathlib import Path
from ..net.errors import FinalError

class RenderHost:
    def __init__(self,directory,stop=None):
        self.base=Path(directory).resolve();self.base.mkdir(parents=True,exist_ok=True)
        self.directory=Path(tempfile.mkdtemp(prefix='render-',dir=self.base)).resolve()
        self.process=None;self.hwnd=None;self.stop=stop

    def start(self):
        if self.stop and self.stop():raise FinalError('Abgebrochen.')
        # Windows' venv launcher creates another PID. This stdlib-only helper
        # uses the actual interpreter so its HWND owner equals the child PID.
        interpreter=getattr(sys,'_base_executable',sys.executable)
        self.process=subprocess.Popen([interpreter,'-m','socialscraper.android.render_host',
            str(self.directory),str(os.getpid())],cwd=Path(__file__).resolve().parents[2],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if self.stop and self.stop():raise FinalError('Abgebrochen.')
            if self.process.poll() is not None:break
            ready=self.directory/'ready.json'
            if ready.is_file():
                data=json.loads(ready.read_text(encoding='utf8'))
                if data.get('pid')!=self.process.pid or type(data.get('hwnd')) is not int:
                    raise FinalError('Android-Grafikfenster gehört nicht zum Testauftrag.')
                from ctypes import wintypes
                user32=ctypes.WinDLL('user32',use_last_error=True)
                user32.IsWindow.argtypes=[wintypes.HWND];user32.IsWindow.restype=wintypes.BOOL
                user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
                owner=wintypes.DWORD()
                user32.GetWindowThreadProcessId(data['hwnd'],ctypes.byref(owner))
                if not user32.IsWindow(data['hwnd']) or owner.value!=self.process.pid:
                    raise FinalError('Android-Grafikfenster konnte nicht unabhängig bestätigt werden.')
                self.hwnd=data['hwnd'];return self.hwnd
            time.sleep(.1)
        raise FinalError('Das eigene Android-Grafikfenster konnte nicht gestartet werden.')

    def close(self):
        (self.directory/'close').write_text('close',encoding='ascii')
        if self.process:
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate();self.process.wait(timeout=5)
        if self.directory.parent!=self.base or not self.directory.name.startswith('render-'):
            raise FinalError('Ungültiges eigenes Android-Grafikverzeichnis.')
        shutil.rmtree(self.directory)

def main(directory,parent_pid):
    from ..net.testpolicy import enable
    enable(offline=True)
    import tkinter as tk
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    parent=kernel.OpenProcess(0x00100000,False,parent_pid)
    if not parent:raise RuntimeError('Android-Auftrag existiert nicht mehr.')
    window=None
    try:
        window=tk.Tk();window.withdraw()
        window.title('Socialscraper – Android-Grafik')
        # pack sizes a child against its still-withdrawn 1x1 parent. Give the
        # native surface an explicit rectangle before Nox reads its client size.
        frame=tk.Frame(window,width=720,height=1280)
        frame.place(x=0,y=0,width=720,height=1280);window.update_idletasks()
        if frame.winfo_width()!=720 or frame.winfo_height()!=1280:
            raise RuntimeError('Android-Grafikfenster hat keine gültige Größe.')
        folder=Path(directory)
        temporary=folder/'ready.tmp'
        temporary.write_text(json.dumps({'pid':os.getpid(),'hwnd':frame.winfo_id(),
            'width':frame.winfo_width(),'height':frame.winfo_height()}),encoding='utf8')
        temporary.replace(folder/'ready.json')
        def check():
            if (folder/'close').exists() or kernel.WaitForSingleObject(parent,0)!=258:
                window.destroy()
            else:window.after(100,check)
        window.after(100,check);window.mainloop()
    finally:
        kernel.CloseHandle(parent)

if __name__=='__main__':main(Path(sys.argv[1]).resolve(),int(sys.argv[2]))
