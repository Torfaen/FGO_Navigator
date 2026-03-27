@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"
title FGO Auto Launcher

set "BOOTSTRAP_PKGS=opencv-python numpy pywin32 pillow"
set "BASE_PY="
set "VENV_PY=.venv\Scripts\python.exe"
set "REQ_FILE=requirements.txt"

where py >nul 2>nul
if %errorlevel%==0 (
    set "BASE_PY=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "BASE_PY=python"
    )
)

if "%BASE_PY%"=="" (
    echo [ERROR] 未找到 Python（py/python）。
    echo [HINT] 请先安装 Python 3 并勾选 Add to PATH。
    goto :END
)

set "NEED_CREATE_VENV=0"
if not exist "%VENV_PY%" set "NEED_CREATE_VENV=1"
if "%NEED_CREATE_VENV%"=="0" (
    "%VENV_PY%" -V >nul 2>nul
    if not %errorlevel%==0 (
        echo [WARN] 检测到 .venv 不可用（常见于跨电脑复制），准备重建...
        set "NEED_CREATE_VENV=1"
    )
)

if "%NEED_CREATE_VENV%"=="1" (
    if exist ".venv" (
        rmdir /s /q ".venv"
    )
    echo [INFO] 正在创建虚拟环境...
    %BASE_PY% -m venv ".venv"
    if not %errorlevel%==0 (
        echo [ERROR] 创建虚拟环境失败。
        goto :END
    )
)

echo [INFO] 正在升级 pip...
"%VENV_PY%" -m pip install -U pip
if not %errorlevel%==0 (
    echo [ERROR] pip 升级失败，请检查网络或镜像源。
    goto :END
)

if exist "%REQ_FILE%" (
    echo [INFO] 正在安装 requirements.txt 依赖...
    "%VENV_PY%" -m pip install -r "%REQ_FILE%"
    if not %errorlevel%==0 (
        echo [ERROR] requirements.txt 安装失败。
        goto :END
    )
) else (
    echo [WARN] 未找到 requirements.txt，安装基础依赖: %BOOTSTRAP_PKGS%
    "%VENV_PY%" -m pip install %BOOTSTRAP_PKGS%
    if not %errorlevel%==0 (
        echo [ERROR] 基础依赖安装失败，请确认网络并重试。
        goto :END
    )
)

echo [INFO] 使用虚拟环境 Python 启动...
"%VENV_PY%" "main.py"

:END
echo.
echo [INFO] 脚本已退出，按任意键关闭窗口...
pause >nul
endlocal
