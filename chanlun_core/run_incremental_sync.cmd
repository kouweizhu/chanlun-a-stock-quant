@echo off
rem Launcher for incremental_kline_sync.py
rem Forces Python UTF-8 mode so the script's emoji/stdout prints do not crash
rem under the GBK Windows console used by Task Scheduler (avoids UnicodeEncodeError).
setlocal
set PYTHONUTF8=1
"C:\Users\13120\AppData\Local\Programs\Python\Python312\python.exe" "D:\常用文件\DeepSeek Harness项目\trading-skills\chanlun_core\incremental_kline_sync.py"
endlocal
