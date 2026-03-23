@echo off
setlocal

set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..\..
pushd "%PROJECT_ROOT%"

python "scripts\migrate\manage_migrations.py" %*
set EXIT_CODE=%ERRORLEVEL%

popd
exit /b %EXIT_CODE%
