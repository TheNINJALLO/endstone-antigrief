# 📦 Release & Build Process

This guide documents building, testing, and releasing AntiGrief packages.

---

## 🛠️ Local Build & Testing

Run unit tests and build local wheels using standard Python tools:

```bash
# 1. Install development dependencies
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

# 2. Run pytest test suite
python -m pytest

# 3. Run ruff linter checks
python -m ruff check src tests

# 4. Build source archive and wheel package
python -m build
```

Built artifacts are created in `dist/`:
- `endstone_antigrief-1.5.13-py3-none-any.whl`
- `endstone_antigrief-1.5.13.tar.gz`

---

## 🚀 Automated GitHub Releases

Releases are fully automated via GitHub Actions (`.github/workflows/release.yml`):

1. **Tag Version**: Tag the commit matching version in `pyproject.toml`:
   ```bash
   git tag v1.5.13
   git push origin v1.5.13
   ```
2. **CI Pipeline Executed**: GitHub Actions automatically runs tests (`pytest`), linter checks (`ruff`), builds wheels, generates `SHA256SUMS.txt`, and creates a published GitHub Release with attached `.whl` and `.tar.gz` assets.
