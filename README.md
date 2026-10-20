# 会議室スケジュール表示 PoC（dummy / Microsoft Graph）

Windows PC上のFastAPI/Uvicornから、設定した6室の予定を会議室別URLから古いiPad SafariへLAN内HTTPで配信します。既存の横向き画面、ES5相当のJavaScript、XMLHttpRequest、15秒更新を維持しています。運用ポートは **8020** です。

## 1. 起動とモード切替

**Windows起動時・ログオン不要で運用する場合：** [SYSTEMタスク登録とSecret保存手順](WINDOWS_STARTUP.md) を参照してください。以下の直接起動は開発・手動確認用です。通常運用では `Start-ScheduledTask -TaskName 'Meeting Room Display'` を使用します。

Python 3.11以降を使用し、このフォルダーでPowerShellを開きます。既存の `.venv` はそのまま利用できます。初回のみ `python -m venv .venv` で作成します。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8020
```

Activateは不要です。停止はCtrl+C。稼働中の旧PoCがある場合は、その起動元ターミナルで停止してから新しいコードを起動してください。他のプロセスを勝手に終了しないでください。**1ワーカーで運用**し、複数のUvicornプロセスや `--workers 2` 等を使用しないでください。メモリキャッシュとGraph取得スレッドはプロセスごとに作られます。

`config.toml` は起動時に読み込みます。変更後はサーバを再起動してください。

現在の設定は `DATA_SOURCE = "graph"` です。利用者が指定した6室のID・表示名・UPNを設定済みです。dummy確認時だけDATA_SOURCEを変更し、roomsはそのまま利用します。

```toml
DATA_SOURCE = "graph"

[[rooms]]
id = "idoutai"
name = "移動体本部 ミーティングルーム"
upn = "idoutai_meeting_room@araidenki7000.onmicrosoft.com"

# 他の5室もconfig.tomlに設定済み。
```

| 項目 | 設定内容 |
| --- | --- |
| DATA_SOURCE | 全室共通の `dummy` または `graph` |
| rooms[].id | URLで使用する一意のID（小文字英数字・ハイフン） |
| rooms[].name | 画面上の会議室名 |
| rooms[].upn | 対象会議室のUPN |

ID・UPNの重複、空のrooms、graphでのUPN不足は設定エラーです。既存URLの互換性のため `idoutai` は必須です。Tenant ID・Client ID・Client Secretは従来の環境変数を全室で共用し、config.tomlへ記載しません。UTF-8（BOMあり・なし両対応）で保存してください。

旧ROOM_NAME/ROOM_UPN形式のファイルも1室版のidoutaiとして読み込めますが、新しいrooms形式と併記はできません。

設定不足のgraphモードからdummyへ自動フォールバックはしません。画面は「設定エラー」になり、空室とは表示しません。dummyへ戻す場合は明示的に設定を変更して再起動します。

## 2. Graph用の環境変数

アプリを起動するPowerShellプロセスに次を設定します。

- `TENANT_ID`：テナントID（GUID）
- `CLIENT_ID`：アプリケーション／クライアントID（GUID）
- `CLIENT_SECRET`：クライアントシークレットの **値**（Secret IDではありません）

Secretをコマンド履歴へ直書きしない入力例です。実値は入力画面で利用者が設定します。この操作をCodex側では実行していません。

```powershell
$env:TENANT_ID = Read-Host "Tenant ID"
$env:CLIENT_ID = Read-Host "Client ID"
$pocSecret = Read-Host "Client Secretの値" -AsSecureString
$pocSecretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pocSecret)
try {
    $env:CLIENT_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pocSecretPtr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pocSecretPtr)
    Remove-Variable pocSecret, pocSecretPtr
}
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8020
```

この設定はそのPowerShellと子プロセスだけに渡ります。別ターミナルからの起動には引き継がれません。環境変数も実行プロセス内では平文になるため、PCへのアクセスを管理してください。停止後は `Remove-Item Env:CLIENT_SECRET -ErrorAction SilentlyContinue` でそのシェルから削除できます。

`.env`の自動読込は実装していません。誤って作成してもGitへ入らないよう `.env` / `.env.*` は除外しています。トークン、資格情報、Graphの生レスポンスはディスクに保存しません。

## 3. Microsoft 365管理者の準備（管理者が実施）

アプリからテナント設定変更は行いません。今回の6室は管理者によるRBAC設定とInScope=Trueの確認が完了済みです。既存の権限を再作成する必要はありません。以下は新規構築・追加室設定時の参考手順です。

1. 対象がExchange Onlineの会議室メールボックスであること、UPNと設備予定表を確認する。
2. Entra IDでシングルテナントのアプリ登録とサービスプリンシパルを用意する。Client Credentials用なのでリダイレクトURIは不要。
3. Client Secretを作成し、値を安全な経路で提供する。有効期限と更新担当を決める。
4. Exchange Onlineの **RBAC for Applications** で `Application Calendars.Read` を対象会議室を含むスコープに割り当てる（下記は1室を対象にする設定例）。
5. 許可した対象室がアクセスでき、管理者指定の対象外メールボックスが拒否されることを確認する。
6. desknet's NEOからの件名・変更・取り消しが会議室予定表へ反映されることを確認する。

RBAC権限とEntra IDのアプリケーション権限は加算されます。Entra ID側に全メールボックス対象の `Calendars.Read`、`Calendars.ReadBasic.All`、`Calendars.ReadWrite` 等を重ねて付与しないでください。RBACのスコープだけでは別途付与した権限を狭められません。書き込み権限は不要です。[Microsoft公式：RBAC for Applications](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac)

管理者向けの設定例です。既存のサービスプリンシパル・スコープ・割当がある場合は内容を確認し、重複作成せず既存設定を利用します。名前と実値は管理者が入力します。

```powershell
Connect-ExchangeOnline
$pocAppId = Read-Host "Client ID"
$pocSpId = Read-Host "Enterprise applications側のObject ID"
$pocRoom = Read-Host "対象会議室UPN"
$pocScope = Read-Host "作成するスコープ名"
$pocAssignment = Read-Host "作成するロール割当名"

