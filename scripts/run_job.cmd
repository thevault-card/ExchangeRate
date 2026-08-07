@echo off
REM 작업 스케줄러가 부르는 배치 진입점. 스케줄러는 작업 디렉터리를 안 잡아주므로 여기서 맞춘다.
REM
REM   run_job.cmd index_spx
REM   run_job.cmd index_kospi
REM   run_job.cmd fx_daily
REM
REM 로그는 logs\collector.log 에 누적한다. 수동 실행과 달리 스케줄 실행은 화면이
REM 없어서, 파일에 안 남기면 "그날 왜 안 쌓였지" 를 나중에 추적할 수 없다.
REM 로그는 배치별로 나눈다. 한 파일에 몰면 두 배치가 같은 순간에 돌 때 append 가
REM 밀리는데, 그게 종료코드 0 으로 조용히 넘어간다(실측으로 확인). 스케줄 시각이
REM 겹치지 않게 짜도 수동 실행이 겹칠 수 있다.
cd /d "%~dp0.."
if not exist logs mkdir logs
"C:\Users\someb\.local\bin\uv.exe" run --env-file .env python -m collector %* >> logs\%1.log 2>&1
exit /b %ERRORLEVEL%
