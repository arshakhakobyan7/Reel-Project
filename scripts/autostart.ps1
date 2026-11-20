<#
.SYNOPSIS
    Start Reel automatically when this Windows user signs in (T-903, D-055).

.DESCRIPTION
    Registers a Task Scheduler task for the current user that runs
    ".venv\Scripts\pythonw.exe -m reel <Arguments>" in the project folder. pythonw opens no
    console window; Reel writes its log to logs\reel-serve.jsonl.

    The task:
    - starts at sign-in, and also later if the computer was off at that time;
    - restarts Reel 3 times, one minute apart, if it stops with an error;
    - has no time limit and also runs on battery;
    - never starts a second copy.

    Nothing needs administrator rights. The task runs only while the user is signed in, so
    no password is stored.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 install
    powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 status
    powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 start
    powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 uninstall
#>
param(
    [ValidateSet("install", "uninstall", "status", "start", "stop")]
    [string]$Action = "status",
    [string]$TaskName = "Reel",
    [string]$Arguments = "serve"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$user = "$env:USERDOMAIN\$env:USERNAME"

switch ($Action) {
    "install" {
        if (-not (Test-Path $pythonw)) {
            throw "Reel's Python is missing: $pythonw. Follow the setup in README.md first."
        }
        $action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m reel $Arguments" `
            -WorkingDirectory $root
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -MultipleInstances IgnoreNew
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive `
            -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal -Force `
            -Description "Reel: lesson recordings edited and published automatically" | Out-Null
        Write-Output "Installed: '$TaskName' starts Reel when $user signs in."
        Write-Output "Start it now with: scripts\autostart.ps1 start"
    }
    "uninstall" {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Output "Removed: '$TaskName'."
        } else {
            Write-Output "There is no task '$TaskName'."
        }
    }
    "start" {
        Start-ScheduledTask -TaskName $TaskName
        Write-Output "Started '$TaskName'."
    }
    "stop" {
        Stop-ScheduledTask -TaskName $TaskName
        Write-Output "Stopped '$TaskName'."
    }
    "status" {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Output "There is no task '$TaskName'. Install it with: scripts\autostart.ps1 install"
            exit 1
        }
        $info = $task | Get-ScheduledTaskInfo
        Write-Output ("'{0}': {1}; last run {2} (result {3})" -f $TaskName, $task.State,
            $info.LastRunTime, $info.LastTaskResult)
    }
}
