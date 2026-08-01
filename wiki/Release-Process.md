# Release Process

The repository includes two supported release paths.

## Tag release

```bash
git tag -a v1.5.13 -m "AntiGrief v1.5.13"
git push origin v1.5.13
```

The Release workflow validates the package, runs tests and Ruff, builds the wheel and source archive, verifies version metadata, creates checksums, and publishes the GitHub Release.

## Manual release

Open **Actions → Release → Run workflow**, enter a tag matching the version in `pyproject.toml`, and run it from `main`.

## Required repository settings

- Actions must be enabled.
- Workflow permissions must allow read and write access for release creation.
- Enable GitHub Pages with **GitHub Actions** as the source.
- Enable the Wiki and create its first page before running the Publish Wiki workflow.
