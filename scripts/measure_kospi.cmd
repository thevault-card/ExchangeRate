@echo off
REM 작업 스케줄러가 부르는 진입점. 스케줄러는 작업 디렉터리를 안 잡아주므로 여기서 맞춘다.
REM 등록: scripts\register_measure_task.ps1
cd /d "%~dp0.."
"C:\Users\someb\.local\bin\uv.exe" run --env-file .env python scripts\measure_kospi.py >> measurements\measure.log 2>&1
