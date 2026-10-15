from pathlib import Path
import re
import zipfile

version = re.search(r'(?m)^version = "([^"]+)"', Path('pyproject.toml').read_text(encoding='utf-8')).group(1)
init = Path('src/endstone_antigrief/__init__.py').read_text(encoding='utf-8')
plugin = Path('src/endstone_antigrief/antigrief_plugin.py').read_text(encoding='utf-8')
assert f'__version__ = "{version}"' in init
assert f'PLUGIN_VERSION = "v{version}"' in plugin
assert f'version = "{version}"' in plugin
wheel = Path('dist') / f'endstone_antigrief-{version}-py3-none-any.whl'
assert wheel.is_file(), f'Missing release wheel: {wheel}'
with zipfile.ZipFile(wheel) as archive:
    assert archive.testzip() is None
    metadata = archive.read(f'endstone_antigrief-{version}.dist-info/METADATA').decode('utf-8')
    assert f'\nVersion: {version}\n' in metadata
    for source in Path('src/endstone_antigrief').glob('*.py'):
        packaged = archive.read(f'endstone_antigrief/{source.name}').decode('utf-8')
        assert packaged.replace('\r\n', '\n') == source.read_text(encoding='utf-8'), source
print(f'AntiGrief {version} metadata verified')
