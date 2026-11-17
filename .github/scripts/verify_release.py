"""Check built wheels with the pinned SDK in disposable server-shaped directories."""
import email.parser
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import endstone

assert endstone.__version__ == "0.11.16", endstone.__version__
for wheel in sorted(Path("dist").glob("*.whl")):
    with tempfile.TemporaryDirectory(prefix="sdk-compatibility-") as temporary:
        server = Path(temporary)
        for name in ("packages", "plugins", "worlds"):
            (server / name).mkdir()
        (server / "server.properties").write_text("level-name=Compatibility Test\nserver-port=0\n")
        with zipfile.ZipFile(wheel) as archive:
            assert archive.testzip() is None
            for name in archive.namelist():
                assert (server / "packages" / name).resolve().is_relative_to((server / "packages").resolve())
            archive.extractall(server / "packages")
            metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
            metadata = email.parser.Parser().parsestr(archive.read(metadata_name).decode())
            entry_name = metadata_name.rsplit("/", 1)[0] + "/entry_points.txt"
            entries = archive.read(entry_name).decode()
        import configparser
        parser = configparser.ConfigParser()
        parser.read_string(entries)
        specs = list(parser["endstone"].values())
        code = '''import importlib, json
from endstone.plugin import Plugin
for spec in {specs!r}:
    module, name = spec.split(':', 1)
    cls = getattr(importlib.import_module(module), name)
    assert issubclass(cls, Plugin), name
    declared = cls.__dict__.get('version')
    assert not isinstance(declared, str) or declared == {version!r}, (name, declared)
print('SDK entry points verified')
'''.format(specs=specs, version=metadata["Version"])
        process = subprocess.run([sys.executable, "-c", code], cwd=server,
                                 env=dict(os.environ, PYTHONPATH=str(server / "packages"), PYTHONNOUSERSITE="1"),
                                 capture_output=True, text=True, timeout=45)
        assert process.returncode == 0, process.stdout + process.stderr
        print(f"Verified {wheel.name} on Endstone {endstone.__version__}")
