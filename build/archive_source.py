"""Create a source-only review archive while retaining every unfinished module."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

SOURCE_FOLDERS = ('socialscraper', 'tests', 'build', 'launcher', 'checks', 'docs', 'assets', 'deploy', 'unused')
ROOT_FILES = ('.gitignore', '.dockerignore', 'README.md', 'BUGFIXES.md', 'Dockerfile',
              'requirements.txt', 'requirements-lock.txt', 'credentials.example.json',
              'Einrichten.cmd', 'Einrichten.ps1', 'Starten.cmd', 'Starten.ps1',
              'Socialscraper.cmd', 'Profilwerk.cmd', 'Desktop-Verknuepfung.cmd', 'Desktop-Verknuepfung.ps1')


def source_files(root):
    """Known code/documentation roots only; no user settings, data or SDK binaries."""
    root = Path(root)
    candidates = [root / name for name in ROOT_FILES if (root / name).is_file()]
    for name in SOURCE_FOLDERS:
        folder = root / name
        if folder.is_symlink() or folder.is_junction():
            raise ValueError('Quellordner ist umgeleitet: ' + name)
        candidates.extend(path for path in folder.rglob('*') if path.is_file())
    for path in sorted(set(candidates)):
        relative = path.relative_to(root)
        if ('__pycache__' in relative.parts or path.suffix in ('.pyc', '.pyo')
                or relative.as_posix() == 'deploy/server.env'):
            continue
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
            raise ValueError('Umgeleitete Quelldatei wird nicht archiviert.')
        yield path


def create_archive(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.is_relative_to(root):
        raise ValueError('Quellarchiv bitte außerhalb des Programmordners speichern.')
    paths = list(source_files(root))
    manifest = {}
    with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            manifest[relative] = hashlib.sha256(data).hexdigest()
            archive.writestr(relative, data)
        archive.writestr('QUELLEN-MANIFEST.json', json.dumps(manifest, indent=2))
    return len(manifest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    count = create_archive(Path(__file__).resolve().parents[1], args.output)
    print(f'{count} Quell-/Test-/Dokumentationsdateien archiviert; Laufzeitbestand bleibt unverändert.')
