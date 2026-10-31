@echo off
cd /d "%~dp0"
if exist ".codex-artifacts\demo311\Scripts\python.exe" (
  ".codex-artifacts\demo311\Scripts\python.exe" -X utf8 -B tools\launch_laptop_demo.py --pi %*
) else (
  py -3.11 -X utf8 -B tools\launch_laptop_demo.py --pi %*
)
pause
