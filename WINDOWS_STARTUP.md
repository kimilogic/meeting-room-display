# Windows起動時のSYSTEM実行（PowerShell 5.1）

会議室アプリ、Graph認証、6室の設定、画面、TCP **8020** は変更しません。新しいスクリプトは既存のTenant ID・Client IDを維持します。Client Secretは起動プロセスとPython子プロセスの環境変数だけに設定します。

## 採用方式

|方式|今回の判断|
|---|---|
|DPAPI LocalMachine＋NTFS ACL|採用。同じPCの管理者が登録し、SYSTEMが復号可能。Windows標準で追加モジュール不要。|
|Windows Credential Manager|ユーザーの資格情報コンテキストに依存し、SYSTEM用の登録・取得処理が別途必要。この用途では運用が増える。|
|DPAPI CurrentUser（既存ファイル）|登録したユーザーとSYSTEMが異なるため、そのままの移行には不適切。ロールバック用に保持。|
|AES暗号化＋鍵ファイル|鍵の保管・アクセス管理が別途必要になり、今回の利点がない。|

PowerShell 5.1では `Add-Type -AssemblyName System.Security` を先に実行します。これにより.NET Frameworkの `System.Security.Cryptography.ProtectedData` を使用します。PowerShell 7や外部暗号化モジュールの導入は不要です。

保存先は `%ProgramData%\MeetingRoomDisplay\client-secret.machine.dpapi`（バイナリ）です。登録時に専用フォルダーの継承を解除し、SYSTEMとローカルAdministratorsのみFullControlを設定します。既存の `client-secret.dpapi` は読み取り・変換・削除しません。**Secretの値を再入力してください**。Secret IDではなく値が必要です。値を保管していない場合は管理者側でSecretを再発行します。

LocalMachineは暗号化ファイルを読める同一PCの他ユーザーも復号できるため、ACLが必須です。管理者やSYSTEMからSecretを隠す仕組みではありません。復号後はMSALへ渡すためプロセスメモリに平文が存在しますが、平文ファイル・永続環境変数・コマンド引数には保存しません。別PCやOS再インストール後はSecretを再登録します。