# App registrations側のObject IDとは異なる点に注意。
New-ServicePrincipal -AppId $pocAppId -ObjectId $pocSpId -DisplayName $pocAssignment

$pocMailbox = Get-Mailbox -Identity $pocRoom
$pocAddress = $pocMailbox.PrimarySmtpAddress.ToString().Replace("'", "''")
$pocFilter = "RecipientTypeDetails -eq 'RoomMailbox' -and PrimarySmtpAddress -eq '$pocAddress'"
$pocTargets = @(Get-Recipient -RecipientPreviewFilter $pocFilter)
if ($pocTargets.Count -ne 1) { throw "対象が1室に限定されていません" }
$pocTargets | Format-Table DisplayName, PrimarySmtpAddress, RecipientTypeDetails

# 表示された対象が意図した1室であることを確認してから実行。
New-ManagementScope -Name $pocScope -RecipientRestrictionFilter $pocFilter
New-ManagementRoleAssignment -Name $pocAssignment -Role "Application Calendars.Read" -App $pocAppId -CustomResourceScope $pocScope
Test-ServicePrincipalAuthorization -Identity $pocAppId -Resource $pocRoom

$pocOutside = Read-Host "管理者が検証用に指定する対象外メールボックス"
Test-ServicePrincipalAuthorization -Identity $pocAppId -Resource $pocOutside
```

対象室は当該ロールの `InScope=True`、対象外はFalseを確認します。このテストはEntra ID側に別途付与した権限を評価しないため、Entra IDの割当も確認し、最終的には同じアプリで実Graph呼び出しの許可・拒否を検証します。権限変更の反映にはキャッシュによる時間差があります。[Microsoft公式：権限テストと制約](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac)

件名が期待と異なる場合は、管理者が `Get-CalendarProcessing -Identity $pocRoom | Format-List DeleteSubject,AddOrganizerToSubject,RemovePrivateProperty` で設備予定表の処理設定を確認します。元件名や非公開属性が連携途中で失われた場合、表示アプリでは復元できません。設定変更は既存連携への影響を確認して管理者が判断します。

## 4. Graph取得と表示仕様

- MSALのClient Credentialsと `https://graph.microsoft.com/.default` でアプリ専用トークンを取得します。MSALインスタンスとトークンキャッシュはサーバメモリ内で再利用します。[Microsoft公式：MSAL](https://learn.microsoft.com/en-us/entra/msal/python/advanced/client-credentials)
- `GET https://graph.microsoft.com/v1.0/users/{room}/calendarView` のみを予定取得に使用します。
- 取得期間は日本時間の当日00:00から **翌々日00:00を終端とする2日間**。翌日23:59台も取りこぼさない指定です。範囲指定に `+09:00` を付け、応答は `Prefer: outlook.timezone="UTC"` で要求します。
- `$select` は `id,subject,start,end,isCancelled,isAllDay,showAs,sensitivity,organizer`。本文、添付、参加者一覧は要求しません。
- `@odata.nextLink` の全ページを取得・検証してから、一括でキャッシュを置き換えます。途中失敗、不正な応答、ページ循環は更新失敗として扱います。nextLinkはGraphのHTTPS URLのみ許可し、HTTPリダイレクトには追従しません。[Microsoft公式：calendarView](https://learn.microsoft.com/en-us/graph/api/calendar-list-calendarview?view=graph-rest-1.0)
- Graphは起動直後に取得し、取得完了後は原則60秒待って再取得します。iPadの15秒取得とは独立しており、表示APIのアクセスでGraph問い合わせは増えません。
- HTTP 429等で `Retry-After` がある場合は、その指定と60秒の長い方を待ちます。401はトークンキャッシュをクリアして1回だけ再試行します。
- 内部予定日時はUTCのタイムゾーン付きdatetime。表示は日本時間。サーバの時計を正しく合わせてください。
- `開始 <= 現在 < 終了` の予約を使用中とします。取り消し予定は除外。`showAs=free` は一覧に「空き時間扱い」として残しますが、使用中・次予約から除外します。
- `private` / `confidential` の件名は正規化時点で「非公開の予定」に置換し、元件名をキャッシュ・APIに残しません。
- 終日・日またぎ予定に対応。仮予約・外出等はfree以外なら予約として扱います。
- 同時に有効な予約が複数あれば「予約重複」と件数を表示し、現在予定欄に各件名・時間を列挙します。任意の1件だけの終了時刻を大きく表示しません。同時刻開始の次予約もまとめます。
- 一覧は本日と重なる予定だけ。次予定には翌日の予定も含め、翌日なら日付付きで表示します。取得範囲外の予定は調べません。

## 5. 通信失敗・古い情報の扱い

| 状況 | 表示 |
| --- | --- |
| 初回取得中 | 取得中。空室と断定しない |
| 設定不足 | 設定エラー。dummyへ自動切替しない |
| 初回Graph取得失敗 | 未取得と安全な原因メッセージ |
| Graph取得失敗・成功キャッシュあり | 前回の予定を維持し「更新遅延」。最終更新は進めない |
| 最終成功から5分以上／取得範囲を過ぎた | 「状態未確認」。キャッシュ内の該当予定は引き続き表示 |
| iPad→FastAPI通信失敗 | 画面側で前回表示と最終更新を維持。通信失敗を表示し再試行 |
| 復旧 | 新しい全件取得成功後にキャッシュ・最終更新を更新し、警告解除 |

Graphエラーだけでは表示APIを500にしません。JSONの `warning` / `connection_text` と `status=unknown` を画面へ返します。生の例外やGraphエラー本文は返しません。

Graphモードの「最終更新」は **全ページ取得に成功した時刻** です。15秒ごとの画面取得や時計更新では進みません。キャッシュからの現在・次予定の判定自体はAPI応答時にサーバで再計算します。5分以内でも失敗後の判定は前回取得情報に基づくため、更新遅延の警告を併記します。

Graph予定のキャッシュはメモリ保持のみなので、サーバ再起動で消えます。臨時使用の開始記録は後述のJSONファイルに別途保存します。ページ再読込後のオフライン表示は対象外です。Microsoft 365側の最新取得に成功しても、desknet's NEOの同期が完了したことまでは保証しません。

## 6. 実予定での接続確認

1. 管理者のRBAC設定とSecret準備を完了する。
2. requirements.txtの依存をインストールする。`.\.venv\Scripts\python.exe -c "import msal, requests; print(msal.__version__, requests.__version__)"` で追加ライブラリを確認する。`.\.venv\Scripts\python.exe -m pip check` はインストール済みライブラリ間の整合性確認に使用できるが、未導入のrequirements.txt項目は検出しない。
3. config.tomlでgraphを選択し、各roomsのid・name・upnを確認する。
4. 起動するPowerShellに3つの環境変数を設定する。値やトークンを画面共有・ログ・チャットへ貼らない。
5. 稼働中の旧PoCを起動元から停止し、8020で再起動する。
6. PCで [画面](http://127.0.0.1:8020/) を開き、「Microsoft 365」と最終更新が表示されることを確認する。
7. 次のコマンドでHTTP応答と表示メタデータを確認する。件名一覧や生Graphレスポンスをログ出力する必要はない。

```powershell
$pocResponse = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8020/api/display
$pocResponse.StatusCode
$pocDisplay = $pocResponse.Content | ConvertFrom-Json
$pocDisplay | Select-Object status, is_demo, warning, updated_text, connection_text
```

8. desknet's NEO／Microsoft 365の既知の予定と画面を照合し、繰り返し・取り消し・非公開・free・日またぎを確認する。アプリから予約変更は行わない。
9. 2分以上表示して、画面は約15秒、Graph最終更新は原則約60秒＋取得時間で進むことを確認する。
10. iPadがLANでPCへ到達できる状態を保って、管理された方法でPCの外向きGraph通信だけを一時的に遮断し、更新遅延・最終更新維持・復旧を確認する。5分超で状態未確認になることも確認する。
11. 別途、PCのサーバを停止し、iPad側の通信失敗・前回表示維持と再起動後の復旧を確認する。

初期の設定エラーや権限エラーは環境変数、Secretの値と有効期限、UPN、対象会議室のRBAC割当を確認してください。ライブラリ不足ならrequirements.txtを再インストールします。接続失敗ならPC→Entra ID/Graphへの外向きHTTPSと社内プロキシ・証明書を確認し、TLS検証を無効化しないでください。

## 7. LAN・古いiPad

`ipconfig` で使用中のWi-Fi／EthernetのIPv4アドレスを確認し、iPad Safariで `http://<PCのIPv4アドレス>:8020/` を開きます。VPN・仮想アダプターのIPや `0.0.0.0` は接続先に使いません。

既に8020で動作しているFirewall設定は変更不要です。アプリからFirewall変更は行いません。新しいPC等で管理者が許可した場合のみ、管理者PowerShellで次を実行します。

```powershell
New-NetFirewallRule -DisplayName "Meeting Room PoC TCP 8020" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8020 -RemoteAddress LocalSubnet -Profile Domain,Private
```

既存ルールがある場合は重複作成しません。Publicプロファイル・別サブネット・ゲストWi-Fiの接続制限は管理者へ確認してください。

古いiPadでは次を確認します。

- iOSバージョンを記録。暫定目標はiOS 9.3以降、実機結果を優先。
- 横向きで時計・状態・実際の長い件名・重複予定・最終更新が読める。
- 1024×768程度を基本に、実予定が多い場合は既存の縦スクロールで一覧を確認する。
- タップしなくても時計とデータが更新される。
- Safariの「ホーム画面に追加」から起動しても表示・更新される。
- 自動ロックを無効にできるか確認し、給電・横向き固定・必要ならアクセスガイドを設定。
- スリープ、アプリ切替、Wi-Fi復旧後の再取得を確認。バックグラウンドでの継続実行は保証しない。

既存の画面構成を維持し、現在・次・本日の予定に予約者欄を追加しました。CSSは予約者の文字サイズ・余白・長文折り返しを追加しています。fetch、Promise、async/await、CSS Grid、CSS変数、Flex gapは追加していません。

## 8. テスト

実認証情報は不要です。GraphとMSALは模擬化し、テストから実テナントへ接続しません。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check app/static/app.js
node tests/test_frontend.cjs
```

Node.jsはフロントエンドの模擬テストだけに使用し、アプリ運用には不要です。HTTPテストはループバックの一時ポートを使用し、稼働中の8020サービスを停止・変更しません。

検証対象はdummy、予定なし、使用中／次予定、境界、取消、free、非公開、重複、終日／日またぎ、翌日の次予定、Graph模擬応答、ページング、途中失敗の原子的保持、最終更新維持、401再認証、429待機、不正なnextLink、設定不足、APIのHTTP 200応答、XHRの失敗保持／復旧です。

臨時使用は対象6室の状態欄で利用できます。テストでは実Graph通信を模擬化し、テナントへの再接続は行いません。旧iPadでのタップ操作・ボタン追加後の高さの最終確認は利用者側で実施してください。

## 9. ファイル構成とセキュリティ

```text
app/main.py             起動・終了、画面／API配信
app/config.py           TOMLと環境変数の読込・検証
app/graph.py            MSAL認証、読み取り専用Graph取得
app/store.py            60秒取得、メモリキャッシュ、障害状態
app/ad_hoc.py           会議室ごとの臨時使用、保存、正式予約との調整
app/schedule.py         dummy生成、正規化、予定判定
app/static/             既存HTML / CSS / ES5 JavaScript
config.toml             全室共通モードとrooms配列（秘密情報なし）
requirements.txt        Python依存
tests/                  実認証情報不要のテスト
```

- CLIENT_SECRET、トークン、Graphレスポンス全体のログ出力は行いません。ライブラリのDEBUG／PIIログを有効化しないでください。
- Secretは環境変数のみ。実値や架空のテナント設定は同梱しません。テスト内の合成値は外部へ送信しません。
- 設備UPNとイベントIDは表示APIへ渡しません。ブラウザへ返すのは表示用情報だけです。
- Graph通信はサーバ側HTTPS、読み取り専用です。iPadは同一オリジンの表示APIだけに接続します。
- LAN内HTTPは継続します。実件名が表示されるため、閲覧できる端末／ネットワーク範囲と件名公開方針を管理者と確認してください。
- 今回は予約変更、HTTPS化、Windowsサービス化、DB、Service Worker、WebSocket、テナント設定変更を含みません。

次工程は管理者設定・実予定接続・iPad実機検証（中、管理者準備済みなら小）。


## 10. 予約者表示

Graphの `organizer.emailAddress.name` を優先し、空欄・未取得なら `address`、どちらもなければ「－」を表示します。organizerは予定の主催者を表すため、画面ラベルは「使用者」ではなく「予約者」です。[Microsoft公式：event](https://learn.microsoft.com/en-us/graph/api/resources/event?view=graph-rest-1.0)

表示APIでは `current.organizer`、`next.organizer`、`events[].organizer` を文字列として追加しました。予定がない場合のcurrent/nextは従来どおりnullです。重複時は件名・時刻と同じ順で予約者を列挙します。dummyでは「テスト予約者1」等を表示します。

非公開予定は従来どおり件名を「非公開の予定」にしますが、今回のPoCでは予約者を表示します。予約者の処理は `organizer_for_display()` に分離し、将来 `hide_private=True` を指定すると正規化時に非公開予定の予約者も「－」へ置換できます（現在は非表示設定を有効化していません）。

反映には8020で稼働するサーバを起動元から再起動し、iPadのページも再読み込みしてください。CSS/JavaScriptのURLに更新識別子を付け、古いキャッシュとの混在を避けています。60秒Graph取得・15秒画面更新を維持し、認証は全室共用、キャッシュは会議室ごとに分離しています。

実機では、現在・次・本日一覧の予約者、長い名前／メールアドレスの折り返し、非公開予定、予約重複、無操作更新、通信失敗時の予約者表示維持を確認してください。表示行が増えるため、画面高さによっては従来同様に一覧の縦スクロールが必要です。


## 11. 複数会議室の運用

1台のWindows PC、1つのUvicornプロセス、同じ8020ポートで以下を配信します。ハイゼットは設定に含みません。

| ID | 会議室名 | パス |
| --- | --- | --- |
| idoutai | 移動体本部 ミーティングルーム | /room/idoutai |
| bc-first | ビジネスセンター第1会議室 | /room/bc-first |
| bc-second | ビジネスセンター第2会議室 | /room/bc-second |
| ds-asagaya | DS阿佐ヶ谷 会議室1 | /room/ds-asagaya |
| ds-tachikawa-1 | DS立川北 第1会議室 | /room/ds-tachikawa-1 |
| ds-tachikawa-2 | DS立川北 第2会議室 | /room/ds-tachikawa-2 |

iPadで開くURLは `http://<Windows PCのIPv4アドレス>:8020` に上のパスを付けます。各ページのJavaScriptは `/api/room/{room_id}/status` だけを参照します。APIレスポンスには識別用の `room_id` を追加し、UPNや認証情報は含めません。存在しないIDは画面URL・APIともHTTP 404となり、別室へフォールバックしません。

**既存互換性：** `/` は従来どおりHTTP 200で移動体本部の画面を表示します。旧 `/api/display` もidoutai専用の別名として維持します。既存iPadのURL変更は不要です。更新後は一度ページを再読み込みしてください。

**内部管理：** `app.state.stores[room_id]` がそれぞれ独立したScheduleStoreを保持します。各Storeに全件取得成功時のsnapshot（予定・取得範囲・最終成功時刻）とerrorがあります。既存の全ページ取得成功時だけ置換する処理をそのまま利用しています。

各室に既存の取得スレッドを1つずつ持ち、原則60秒間隔で独立して実行します。GraphのHTTPセッションも室別です。ある室のタイムアウト・403・429・ページ取得失敗は、その室の警告と再試行だけに影響します。MSALインスタンス・トークンキャッシュ・3つの環境変数は1組だけ共用し、認証呼び出しはロックで直列化します。Secret失効など認証自体の共通障害は全室に影響し得ます。

### Windowsでの切替確認

1. 既存Uvicornの起動元PowerShellでCtrl+C。環境変数を設定済みの同じPowerShellを使います。Codex環境から実認証情報は読み取りません。
2. 以下で再起動します。8020を他プロセスが使用中の場合は二重起動せず、起動元を確認します。

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8020
```

3. PCで `/` と6室のURLを開き、会議室名、既知の実予定、予約者、非公開予定を照合します。各室の最終更新が約60秒＋取得時間で進むことを確認します。
4. API確認例（件名・Secretの出力は不要）：

```powershell
$pocRoomIds = @("idoutai", "bc-first", "bc-second", "ds-asagaya", "ds-tachikawa-1", "ds-tachikawa-2")
foreach ($pocRoomId in $pocRoomIds) {
    Invoke-RestMethod ("http://127.0.0.1:8020/api/room/" + $pocRoomId + "/status") |
        Select-Object room_id, room_name, status, warning, updated_text
}
```

5. `/room/not-configured` と `/api/room/not-configured/status` が404になることを確認します。
6. それぞれのiPadを該当URLで開き、無操作更新とホーム画面起動を確認します。既存の移動体本部iPadは `/` のままで使えます。

認証情報がCodex環境にないため、実Graphの6室同時接続は利用者側で確認してください。ローカルテストでは6室設定・正しいUPN選択・共有認証・キャッシュ分離・1室だけの初回／更新失敗・他室の正常応答・予約者・非公開・dummy・旧URL・未知IDを検証しています。

### 次に1室追加する手順

1. 管理者が同じアプリのRBACスコープへ追加室を含め、Application Calendars.Readの許可と対象外への制限を確認します。新しいアプリやSecretは不要です。
2. config.toml末尾に `[[rooms]]` を追加し、一意の `id`、`name`、`upn` を設定します。新しい値は管理者から提供されたものを使ってください。
3. 同じ環境変数を設定したPowerShellでサーバを再起動します。
4. `/room/<追加したid>` と `/api/room/<追加したid>/status` を確認し、iPadへ設定します。

通常はPython・JavaScript・HTMLの修正やポート追加は不要です。idoutaiは既存URLの接続先として維持してください。


## 12. 6会議室の臨時使用

`idoutai`、`bc-first`、`bc-second`、`ds-asagaya`、`ds-tachikawa-1`、`ds-tachikawa-2` の各 `/room/{room_id}` で使用します。`/` と旧 `/api/display` は引き続きidoutaiを参照します。Microsoft 365 / desknet'sには一切書き込みません。Application Calendars.Readと現在の認証方式をそのまま使用します。設定済みかつ `app/ad_hoc.py` の `ENABLED_ROOM_IDS` にある会議室だけを許可します。今後会議室を設定に追加するだけでは臨時使用は有効になりません。

### 操作と条件

- 実Graphモードで、取得済み・更新警告なし・現在空室の場合に「今から使用」を1回タップします。確認画面や時間指定はありません。
- 中央に「臨時使用中」「使用開始 HH:MM」「経過 N分」を表示します。現在・次・本日の正式予定と予約者はそのまま表示します。
- 「使用終了」で解除し、その時点の取得済み正式予定から再判定します。GraphをPOSTのたびに再取得する方式ではありません。
- 利用時間に上限はありません。正式予約の開始または利用者の終了操作で解除します。
- Graph失敗、初回未取得、古いデータ、設定エラー、保存エラー時は新規開始できません。dummyでは実Graphの空室を確認できないため開始できません。動作テストは模擬Graphで行います。
- 開始の連打は開始時刻を変えず、終了の連打も安全に処理します。サーバのロックで判定と状態変更を直列化し、画面側でも通信中は操作を無効にします。
- POSTの応答を受け取れなかった場合は、操作を自動再送せずGETで状態を確認します。サーバ側で開始済みの場合も次の取得で反映されます。

### 優先順位と正式予約

1. 状態未確認・データ取得不能
2. 正式予約の使用中
3. 臨時使用中
4. 空室

各会議室でGraphの更新失敗が分かった時点で「状態未確認」を優先します。「今から使用」はGraph正常・空室・開始許可ありの場合のみ表示し、通信失敗時にも非表示にします。臨時使用の記録自体は残り、通信復旧後に再判定します。記録が残っている間は「使用終了」で解除できます（iPadからサーバへの通信も失敗している間は操作不可）。他室の開始・終了・Graph障害はその部屋の臨時使用に影響しません。

正式予約との衝突はサーバの約1秒間隔の確認と、status/start/stop API処理時に調べます。ブラウザを閉じても、取得済み正式予約の開始時刻を迎えれば臨時使用を解除します。停止・再起動中に開始して既に終了した予約も、取得範囲内で確認できれば臨時使用を復活させません。取消・freeの予定は正式な使用中として扱いません。

画面の15秒ポーリングは継続します。臨時使用中に既知の次予約があれば、その開始時刻にも追加のGETを行います。サーバ時刻・通信・旧Safariのタイマー遅延により秒単位の遅れはあり得ます。新規／変更された正式予約を知る時刻は従来どおりGraphの約60秒取得とdesknet's同期に依存します。

### 専用API

```text
POST /api/room/{room_id}/ad-hoc/start
POST /api/room/{room_id}/ad-hoc/stop
```

画面はXMLHttpRequestで `X-Room-Action: 1` ヘッダーを付けます。ブラウザの別オリジンからのフォーム送信を防ぐため、このヘッダーなしは403とし、CORSは有効にしません。既存の社内LANアクセス制限は維持してください。

- 成功：HTTP 200、操作後の表示用JSON
- 開始条件不成立：HTTP 409、表示用JSONと `operation_error`
- 保存失敗：HTTP 503、表示用JSONと安全なエラー文言
- 許可対象外／存在しないID／未対応操作：HTTP 404

対象6室のstatus APIと旧 `/api/display` に次を追加しています。値は指定会議室の状態のみを返します。

- `ad_hoc_active`：記録があるか
- `ad_hoc_started_at`：UTCのISO日時、未使用ならnull
- `ad_hoc_elapsed_minutes`：サーバ基準の経過分
- `ad_hoc_can_start`：開始許可
- `ad_hoc_started_epoch_ms`：旧Safariの経過時間表示用数値
- `ad_hoc_next_reservation_ms`：既知の正式予約開始時の再取得用数値

`ad_hoc_active=true`でもGraph不明時の中央表示は「状態未確認」を優先します。経過時間はサーバ開始時刻とサーバ時刻を基に表示し、iPadでISO日時文字列を解析しません。

### 保存・再起動

保存先はプロジェクト内の `data/{room_id}-ad-hoc.json` です。6室で別々のファイル・ロック・状態を使用し、正式予約との衝突もその部屋の予定だけで判定します。既存の `data/idoutai-ad-hoc.json` は同じ形式・同じ場所で読み込むため移行作業は不要です。最初の操作時に作成し、内容は `active`（真偽値）と `started_at`（UTC日時／null）だけです。認証情報・個人情報・予定は保存しません。dataフォルダーのGit除外は維持しています。

一時ファイルへ書き込み、同じフォルダー内で置き換えて保存します。開始・終了は保存成功後に確定します。ただし正式予約による自動解除は、保存失敗でもメモリ上で直ちに解除し正式予約を優先します。保存障害中は警告し、新規開始を禁止します。

壊れた保存ファイルや未来の開始時刻を検出した場合も開始不可です。稼働アカウントのdataフォルダー書込権限・空き容量・PC時計を確認してください。ファイル破損の修復・削除は管理者がサーバ停止中に判断します。ファイルを削除すると臨時使用記録は失われます。

再起動後も記録は復元しますが、Graphの初回取得までは状態未確認／取得中を優先します。DBは不要です。**1プロセス・1ワーカー**を継続し、複数プロセスで同じ保存ファイルを共有しないでください。

現在のSYSTEMスケジュールタスクでの運用では、管理者PowerShellから既存タスクを停止し、8020の待受がなくなったことを確認してから同じタスクを開始します。起動方式・Secret登録・Graph認証の変更や再登録は不要です。

```powershell
Stop-ScheduledTask -TaskName 'Meeting Room Display'
Get-NetTCPConnection -LocalPort 8020 -State Listen -ErrorAction SilentlyContinue
# 待受がなくなったことを確認してから
Start-ScheduledTask -TaskName 'Meeting Room Display'
```

iPadは一度ページを再読み込みしてください。以降、ページ再読み込みだけで臨時使用が解除されることはありません。今回は稼働中のサーバをCodexから停止・再起動していません。

### 実機確認

6室それぞれで空室時の1タップ開始、経過時間、終了、連打、ページ再読み込み、サーバ再起動後の復元を確認してください。2室を同時に臨時使用し、一方の終了や正式予約開始で他方が解除されないこと、Graph障害のある部屋では開始ボタンが表示されないことも確認します。iOS 10.3.4で通常のSafariとホーム画面起動を確認してください。ES5相当の記述・XHRを維持し、fetch／Promise／async-awaitは導入していません。ボタンを追加した分、低い画面では下部一覧のスクロールが必要になる場合があります。実機の描画・タップは未確認です。

自動テストは実Graphに接続せず、一時保存ファイルと模擬Graphを使用しています。本番の臨時使用ファイルは変更しません。テスト項目には保存失敗・不正ファイル・複数スレッドの連打・ブラウザアクセスなしの自動解除・時間上限なしも含めています。

6室対応時の確認結果：既存を含むPythonテスト62件、JavaScript構文確認、6室のXHR/DOM模擬テストが成功しました。開始・終了・連打・再起動復元・室間分離・正式予約による対象室のみの解除・一室の保存ファイル破損・Graph異常を検証しています。Codexのサンドボックスでは既定の一時フォルダーでファイル置換が拒否されるため、PythonテストのTEMP/TMPをGit除外済みの `data/test-temp` に設定し、localhostの一時ポートでHTTPテストを実行しました。稼働中の8020番ポートは使用していません。
