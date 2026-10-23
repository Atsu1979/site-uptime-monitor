# site-uptime-monitor

本番サイトの**外形監視**。HTTP 200 だけでなく、ページ内の期待テキスト・最小本文サイズ・
TLS証明書の残り日数（既定で14日前に警告）を見る。3回リトライし、Cloudflare のチャレンジ画面は
「落ちている」ではなく「監視側が確認できない」として扱う。

- **通知**: 状態が変わったとき（ダウン/復旧）だけ Telegram・汎用 Webhook・GitHub Issues に出す。週次レポートあり。
- **稼働率**: 対象ごとに直近 N 回（既定 144 回＝10分ごとで24時間）の判定を残し、通知と週次レポートに稼働率を載せる。
- **実行**: GitHub Actions で10分ごと（サーバ不要・Python 標準ライブラリのみ・3.10+）。
- **状態**: `STATUS.md` と `status/` にジェネリックなラベル（Site A/B 等）で記録。

## 監視対象について
本番 Web サイトとその AI ヘルスエンドポイントを監視しているが、**具体的な URL やサイト名は
公開していない**（`targets.json` は Secret から実行時に展開。公開リポには `targets.example.json`
のサンプルのみ）。ポートフォリオとして実例が必要な場合は個別に共有する。

## セットアップ
1. リポジトリ Secret に設定:
   - `TARGETS_JSON`（実際の監視対象 JSON。`targets.example.json` と同じ形）
   - 通知先（どれも任意。設定したものにだけ送る）: `TELEGRAM_BOT_TOKEN` と `TELEGRAM_CHAT_ID`、`WEBHOOK_URL`
2. 必要ならリポジトリ変数（Settings → Variables）で `CERT_WARN_DAYS`・`HISTORY_SIZE`・`NOTIFY_TIMEOUT` を変える。
3. `targets.example.json` を見て対象の形を確認。
4. ローカル確認: `python -m monitor.main --dry-run --targets targets.example.json`
5. 通知の確認: Actions の `uptime` を手動実行し `test_notify` に印を付ける（設定済みの通知先へ1通だけ送る）。

## 使い方

```
python -m monitor.main                        本番（通知し、status/ と STATUS.md を書き換える）
python -m monitor.main --dry-run              見るだけ（通知しない・書き換えない）
python -m monitor.main --test-notify          設定済みの通知先にテストを1通送って終わる
```

| 引数 | 既定 | 意味 |
|---|---|---|
| `--targets` | `targets.json` | 監視対象の JSON |
| `--status-dir` | `status` | `state.json`・`incidents.md`・`weekly.md` の置き場 |
| `--status-md` | `STATUS.md` | 現在の状態の表の書き出し先 |
| `--dry-run` | — | 通知も書き込みもしない |
| `--test-notify` | — | テスト送信だけして終わる（どれか1つ届けば終了コード 0） |

## 監視対象の書き方（`targets.json`）

| 鍵 | 必須 | 意味 |
|---|---|---|
| `id` | ○ | 状態の記録に使う識別子（変えると別の対象として扱われる） |
| `url` | ○ | 見る URL。`https://` ならそのホストの証明書も見る |
| `label` | | 公開される表示名（`STATUS.md` などに載る）。省略時は `id` |
| `name` | | 通知（非公開）にだけ使う実名。省略時は `label` |
| `expect_status` | | 期待する HTTP ステータス（既定 200）。リダイレクトは自動で辿り、最後の応答で判定する |
| `must_contain` | | 本文に含まれているべき文字列 |
| `min_bytes` | | 本文の最小バイト数 |
| `json_true` | | 本文を JSON として読み、この鍵が `true` であること（ヘルスエンドポイント向け） |
| `timeout` | | 1回の受信の待ち時間（秒、既定 15）。全体ではその2倍で打ち切る |

## 環境変数

