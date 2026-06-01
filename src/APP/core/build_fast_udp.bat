@echo off
echo ========================================
echo 编译 Fast UDP Receiver Cython 扩展
echo ========================================

cd /d "%~dp0"

echo.
echo [1/3] 检查 Cython...
python -c "import Cython" 2>nul
if errorlevel 1 (
    echo Cython 未安装，正在安装...
    pip install Cython
)

echo.
echo [2/3] 编译 Cython 扩展...
python setup_fast_udp.py build_ext --inplace

if errorlevel 1 (
    echo.
    echo ❌ 编译失败！
    echo 请确保已安装 Microsoft Visual C++ Build Tools
    pause
    exit /b 1
)

echo.
echo [3/3] 清理临时文件...
if exist "fast_udp_receiver.c" del /f /q "fast_udp_receiver.c"
if exist "build" rd /s /q "build"

echo.
echo ✅ 编译完成！
echo 生成文件: fast_udp_receiver.cp*.pyd
echo.
pause
