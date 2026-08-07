from pathlib import Path
import re, sys, zipfile
version = re.search(r'(?m)^version = "([^"]+)"', Path('pyproject.toml').read_text()).group(1)
init = Path('src/endstone_antigrief/__init__.py').read_text()
plugin = Path('src/endstone_antigrief/antigrief_plugin.py').read_text()
assert f'__version__ = "{version}"' in init
assert f'PLUGIN_VERSION = "v{version}"' in plugin
assert f'version = "{version}"' in plugin
wheels=list(Path('dist').glob('*.whl'))
if wheels:
    with zipfile.ZipFile(wheels[0]) as z: assert z.testzip() is None
print(f'AntiGrief {version} metadata verified')
