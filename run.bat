@echo off
REM run.bat — Process all CCTV clips and feed events into the API.
REM Usage: run.bat [STORE_ID] [API_URL]
REM Example: run.bat STORE_BLR_002 http://localhost:8000

SET STORE=%1
IF "%STORE%"=="" SET STORE=STORE_BLR_002

SET API=%2
IF "%API%"=="" SET API=http://localhost:8000

echo Running Store Intelligence Pipeline
echo   Store : %STORE%
echo   API   : %API%
echo.

python -m pipeline.run_pipeline --store %STORE% --api %API%
