@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================================
echo 工程规范智能体 V1.0 Desktop - Windows 安装包一键构建
echo ==========================================================
echo.

where py >nul 2>nul
if errorlevel 1 (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [错误] 未检测到 Python 3.11/3.12。
    echo 请先安装 64 位 Python，并勾选 Add Python to PATH。
    pause
    exit /b 1
  )
  set PY=python
) else (
  set PY=py -3.12
  %PY% --version >nul 2>nul
  if errorlevel 1 set PY=py -3.11
)

echo [1/5] 创建构建环境...
if not exist ".buildenv\Scripts\python.exe" (
  %PY% -m venv .buildenv
  if errorlevel 1 goto :fail
)
call .buildenv\Scripts\activate

echo [2/5] 安装桌面版构建依赖...
python -m pip install --upgrade pip
pip install -r requirements-build.txt
if errorlevel 1 goto :fail

echo [3/5] 运行桌面版代码检查...
python desktop_tools\preflight.py
if errorlevel 1 goto :fail

echo [4/5] 生成 Windows EXE...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
pyinstaller --noconfirm --clean installer\EngineeringNormAgent.spec
if errorlevel 1 goto :fail

echo [5/5] 生成 Setup.exe...
set ISCC=
if exist "%ProgramFiles%\Inno Setup 7\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 7\ISCC.exe
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe

if "%ISCC%"=="" (
  echo.
  echo [提示] 已成功生成可直接运行的桌面程序：
  echo   dist\EngineeringNormAgent\EngineeringNormAgent.exe
  echo.
  echo 但未检测到 Inno Setup，因此暂未生成安装程序 Setup.exe。
  echo 可安装 Inno Setup 后再次运行本脚本。
  echo 官方可使用 winget 安装：
  echo   winget install --id JRSoftware.InnoSetup.7 -e -s winget -i
  echo.
  pause
  exit /b 0
)

"%ISCC%" "installer\EngineeringNormAgent.iss"
if errorlevel 1 goto :fail

echo.
echo ==========================================================
echo 构建完成！
echo 安装包：release\工程规范智能体_V1.0_Setup.exe
echo 便携目录：dist\EngineeringNormAgent\
echo ==========================================================
pause
exit /b 0

:fail
echo.
echo [失败] 构建过程中发生错误。请保留本窗口并检查上方信息。
pause
exit /b 1
