# Contributing

1. Open an issue describing the problem or proposed change.
2. Create a focused branch from `main`.
3. Keep native BlockData calls on the Endstone primary thread.
4. Add or update regression tests.
5. Run `python -m pytest` and `ruff check src tests`.
6. Update documentation and `CHANGELOG.md` when behavior changes.
7. Open a pull request with the runtime versions used for testing.

Do not include server databases, worlds, player identifiers, API secrets, or copyrighted pack assets.
