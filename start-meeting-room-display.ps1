# Use the SYSTEM scheduled task for unattended operation.
$ErrorActionPreference = 'Stop'
$exitCode = 1
$plainBytes = $null
$runtime = $null
$stage = 'load runtime helpers'
try {
    . (Join-Path $PSScriptRoot 'windows-runtime.ps1')
    $runtime = Get-MeetingRoomRuntimePath
    $stage = 'locate Python virtual environment'
    Set-Location -LiteralPath $PSScriptRoot
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Python not found.' }
    $stage = 'read and decrypt machine secret'
    $encryptedBytes = [IO.File]::ReadAllBytes((Join-Path $runtime 'client-secret.machine.dpapi'))
    $plainBytes = [Security.Cryptography.ProtectedData]::Unprotect(
        $encryptedBytes, $null, [Security.Cryptography.DataProtectionScope]::LocalMachine)
    $env:TENANT_ID = '2f80d0ba-03b2-4fe4-9679-e9c60963ad50'
    $env:CLIENT_ID = '0854303d-8489-4a42-9b18-2972c83c31b3'
    $env:CLIENT_SECRET = [Text.Encoding]::UTF8.GetString($plainBytes)
    if ([string]::IsNullOrWhiteSpace($env:CLIENT_SECRET)) { throw 'Secret is empty.' }
    [Array]::Clear($plainBytes, 0, $plainBytes.Length)
    $env:PYTHONUNBUFFERED = '1'
    $stage = 'prepare Uvicorn log'
    $log = Join-Path $runtime 'server.log'
    if (Test-Path -LiteralPath $log) { Move-Item -LiteralPath $log -Destination ($log + '.previous') -Force }
    # PS 5.1 wraps native stderr as ErrorRecord; normal Uvicorn logging must not abort.
    $ErrorActionPreference = 'Continue'
    & $python -m uvicorn app.main:app --host 0.0.0.0 --port 8020 --workers 1 --no-access-log *> $log
    $exitCode = $LASTEXITCODE
} catch {
    $message = 'Meeting Room Display startup failed at: ' + $stage + '. Check registration and permissions.'
    if ($runtime) {
        try { [IO.File]::WriteAllText((Join-Path $runtime 'startup-error.log'), $message) } catch { }
    }
    Write-Error $message -ErrorAction Continue
} finally {
    if ($null -ne $plainBytes) { [Array]::Clear($plainBytes, 0, $plainBytes.Length) }
    Remove-Item Env:CLIENT_SECRET -ErrorAction SilentlyContinue
}
exit $exitCode
