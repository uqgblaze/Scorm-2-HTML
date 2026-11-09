@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 "%~dp0rise_scorm_to_html.py"
) else (
  python "%~dp0rise_scorm_to_html.py"
)
if errorlevel 1 pause