参考：[Microsoft DPAPIのスコープ](https://learn.microsoft.com/en-us/dotnet/api/system.security.cryptography.dataprotectionscope)、[Windowsのパスワード保存方式](https://learn.microsoft.com/en-us/windows/win32/secbp/handling-passwords)、[タスクの実行主体](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtaskprincipal)。

## 切替手順

**管理者として起動したWindows PowerShell 5.1**で実行してください。今回、実Secret登録・稼働タスク変更・サーバ再起動は自動実施していません。

```powershell
Set-Location 'C:\Users\P1909I130036\Documents\Codex\2026-09-09\meeting-room-display'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\register-meeting-room-secret.ps1
```

通常のRead-HostでClient Secretを入力します（入力は画面に表示されます）。20文字未満は保存せず中止します。スクリプトはSecret本体をログ出力せず、保存ファイルを復号・照合して文字数だけを表示します。コマンド行やスクリプトに値を書かないでください。処理が成功してから次へ進みます。旧タスクを停止し、手動起動のUvicornがあればその元のウィンドウでCtrl+C停止します。

```powershell
Stop-ScheduledTask -TaskName 'Meeting Room Display'
Get-NetTCPConnection -LocalPort 8020 -State Listen -ErrorAction SilentlyContinue
```

8020の待受が残る場合は所有プロセスと起動元を確認し、適切な起動元から停止してください。無関係なプロセスを一括終了しないでください。待受がなくなってから登録します。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\register-meeting-room-task.ps1
# 上の登録が成功したことを確認してから実行
Start-ScheduledTask -TaskName 'Meeting Room Display'
```

登録スクリプトは旧タスク定義を `%ProgramData%\MeetingRoomDisplay\task-before-日時.xml` に保存してから、同名タスクを更新します。表示されたバックアップ名を控えてください。実行中のタスクは更新せずエラーにします。`-WhatIf` を付ければタスク定義を組み立てるだけで登録・フォルダー変更は行いません。

登録内容は次のとおりです。

- 実行主体：SYSTEM、ServiceAccount、Highest。ユーザーパスワード不要。
- トリガー：OS起動、30秒遅延。ユーザーログオンは不要。
- Action：Windows PowerShell 5.1の絶対パス、`-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "…\start-meeting-room-display.ps1"`。
- 作業ディレクトリ：プロジェクト。Uvicornは `0.0.0.0:8020`、1ワーカー。
- 実行時間制限なし、同一タスクの重複起動なし、異常終了時1分間隔で最大3回再起動。
- バッテリー稼働時にも開始・継続。開始機会を逃した場合は可能になり次第開始。

起動後にネットワークがまだ使えなければ、既存アプリのGraph再取得処理で復旧を待ちます。PCのスリープ中・電源断中は配信できません。常時配信が必要なら電源設定も管理者側で確認してください。

## PowerShellを閉じても停止しない理由

タスクスケジューラがSYSTEMの非対話セッションでPowerShellとその子のUvicornを起動します。`Start-ScheduledTask` を実行した管理者のウィンドウとは別の実行セッションなので、そのウィンドウを閉じたりユーザーがログオフしても影響しません。起動スクリプト自体はUvicornの終了を待ち、終了コードをタスクに返します。

**起動スクリプトを手動で直接実行し、そのウィンドウを閉じる場合は継続保証はありません。** 通常運用では必ずタスク経由で開始してください。タスクを停止した場合やOSを終了した場合には停止します。

## 確認・障害調査

自動確認（実Secret・Graphを使用せず、タスク登録もしません）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tests\test_windows_startup.ps1
```

PowerShell構文、System.Security読込、テストデータのLocalMachine暗号化・復号、SYSTEM・起動トリガー・時間制限なし等のタスク定義を検査します。CIMアクセスが制限された環境では `-SkipTaskScheduler` で暗号化まで検査できます。今回のCodex環境では構文とDPAPIは確認済み、CIMによるタスク定義確認は環境制限で未確認です。

ACLの組立ても、Set-Aclを模擬してSYSTEM・Administrators限定／継承解除を検査済みです（実ProgramDataのACL変更は未実施）。既存の設定・予定判定・Graph処理の32テストは成功しました。HTTP等を含む全体テストは結果が返らず中断しており、全体成功とは判定していません。実SecretでのSYSTEM復号・タスク起動・再起動後の動作は以下の利用環境での確認が必要です。

切替後は管理者PowerShellで確認します。

```powershell
Get-ScheduledTask -TaskName 'Meeting Room Display' | Select-Object TaskName,State,Principal
Get-ScheduledTaskInfo -TaskName 'Meeting Room Display'
Get-NetTCPConnection -LocalPort 8020 -State Listen
Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8020/room/idoutai' | Select-Object StatusCode
Invoke-RestMethod 'http://127.0.0.1:8020/api/room/idoutai/status'
Get-Acl "$env:ProgramData\MeetingRoomDisplay" | Format-List Owner,AccessToString
```

APIには予定等が含まれるため出力は社内で扱ってください。タスクのStateがRunningで、実行結果が `267009`（`0x41301`）の場合は実行継続中を表し、異常終了ではありません。起動エラー時はTask Schedulerの履歴、`%ProgramData%\MeetingRoomDisplay\startup-error.log`（失敗した処理段階のみ）、`server.log` を確認します。startup-error.logは過去の失敗記録が残る場合があるため更新日時を確認してください。Uvicornの前回ログは `server.log.previous` です。HTTPアクセスログは無効化し、トークンやGraphレスポンスを出力するデバッグログは追加していません。長期運用時はログ容量も確認してください。

1. 起動後、管理者PowerShellを閉じ、iPadの全6室の表示・15秒更新・Graph正常取得を確認する。
2. Windowsからログオフし、別端末のiPadで配信継続を確認する。
3. Windowsを再起動し、**ログオン画面のまま**30秒＋起動時間を待ち、iPadから同じURLを確認する。
4. 8020で待受するサーバが1つであること、予約者表示と臨時使用等の既存機能を確認する。

既存のPython本体は `C:\Users\P1909I130036\Documents\Codex\Python311` にあります。SYSTEMがプロジェクト・`.venv`・Python本体を読み取り／実行でき、`data`へ書き込める必要があります。ユーザーのEFS暗号化やログオン後のみ利用できるドライブは使用しないでください。SYSTEM実行ではユーザー固有のプロキシ設定等が使われない場合があるため、実Graph疎通は必ず確認してください。FirewallやMicrosoft 365の権限はこの変更では操作しません。

SYSTEMが実行するコード・Python・起動スクリプトを変更できる利用者は信頼できる管理者に限定するのが運用上の前提です。この変更ではユーザープロファイル全体のACLは変更していません。フォルダーを移動する際はタスクを再登録してください。

## Secret更新

同じPCで登録スクリプトを再実行し、その後 `Stop-ScheduledTask`、8020の停止確認、`Start-ScheduledTask` の順で再起動します。旧CurrentUser形式のファイルを新しいファイルへコピーしても使用できません。保存先はGitリポジトリ外で、さらに `.gitignore` で `*.dpapi` を除外しています。

## ロールバック

新タスクを止め、8020の待受がなくなったことを確認します。元の起動スクリプトは `start-meeting-room-display.pre-system.ps1` に保管済みです（Git除外）。旧 `client-secret.dpapi` はそのままです。

```powershell
Stop-ScheduledTask -TaskName 'Meeting Room Display'
Get-NetTCPConnection -LocalPort 8020 -State Listen -ErrorAction SilentlyContinue
# 待受がなくなってから実行
Copy-Item -LiteralPath .\start-meeting-room-display.pre-system.ps1 -Destination .\start-meeting-room-display.ps1 -Force
# 実際の切替時に出力された、旧InteractiveタスクのバックアップXMLを選択
$backup = Read-Host 'Full path of the original task-before-*.xml'
Register-ScheduledTask -TaskName 'Meeting Room Display' -TaskPath '\' -Xml (Get-Content -LiteralPath $backup -Raw) -Force
```

旧ユーザー `P1909I130036` でログオンした状態でタスクを開始してください。以前のInteractive実行へ戻るため、ログオン不要起動は失われます。復元確認が終わるまでは旧DPAPIファイル・元のスクリプト・XMLを保持してください。LocalMachine側の暗号化ファイルは削除しなくても旧版へ影響しません。
