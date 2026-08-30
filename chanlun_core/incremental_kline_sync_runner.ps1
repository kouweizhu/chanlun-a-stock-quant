# incremental_kline_sync_runner.ps1
# 由 Windows 任务计划程序每天 21:30 调用，触发 K线增量同步
$py = "C:\Users\13120\AppData\Local\Programs\Python\Python312\python.exe"
$script = "D:\常用文件\DeepSeek Harness项目\trading-skills\chanlun_core\incremental_kline_sync.py"
$log = "D:\常用文件\DeepSeek Harness项目\trading-skills\chanlun_core\logs\task_stdout.log"

$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "$ts [RUN] start"
& $py $script *>> $log
$code = $LASTEXITCODE
Add-Content -Path $log -Value "$ts [RUN] exit=$code"
exit $code
