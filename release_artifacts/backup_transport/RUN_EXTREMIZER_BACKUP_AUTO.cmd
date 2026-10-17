@echo off
setlocal
set "BASE=C:\Users\ASUS\Desktop\Работа\DCP Railway Test\predeploy_20261004"
set "PY=C:\Users\ASUS\AppData\Local\Python\pythoncore-3.14-64\python.exe"
set "RAILWAY=C:\Railway\railway.exe"
set "PATH=C:\Program Files\Git\usr\bin;%PATH%"
"%PY%" "%BASE%\backup_auto_main.py" --railway "%RAILWAY%" --remote-root "/data/backups/auto_transport" --inbox "%BASE%\auto\inbox" --business-source "%BASE%\auto\business_source" --daily-root "%BASE%\auto\daily" --log-dir "%BASE%\auto\logs" --deployment-id "AUTO" --max-age-minutes 90
exit /b %ERRORLEVEL%
