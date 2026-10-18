# site-uptime-monitor

本番サイトの**外形監視**。HTTP 200 だけでなく、ページ内の期待テキスト・最小本文サイズ・
TLS証明書の残り日数（14日前に警告）を見る。3回リトライし、Cloudflare のチャレンジ画面は
「落ちている」ではなく「監視側が確認できない」として扱う。

- **通知**: 状態が変わったとき（ダウン/復旧）だけ Telegram と GitHub Issues に出す。週次レポートあり。
- **実行**: GitHub Actions で10分ごと（サーバ不要・Python 標準ライブラリのみ・3.10+）。
- **状態**: `STATUS.md` と `status/` にジェネリックなラベル（Site A/B 等）で記録。

## 監視対象について
本番 Web サイトとその AI ヘルスエンドポイントを監視しているが、**具体的な URL やサイト名は
公開していない**（`targets.json` は Secret から実行時に展開。公開リポには `targets.example.json`
のサンプルのみ）。ポートフォリオとして実例が必要な場合は個別に共有する。

## セットアップ
1. リポジトリ Secret に設定:
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   - `TARGETS_JSON`（実際の監視対象 JSON。`targets.example.json` と同じ形）
2. `targets.example.json` を見て対象の形を確認。
3. ローカル確認: `python -m monitor.main --dry-run --targets targets.example.json`

## テスト
`python -m pytest`（標準ライブラリのみ）。

## 制限
- GitHub のスケジュールは混雑時に遅延することがある。
- 証明書チェックは 443/TLS 前提。Cloudflare チャレンジは「確認不可」。
