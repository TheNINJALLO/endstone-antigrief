#!/usr/bin/env bash
set -euo pipefail
repo_url="${1:?Usage: scripts/publish-new-repo.sh <repository-url>}"
git init -b main
git add .
git commit -m "Release AntiGrief v1.5.13"
git remote add origin "$repo_url"
git push -u origin main
git tag -a v1.5.13 -m "AntiGrief v1.5.13"
git push origin v1.5.13
echo "Uploaded main and v1.5.13. GitHub Actions will build the release."
