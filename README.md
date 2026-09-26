# Chatwork Mention Alarm for macOS

指定した時間帯にChatworkで自分宛てのToまたは返信を受けると、確認するまでMacの警告音を繰り返す軽量な常駐ツールです。

## 特徴

- Chatwork公式APIを読み取り専用で利用
- 自分宛てのTo（`[To:account_id]`）と返信（`[rp aid=account_id ...]`）を検知
- 複数ルームと日付をまたぐ時間帯に対応
- 「停止」ダイアログを押すまで警告音を反復
- macOSのLaunchAgentとしてログイン時に自動起動
- APIトークンはmacOSキーチェーンに保存
- メッセージ本文とAPIトークンをログに残さない
- 外部Pythonパッケージ不要

## 必要環境

- macOS
- Python 3
- Chatwork APIトークン
- 監視中にMacが起動し、ネットワークへ接続されていること

Macがスリープしている間は監視できません。画面の消灯やロックは問題ありません。

## インストール

Chatworkの利用者名メニューから「サービス連携」→「APIトークン」を開き、トークンをコピーします。その後、ターミナルで以下を実行します。

```sh
git clone https://github.com/s-kart1s/chatwork-mention-alarm.git
cd chatwork-mention-alarm
./install.sh
```

インストーラーが以下を尋ねます。

- 監視するルームID（URLの `rid` に続く数字）
- 監視の開始・終了時刻
- 確認間隔
- Chatwork APIトークン（macOSキーチェーンの入力画面には表示されません）

初回起動時は現在の最新メッセージを基準値として保存するため、過去のメンションでは鳴りません。

## 動作確認

```sh
./test-alarm.sh
```

「Chatwork 緊急メンション」ダイアログの「停止」を押すまで警告音が繰り返されれば成功です。

常駐状態と直近のログは以下で確認できます。

```sh
./status.sh
```

## 設定

設定ファイルは `~/.config/chatwork-mention-alarm/config.json` です。

```json
{
  "room_ids": [123456789],
  "start_hour": 4,
  "end_hour": 9,
  "poll_seconds": 30,
  "sound_path": "/System/Library/Sounds/Sosumi.aiff",
  "sound_volume": 2.0
}
```

`start_hour` と `end_hour` が同じ場合は終日監視します。`22`〜`6`のように日付をまたぐ指定も可能です。変更後は以下で再起動します。

アラーム対象かどうかは、取得時刻ではなくメッセージの送信時刻で判定します。設定可能なルーム数と確認間隔はChatwork APIの制限を超えない範囲に制限されます。

```sh
launchctl kickstart -k gui/$(id -u)/io.github.skartis.chatwork-mention-alarm
```

## 保存場所

- 実行ファイル: `~/Library/Application Support/chatwork-mention-alarm`
- 設定: `~/.config/chatwork-mention-alarm`
- 状態: `~/Library/Application Support/chatwork-mention-alarm/state.json`
- ログ: `~/Library/Logs/chatwork-mention-alarm`
- APIトークン: macOSキーチェーン

再インストール時はキーチェーンに保存済みのAPIトークンを再利用します。トークンを変更する場合は、先に「キーチェーンアクセス」で`io.github.skartis.chatwork-mention-alarm`を削除してから再実行してください。

## アンインストール

```sh
./uninstall.sh
```

LaunchAgentと常駐用実行ファイルをゴミ箱へ移動します。再導入できるよう、設定・ログ・キーチェーンのトークンは残します。

## セキュリティ

- APIトークンを設定ファイルや環境変数、ログへ書き込みません。
- メッセージ本文を永続化しません。
- ログには検知時のルームID、送信者名、メッセージIDのみを記録します。
- APIアクセスはプロフィール、ルーム情報、メッセージ取得のみです。投稿・編集・削除は行いません。

不具合報告へログを添付する場合は、送信者名やルームIDなどを必要に応じて伏せてください。

## 開発

```sh
python3 -m unittest -v
python3 -m py_compile chatwork_alarm.py
bash -n install.sh status.sh test-alarm.sh uninstall.sh
```

## License

[MIT](LICENSE)
