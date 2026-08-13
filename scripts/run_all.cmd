@echo off
REM Entry point for the twice-a-day schedule (06:30 / 18:30 KST).
REM Runs all three batches in order; each one checks the DB first and exits
REM as up_to_date without any external call when there is nothing to fetch.
REM That is why one entry point can replace the four per-batch tasks.
REM Rationale and timing: docs/design/2026-08-11 sec.3
REM
REM Exit code 1 if any batch failed, but every batch is attempted.
REM Overlap protection comes from Task Scheduler (MultipleInstances=IgnoreNew).
REM ASCII only on purpose: cmd.exe parses this file in the system codepage,
REM so non-ASCII comments corrupt the parse.
setlocal
cd /d "%~dp0.."
if not exist logs mkdir logs

set RC=0
for %%J in (index_spx index_kospi fx_daily) do call :run %%J
exit /b %RC%

:run
REM One log file per batch (a shared file loses appends when runs overlap).
"C:\Users\someb\.local\bin\uv.exe" run --env-file .env python -m collector %1 >> logs\%1.log 2>&1
if errorlevel 1 set RC=1
exit /b 0
