$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows-runtime.ps1')
Assert-MeetingRoomAdministrator
$runtime = Initialize-MeetingRoomPrivateDirectory
$path = Join-Path $runtime 'client-secret.machine.dpapi'
$temp = Join-Path $runtime ([Guid]::NewGuid().ToString() + '.dpapi')
$secret = $null
$plainBytes = $null
$verification = $null
$failureMessage = 'Secret registration failed. Check elevation and runtime folder permissions.'
try {
    $secret = [string](Read-Host 'Enter Microsoft Graph Client Secret VALUE')
    if ($secret.Length -lt 20) {
        $failureMessage = 'Secret must contain at least 20 characters. Nothing was saved.'
        throw $failureMessage
    }
    $plainBytes = [Text.Encoding]::UTF8.GetBytes($secret)
    $cipher = [Security.Cryptography.ProtectedData]::Protect(
        $plainBytes, $null, [Security.Cryptography.DataProtectionScope]::LocalMachine)
    # Temp inherits the private directory ACL; replacement keeps that ACL.
    [IO.File]::WriteAllBytes($temp, $cipher)
    Move-Item -LiteralPath $temp -Destination $path -Force
    $failureMessage = 'Secret file was written, but verification failed. Register the secret again before restarting the task.'
    $verification = [Security.Cryptography.ProtectedData]::Unprotect(
        [IO.File]::ReadAllBytes($path), $null, [Security.Cryptography.DataProtectionScope]::LocalMachine)
    if (-not [string]::Equals($secret, [Text.Encoding]::UTF8.GetString($verification), [StringComparison]::Ordinal)) {
        throw $failureMessage
    }
    Write-Host ('Saved secret character count: ' + [Text.Encoding]::UTF8.GetCharCount($verification))
} catch {
    throw $failureMessage
} finally {
    if ($null -ne $plainBytes) { [Array]::Clear($plainBytes, 0, $plainBytes.Length) }
    if ($null -ne $verification) { [Array]::Clear($verification, 0, $verification.Length) }
    $secret = $null
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
}
