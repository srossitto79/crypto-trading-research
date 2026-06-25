@echo off
setlocal
cd /d "%~dp0"

if exist "%ProgramFiles%\nodejs\npm.cmd" set "PATH=%ProgramFiles%\nodejs;%PATH%"
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PATH=%LocalAppData%\Programs\Python\Python311;%PATH%"
if exist "%LocalAppData%\Programs\Python\Launcher\py.exe" set "PATH=%LocalAppData%\Programs\Python\Launcher;%PATH%"

if "%AXIOM_HOME%"=="" set "AXIOM_HOME=%USERPROFILE%\.axiom"
if "%AXIOM_ENABLE_REGIME_LAB%"=="" set "AXIOM_ENABLE_REGIME_LAB=0"
if "%VITE_ENABLE_REGIME_LAB%"=="" set "VITE_ENABLE_REGIME_LAB=%AXIOM_ENABLE_REGIME_LAB%"
if "%START_BOT%"=="" set "START_BOT=0"
if "%START_LAB_WORKER%"=="" set "START_LAB_WORKER=0"
if "%START_DAEMON%"=="" set "START_DAEMON=1"
if "%BACKEND_WORKERS%"=="" set "BACKEND_WORKERS=1"
if "%SHOW_CHILD_WINDOWS%"=="" set "SHOW_CHILD_WINDOWS=0"
if "%FORCE_RESTART%"=="" set "FORCE_RESTART=1"

echo [start_all.bat] Repo: %CD%
echo [start_all.bat] AXIOM_HOME=%AXIOM_HOME%
echo [start_all.bat] AXIOM_ENABLE_REGIME_LAB=%AXIOM_ENABLE_REGIME_LAB%
echo [start_all.bat] START_BOT=%START_BOT%
echo [start_all.bat] START_LAB_WORKER=%START_LAB_WORKER%
echo [start_all.bat] START_DAEMON=%START_DAEMON%
echo [start_all.bat] BACKEND_WORKERS=%BACKEND_WORKERS%
echo [start_all.bat] SHOW_CHILD_WINDOWS=%SHOW_CHILD_WINDOWS%
echo [start_all.bat] FORCE_RESTART=%FORCE_RESTART%

powershell -NoProfile -ExecutionPolicy Bypass -File ".\start_all.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" (
  echo [start_all.bat][error] start_all.ps1 exited with code %EXIT_CODE%
) else (
  echo [start_all.bat] start_all.ps1 exited cleanly.
)
echo.
echo Press any key to close this window...
pause >nul
exit /b %EXIT_CODE%
