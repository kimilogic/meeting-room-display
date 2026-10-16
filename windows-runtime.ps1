# Windows PowerShell 5.1 helpers; no secret arguments.
Set-StrictMode -Version 2.0
Add-Type -AssemblyName System.Security -ErrorAction Stop

function Get-MeetingRoomRuntimePath {
    Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MeetingRoomDisplay'
}
function Assert-MeetingRoomAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script in an elevated Windows PowerShell window.'
    }
}
function Initialize-MeetingRoomPrivateDirectory {
    $path = Get-MeetingRoomRuntimePath
    if (Test-Path -LiteralPath $path) {
        $item = Get-Item -LiteralPath $path -Force
        if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Runtime path must be a regular local directory.'
        }
    } else {
        New-Item -ItemType Directory -Path $path -ErrorAction Stop | Out-Null
    }
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $admins = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
    $acl.SetOwner($admins)
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $path -AclObject $acl -ErrorAction Stop
    return $path
}
