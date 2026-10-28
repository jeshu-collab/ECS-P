@echo off
cd /d "%~dp0"
echo AquaMesh evidence-driven flood twin
echo   Hardware : python aquamesh_pi.py              (reads all detected COM ports)
echo   Sensors  : live USB serial telemetry only; no simulated readings
echo   Tests    : python tests\test_engine.py
echo   Open     : http://localhost:8000
python aquamesh_pi.py %*
pause
