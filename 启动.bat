@echo off
setlocal
cd /d "%~dp0"
title 当前难度重复挑战

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [ERROR] Missing: %~dp0.venv\Scripts\python.exe
  pause
  exit /b 1
)

"%~dp0.venv\Scripts\python.exe" -c "import cv2,numpy,mss,pyautogui,win32gui,PIL,yaml,tkinter" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Missing packages, installing...
  "%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 (
    echo [ERROR] pip install failed
    pause
    exit /b 1
  )
)

REM Use pythonw: GUI only, no console window left behind.
REM Logs still go to logs\tower_bot.log
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" -m selected_level_retry
) else (
  start "" "%~dp0.venv\Scripts\python.exe" -m selected_level_retry
)
exit /b 0
