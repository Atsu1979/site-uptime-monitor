# site-uptime-monitor

[![uptime](https://github.com/Atsu1979/site-uptime-monitor/actions/workflows/uptime.yml/badge.svg)](https://github.com/Atsu1979/site-uptime-monitor/actions/workflows/uptime.yml)
[![tests](https://github.com/Atsu1979/site-uptime-monitor/actions/workflows/tests.yml/badge.svg)](https://github.com/Atsu1979/site-uptime-monitor/actions/workflows/tests.yml)

旅比較（[tabi-hikaku.com](https://tabi-hikaku.com/)）と修繕ナビ（[shuzen-navi.com](https://shuzen-navi.com/)）、それぞれのAIチャットを、GitHub Actions から10分ごとに**外から**確かめる死活監視。

> **English summary** — An external uptime monitor for two Japanese content sites and their AI chat APIs, running on GitHub Actions every 10 minutes. Python standard library only. Checks content markers (not just HTTP 200), retries before declaring an outage, tells a Cloudflare challenge apart from a real outage, alerts only on state changes (Telegram + GitHub Issues), and writes a weekly heartbeat so GitHub's 60-day inactivity rule never disables the schedule.

現在の状態は [STATUS.md](STATUS.md)、これまでの障害は [status/incidents.md](status/incidents.md)。

## なぜ外から見るのか

サーバの中には点検の仕組みがいくつもある。ただ、サーバの中で動く監視は、サーバと一緒に止まる。サーバそのものが止まったときに知らせられるのは、サーバの外にいる監視だけだ。

## 何を見ているか

| 対象 | 正常の条件 |
|---|---|
| 旅比較 トップ | HTTP 200・本文50KB以上・「旅比較」を含む |
| 旅比較 記事（原爆ドーム・平和記念資料館） | HTTP 200・本文20KB以上・「原爆ドーム」を含む |
| 修繕ナビ トップ | HTTP 200・本文5KB以上・「修繕ナビ」を含む |
| 旅比較 AIチャット | `/health` が `{"ok": true}` |
| 修繕ナビ AIチャット | `/health` が `{"ok": true}` |
| 各ドメインのTLS証明書 | 残り14日以上 |

**「200が返る」だけでは足りない。** ビルドが途中で壊れて中身の無いページが配られても、200は返る。だから中身の目印と最低サイズも見る。対象と条件は [targets.json](targets.json) で変えられる。

## どう判定するか

- **3回試す**（すぐ・20秒後・さらに60秒後）。3回とも駄目なときだけ「障害」にする。一瞬の揺れでは鳴らさない。
- **Cloudflare の確認画面に止められたとき**（`cf-mitigated: challenge`）は「障害」ではなく「監視側が確認できない」として別に扱う。サイトは生きているのに「落ちた」と言わないため。
- **1回の取得に全体の時間上限を掛ける。** `urlopen` の `timeout` は1回の受信待ちの上限でしかなく、相手が少しずつ返し続けると終わらないことがある。

## いつ知らせるか

- **状態が変わったときだけ**（正常→障害、障害→復旧）。落ちている間じゅう10分ごとに鳴らすことはしない。
- 知らせる先は **Telegram** と **GitHub Issue**。障害ごとに Issue を開き、復旧したら止まっていた時間を書き込んで閉じる。Issue の一覧が、そのまま障害の記録になる。
- **週1回、稼働率の報告を送る。** 便りが無いのは良い知らせ、とは限らない。監視そのものが止まっていても便りは無い。だから「動いている証拠」を週1回出す。

## GitHub の60日ルールへの備え

公開リポジトリでは、60日間リポジトリに動きが無いと、定時実行が自動で止まる（[GitHub Docs](https://docs.github.com/actions/managing-workflow-runs/disabling-and-enabling-a-workflow)）。正常な日が続くと状態は書き換わらないので、週次の報告を [status/weekly.md](status/weekly.md) に書き足して動きを作る。

## ファイル

| ファイル | 役割 |
|---|---|
| `targets.json` | 見る対象と条件 |
| `monitor/check.py` | 取得と判定（再試行・Cloudflare の見分け・時間上限） |
| `monitor/state.py` | 状態の記録、変化の検出、表の生成 |
| `monitor/notify.py` | Telegram と GitHub Issue |
| `monitor/main.py` | 1回分の実行 |
| `STATUS.md` | 今の状態（変化したときだけ更新） |
| `status/incidents.md` | 障害の記録 |
| `status/weekly.md` | 週次の報告 |

## 設定

リポジトリの **Settings → Secrets and variables → Actions** に2つ登録する。

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

登録しなくても監視は動き、GitHub Issue だけで知らせる。鍵はコードにもログにも出さない。

登録できたかは **Actions → uptime → Run workflow** で `test_notify` にチェックを入れて実行すると確かめられる。Telegram にテストが1通届く。

## 手元で試す

```bash
python -m monitor.main --dry-run          # 見るだけ。通知も書き込みもしない
python -m unittest discover -s tests -v   # テスト
```

外部ライブラリは使っていない。Python 3.10 以上の標準ライブラリだけで動く。

## 限界

- GitHub が混んでいると、定時実行が遅れることがある（[GitHub Docs](https://docs.github.com/actions/using-workflows/events-that-trigger-workflows)）。数分単位の検知が要る用途には向かない。
- 見ているのは代表のURLだけ。全ページの点検や、記事の中身・料金の正しさは、サーバ側の仕組みが受け持つ。

## ライセンス

MIT
