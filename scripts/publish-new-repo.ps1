param([Parameter(Mandatory=$true)][string]$RepositoryUrl)
$ErrorActionPreference = "Stop"
git init -b main
git add .
git commit -m "Release AntiGrief v1.5.13"
git remote add origin $RepositoryUrl
git push -u origin main
git tag -a v1.5.13 -m "AntiGrief v1.5.13"
git push origin v1.5.13
Write-Host "Uploaded main and v1.5.13. GitHub Actions will build the release."
