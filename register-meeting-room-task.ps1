# Does not stop a server or start the task automatically.
[CmdletBinding(SupportsShouldProcess = $true)]
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows-runtime.ps1')
$taskName = 'Meeting Room Display'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$script = Join-Path $PSScriptRoot 'start-meeting-room-display.ps1'
$action = New-ScheduledTaskAction -Execute $powershell -WorkingDirectory $PSScriptRoot -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $script + '"')
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT30S'
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$definition = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings
if ($PSCmdlet.ShouldProcess($taskName, 'Back up existing definition and register SYSTEM startup task')) {
    Assert-MeetingRoomAdministrator
    $runtime = Initialize-MeetingRoomPrivateDirectory
    if (-not (Test-Path -LiteralPath (Join-Path $runtime 'client-secret.machine.dpapi') -PathType Leaf)) { throw 'Register the machine-scoped secret first.' }
    $existing = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
    if ($existing) {
        if ($existing.State -eq 'Running') { throw 'Stop this task first, then confirm port 8020 is free.' }
        $backup = Join-Path $runtime ('task-before-' + (Get-Date -Format 'yyyyMMdd-HHmmss-ffff') + '.xml')
        Export-ScheduledTask -TaskName $taskName -TaskPath '\' | Set-Content -LiteralPath $backup -Encoding Unicode
        Write-Host ('Previous task definition: ' + $backup)
    }
    Register-ScheduledTask -TaskName $taskName -TaskPath '\' -InputObject $definition -Force | Out-Null
    Write-Host 'Task registered. Use Start-ScheduledTask to start now, or reboot.'
}
