# 저장소와 수집 데이터를 D 드라이브로 백업한다.
#
#   powershell -File scripts\backup.ps1
#
# GitHub 에 push 하지 않기로 했으므로(설계 §13-1) 이 백업이 유일한 사본이다.
# 커밋되지 않은 변경은 번들에 담기지 않는다. 백업 전에 커밋할 것.

$ErrorActionPreference = "Stop"

$repo  = Split-Path -Parent $PSScriptRoot
$dest  = "D:\backup\ExchangeRate"
$bin   = "C:\Program Files\PostgreSQL\15\bin"
$stamp = Get-Date -Format "yyyyMMdd"

New-Item -ItemType Directory -Force $dest | Out-Null

# 1. 저장소 — --all 이라 모든 브랜치와 태그가 들어간다
$bundle = "$dest\ExchangeRate-$stamp.bundle"
git -C $repo bundle create $bundle --all
git -C $repo bundle verify $bundle

# 2. 데이터 — 커스텀 포맷(-Fc)이라 pg_restore 로 선택 복원이 된다
$url  = (Get-Content "$repo\.env" | Select-String '^DATABASE_URL=').ToString().Substring(13)
$dump = "$dest\exchangerate_dev-$stamp.dump"
& "$bin\pg_dump.exe" -Fc -f $dump $url
if ($LASTEXITCODE -ne 0) { throw "pg_dump 실패 (exit $LASTEXITCODE)" }

Get-ChildItem $dest -Filter "*$stamp*" |
    Select-Object Name, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 2) } }

Write-Output ""
Write-Output "복원 방법:"
Write-Output "  git clone $bundle <새폴더>"
Write-Output "  createdb <새DB>; pg_restore -d <새DB> $dump"
Write-Output ""
Write-Output "주의: 복원이 되는지 실제로 해보지 않으면 백업이 아니다."
Write-Output "      2026-08-07 에 scratch DB 로 복원해 원본과 행 수가 같은 것을 확인했다."
