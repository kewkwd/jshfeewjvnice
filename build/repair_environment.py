"""Rebind copied Windows entry points locally; never install or download packages."""
from importlib import metadata
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import venv


def plain(path):
    path = Path(path).absolute()
    if any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in (path, *path.parents)):
        raise ValueError('Dateiumleitungen sind für die Reparatur nicht erlaubt.')
    return path.resolve()


def replace_files(changes, verify=lambda: None):
    """Stage locally, replace atomically per file, roll back on a caught failure."""
    changes = {plain(path): data for path, data in changes.items()}
    before = {path: path.read_bytes() if path.exists() else None for path in changes}
    changes = {p: data for p, data in changes.items() if before[p] != data}
    staged, written = {}, []
    try:
        for path, data in changes.items():
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.repair-', delete=False) as stream:
                staged[path] = Path(stream.name)
                stream.write(data)
        for path, temp in staged.items():
            os.replace(temp, path)
            written.append(path)
        verify()
    except BaseException:
        for path in reversed(written):
            if before[path] is None:
                path.unlink()
            else:
                # Use a new atomic replacement for rollback too.
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.repair-', delete=False) as stream:
                    rollback = Path(stream.name)
                    stream.write(before[path])
                try:
                    os.replace(rollback, path)
                finally:
                    rollback.unlink(missing_ok=True)
        raise
    finally:
        for temp in staged.values():
            temp.unlink(missing_ok=True)
    return len(changes)


def repair(root):
    root = plain(root)
    env = plain(root / '.venv')
    scripts = plain(env / 'Scripts')
    if os.name != 'nt' or plain(sys.prefix) != env or plain(sys.executable) != scripts / 'python.exe':
        raise ValueError('Bitte mit dem eigenen .venv/Scripts/python.exe starten.')
    from pip._vendor.distlib.scripts import ScriptMaker
    entries = {}
    for dist in metadata.distributions(path=[str(env / 'Lib/site-packages')]):
        for entry in dist.entry_points:
            if entry.group not in ('console_scripts', 'gui_scripts'):
                continue
            if entry.name != Path(entry.name).name or '/' in entry.name or '\\' in entry.name:
                raise ValueError('Ungültiger Paketstarter.')
            spec = (entry.value, entry.group == 'gui_scripts')
            if entry.name in entries and entries[entry.name] != spec:
                raise ValueError('Mehrdeutiger Paketstarter.')
            entries[entry.name] = spec
    # pip publishes pip and pip3, but also creates a versioned installer alias.
    entries.setdefault(f'pip{sys.version_info.major}.{sys.version_info.minor}', entries['pip'])
    wrappers = [p for p in scripts.glob('*.exe') if p.name.lower() not in ('python.exe', 'pythonw.exe')]
    if any(p.stem not in entries for p in wrappers):
        raise ValueError('Unbekannter Paketstarter: Reparatur vor Änderungen abgebrochen.')
    with tempfile.TemporaryDirectory(prefix='profilwerk-entrypoints-') as folder:
        stage = Path(folder)
        maker = ScriptMaker(None, str(stage))
        maker.executable = str(scripts / 'python.exe')
        maker.variants = {''}
        maker.clobber = True
        for path in wrappers:
            value, gui = entries[path.stem]
            maker.make(f'{path.stem} = {value}', options={'gui': gui})
        context = SimpleNamespace(env_dir=str(env), env_name=env.name, prompt=env.name,
                                  bin_name='Scripts', bin_path=str(stage),
                                  env_exe=str(scripts / 'python.exe'))
        venv.EnvBuilder().setup_scripts(context)
        changes = {scripts / p.name: p.read_bytes() for p in stage.iterdir() if p.is_file()}
        count = replace_files(changes)
    return count


if __name__ == '__main__':
    count = repair(Path(__file__).resolve().parents[1])
    print(f'Lokale Python-Starter und Aktivierung geprüft; {count} Dateien aktualisiert. Kein Paketabruf.')
