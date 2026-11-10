@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
set "FABRIC_DEMO_PYTHON="
set "FABRIC_DEMO_PY_ARGS="

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "FABRIC_DEMO_PYTHON=py"
    set "FABRIC_DEMO_PY_ARGS=-3"
    goto run_demo
  )
)
for %%P in (python python3) do (
  %%P -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "FABRIC_DEMO_PYTHON=%%P"
    set "FABRIC_DEMO_PY_ARGS="
    goto run_demo
  )
)
echo 未找到 Python 3.10 或更新版本。本脚本不会自动下载或安装软件。
echo 请用已有的 Python 3.10+ 执行 server.py --port 0 --open。
pause
exit /b 1

:run_demo
echo FabricSandbox 本地演示
echo 即将自动打开浏览器。服务会选择空闲端口，请保留此窗口。
echo 演示结束：先点网页中的“停止实验”，再在此处按 Control+C。
"%FABRIC_DEMO_PYTHON%" %FABRIC_DEMO_PY_ARGS% server.py --port 0 --open
if errorlevel 1 (
  echo 服务启动失败或异常退出。请查看上方错误信息。
  pause
  exit /b 1
)
endlocal