| 変数 | 既定 | 意味 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | 未設定 | Telegram ボットのトークン。`TELEGRAM_CHAT_ID` と両方あるときだけ送る |
| `TELEGRAM_CHAT_ID` | 未設定 | 送り先のチャット ID |
| `WEBHOOK_URL` | 未設定 | 汎用 Webhook（http/https）。未設定なら送らない。本文は下記 |
| `NOTIFY_TIMEOUT` | `10` | 通知1件あたりの待ち時間（秒、1〜60）。Telegram・Webhook・GitHub Issue 共通 |
| `CERT_WARN_DAYS` | `14` | 証明書の残りがこの日数未満になったら「期限が近い」（0〜365） |
| `HISTORY_SIZE` | `144` | 稼働率の計算に残す直近の判定の回数（0〜10000。0 で履歴を取らない） |
| `GITHUB_TOKEN` | 未設定 | 障害ごとに Issue を開け閉めする（Actions では自動で渡る） |
| `GITHUB_REPOSITORY` | 未設定 | Issue を作るリポジトリ（`owner/repo`）。User-Agent にも使う。Actions では自動 |

数値の変数は、空・読めない値なら既定値、範囲外なら端の値に寄せる（どちらもログに1行出す）。
`uptime.yml` では `WEBHOOK_URL` を Secret から、`CERT_WARN_DAYS`・`HISTORY_SIZE`・`NOTIFY_TIMEOUT` を
リポジトリ変数から渡している（未登録なら空＝既定値）。

### Webhook の本文

`POST`、`Content-Type: application/json`。`text` は Slack 互換の人向けの文、`event` は機械向け。

```json
{
  "text": "🔴 Site A が落ちています\n理由: HTTP 502（期待は 200）\n…",
  "event": {
    "type": "down",
    "id": "t1", "name": "Site A (top)", "old": "up", "new": "down",
    "reason": "HTTP 502（期待は 200）", "at": "2026-10-19T02:35:00Z", "started": "2026-10-18T20:00:00Z",
    "availability_pct": 99.31, "availability_checks": 144
  }
}
```

`type` は `down`・`recovered`（up 以外から up に戻った）・`challenged`・`expiring`・`expired`・`weekly`・`test`。
`text` には通知用に実 URL が入るが、`event` には入れない。

### 通知の失敗

通知先が落ちていても監視の実行は止めない。例外はすべて通知の中で止め、例外の種類だけログに出す
（本文はトークンや URL を含みうるので出さない）。各通知は `NOTIFY_TIMEOUT` 秒で打ち切る。

## 状態と稼働率

- `status/state.json` に対象ごとの現在の状態・障害の記録（最大500件）・直近の判定の履歴を置く。
- 履歴は1回1文字（`u`=正常, `d`=障害, `c`=確認できない）で、`HISTORY_SIZE` 回ぶんだけ残す。時刻は持たない。
  `c`（Cloudflare に止められた）は稼働率の分母から外す。
- 全部正常のあいだは履歴の文字列が変わらないので `state.json` も変わらず、コミットされない。
  障害があると、それが履歴から押し出されるまで（既定で24時間）は毎回 `state.json` が変わり、10分ごとにコミットされる。
  導入直後も、履歴が `HISTORY_SIZE` 回ぶん溜まるまでは同じく毎回コミットされる。
- 履歴の無い古い `state.json` もそのまま読める（足りない鍵は既定値で埋める）。監視対象から外した `id` の履歴は消す。
- 証明書: 期限切れの証明書は検証で弾かれて残り日数が取れないので、検証エラーの種類で「期限切れ」を見分ける。
  それ以外の理由で証明書を見られなかったときは、前回の判定のまま変えない（同じホストの HTTP 監視が拾う）。

## テスト
CI（`.github/workflows/tests.yml`）と同じコマンド。標準ライブラリだけで、外部のネットワークには出ない
（手元に小さな HTTP サーバを立てるか、通信部分を差し替える）。

```
python -m unittest discover -s tests -v
```

## 制限
- GitHub のスケジュールは混雑時に遅延することがある。
- 証明書チェックは 443/TLS 前提。Cloudflare チャレンジは「確認不可」。
- リダイレクトは常に辿るので、`expect_status` に 3xx を指定しても一致しない。
- 稼働率は「直近 N 回の判定」の割合で、時間ではない（実行が遅れた回も1回と数える）。
