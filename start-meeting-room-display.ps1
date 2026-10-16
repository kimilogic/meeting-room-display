Set-Location "C:\Users\P1909I130036\Documents\Codex\2026-09-09\meeting-room-display"

$env:TENANT_ID = "2f80d0ba-03b2-4fe4-9679-e9c60963ad50"
$env:CLIENT_ID = "0854303d-8489-4a42-9b18-2972c83c31b3"

$secureSecret = Get-Content ".\client-secret.dpapi" | ConvertTo-SecureString
$env:CLIENT_SECRET = [System.Net.NetworkCredential]::new("", $secureSecret).Password

& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8020