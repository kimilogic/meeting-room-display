# No real secret, scheduled-task registration or ProgramData writes.
param([switch]$SkipTaskScheduler)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
foreach ($name in @('windows-runtime.ps1', 'start-meeting-room-display.ps1', 'register-meeting-room-secret.ps1', 'register-meeting-room-task.ps1')) {
    $tokens = $null
    $errors = $null
    [Management.Automation.Language.Parser]::ParseFile((Join-Path $root $name), [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count) { throw ('Parse failed: ' + $name) }
    Write-Host ('PASS syntax: ' + $name)
}
. (Join-Path $root 'windows-runtime.ps1')
$sample = [Text.Encoding]::UTF8.GetBytes('Synthetic-test-only-123')
$cipher = [Security.Cryptography.ProtectedData]::Protect($sample, $null, [Security.Cryptography.DataProtectionScope]::LocalMachine)
$decoded = [Security.Cryptography.ProtectedData]::Unprotect($cipher, $null, [Security.Cryptography.DataProtectionScope]::LocalMachine)
if ([Convert]::ToBase64String($sample) -cne [Convert]::ToBase64String($decoded)) { throw 'DPAPI roundtrip failed.' }
[Array]::Clear($sample, 0, $sample.Length)
[Array]::Clear($decoded, 0, $decoded.Length)
Write-Host 'PASS System.Security / LocalMachine DPAPI synthetic roundtrip'
# Inspect the exact ACL passed to Set-Acl without changing any permissions.
$testDirectory = Join-Path ([IO.Path]::GetTempPath()) ('meeting-room-acl-test-' + [Guid]::NewGuid().ToString())
[IO.Directory]::CreateDirectory($testDirectory) | Out-Null
function Get-MeetingRoomRuntimePath { return $testDirectory }
function Set-Acl {
    param($LiteralPath, $AclObject, $ErrorAction)
    if ($LiteralPath -ne $testDirectory) { throw 'Unexpected ACL target.' }
    $script:capturedAcl = $AclObject
}
try {
    Initialize-MeetingRoomPrivateDirectory | Out-Null
    if (-not $capturedAcl.AreAccessRulesProtected) { throw 'ACL inheritance enabled.' }
    $rules = @($capturedAcl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 2) { throw 'Unexpected ACL rule count.' }
    $identities = @($rules | ForEach-Object { $_.IdentityReference.Value })
    if ('S-1-5-18' -notin $identities -or 'S-1-5-32-544' -notin $identities) { throw 'Unexpected ACL identities.' }
    Write-Host 'PASS ACL construction: SYSTEM and Administrators only, inheritance disabled (Set-Acl mocked)'
} finally {
    Remove-Item Function:Set-Acl
    [IO.Directory]::Delete($testDirectory)
}
if ($SkipTaskScheduler) {
    Write-Host 'SKIP task definition validation (CIM unavailable in sandbox)'
    return
}
# Dot-source with WhatIf: builds actual CIM definition, performs no registration.
. (Join-Path $root 'register-meeting-room-task.ps1') -WhatIf
if ($definition.Principal.UserId -notin @('SYSTEM', 'S-1-5-18')) { throw 'Unexpected principal.' }
if ($definition.Principal.LogonType -ne 'ServiceAccount') { throw 'Unexpected logon type.' }
if ($definition.Settings.ExecutionTimeLimit -ne 'PT0S') { throw 'Execution timeout enabled.' }
if ($definition.Settings.MultipleInstances -ne 'IgnoreNew') { throw 'Multiple instances allowed.' }
if ($definition.Triggers[0].CimClass.CimClassName -ne 'MSFT_TaskBootTrigger') { throw 'Not a boot trigger.' }
if ($definition.Triggers[0].Delay -ne 'PT30S') { throw 'Unexpected delay.' }
if ($definition.Settings.RestartCount -ne 3) { throw 'Unexpected restart count.' }
Write-Host 'PASS SYSTEM / boot / no time limit / single instance / restart task definition'
