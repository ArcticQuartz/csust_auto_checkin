<#
.SYNOPSIS
    注册「钉钉群签到」的 Windows 定时任务（每天定时 + 登录时各一个触发器）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\register_checkin_task.ps1

.EXAMPLE
    .\register_checkin_task.ps1 -At 06:40 -WindowStart 06:30 -WindowEnd 21:30 -Windowless

.NOTES
    - 只在当前用户登录时运行（adb 跟用户会话走）。
    - 签到有效时间 06:30-21:30，脚本在自己的窗口内最多重试 MaxMinutes 分钟。
    - 同一天不会重复签到（state\last_checkin.txt + 群里签到卡片双重判断）。
#>
param(
    [string]$TaskName = "钉钉群签到",
    [string]$At = "06:35",
    [string]$WindowStart = "06:30",
    [string]$WindowEnd = "21:30",
    [int]$MaxMinutes = 15,
    [string]$Python = "%PYTHONPATH%",
    [string]$ScriptDir = $PSScriptRoot,
    [string]$Serial = "",
    [switch]$NoLogonTrigger,
    [switch]$Windowless
)

$ErrorActionPreference = "Stop"

$script = Join-Path $ScriptDir "dingtalk_checkin.py"
if (-not (Test-Path $script)) { throw "找不到脚本：$script" }

if ($Windowless) { $Python = $Python -replace 'python\.exe$', 'pythonw.exe' }
if (-not (Test-Path $Python)) { throw "找不到 python：$Python" }

$argList = '"' + $script + '" --window ' + $WindowStart + '-' + $WindowEnd + ' --max-minutes ' + $MaxMinutes
if ($Serial) { $argList = $argList + ' --serial ' + $Serial }

$action = New-ScheduledTaskAction -Execute $Python -Argument $argList -WorkingDirectory $ScriptDir

$daily = New-ScheduledTaskTrigger -Daily -At $At
$triggers = @($daily)

if (-not $NoLogonTrigger) {
    $logon = New-ScheduledTaskTrigger -AtLogOn
    $logon.Delay = "PT2M"
    $triggers += $logon
}

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 2) -ExecutionTimeLimit (New-TimeSpan -Minutes 40)
$desc = "每天 " + $At + " 与登录时触发：adb 打开钉钉进入建筑学院群完成群签到（窗口 " + $WindowStart + "-" + $WindowEnd + "）"

$registerArgs = @{
    TaskName    = $TaskName
    Action      = $action
    Trigger     = $triggers
    Settings    = $settings
    Description = $desc
    Force       = $true
}
Register-ScheduledTask @registerArgs | Out-Null

Write-Host "已注册任务：$TaskName"
Write-Host "  触发器：每天 $At" + $(if ($NoLogonTrigger) { "" } else { " + 登录时(延迟2分钟)" })
Write-Host "  命令：$Python $argList"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
(Get-ScheduledTask -TaskName $TaskName).Triggers | Select-Object -Property CimClass, StartBoundary, Delay | Format-List
