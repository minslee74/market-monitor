@echo off
cd /d "%~dp0"
echo ==== %date% %time% ==== >> collect_log.txt
python collect.py >> collect_log.txt 2>&1
echo. >> collect_log.txt
