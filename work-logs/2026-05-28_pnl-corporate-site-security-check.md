# pnl.co.jp 簡易セキュリティ診断ログ

- **日付：** 2026-05-28
- **対象：** https://pnl.co.jp/（コーポレートサイト・メール設定）
- **実施者：** 佐藤 雄太
- **手法：** 公開情報・外部ツールによる受動的外形チェックのみ（能動的な脆弱性診断は実施せず）
- **ステータス：** 対応済み・クローズ

## 使用ツール（外部・全て無料）

- Security Headers（securityheaders.com）
- Qualys SSL Labs（ssllabs.com）
- MXToolbox SuperTool（DMARC / SPF / MX Lookup）

## 主な発見事項

| # | 項目 | 当時の状態 | 重大度 |
|---|---|---|---|
| 1 | SPF | `v=spf1 include:_spf.heteml.jp ~all` — Google Workspace 送信を許可していない | 🔴 高 |
| 2 | DMARC | レコード未設定 | 🔴 高 |
| 3 | DKIM | 未確認（Google Admin 側） | 🟡 中 |
| 4 | HTTPセキュリティヘッダ | F評価（CSP / HSTS / X-Frame-Options / X-Content-Type-Options / Referrer-Policy / Permissions-Policy 全欠落） | 🟡 中 |
| 5 | TLS（SSL Labs） | B評価（Cloudflare経由なのに最小TLSバージョン低めと推定） | 🟡 軽 |
| 6 | サイト構成 | HTTPS自動転送あり・Cloudflare配信・MXはGoogle Workspace | 🟢 良好 |

## 確認できた基盤情報

- DNS / CDN：Cloudflare
- メール受信：Google Workspace（ASPMX.L.GOOGLE.COM）
- 旧送信基盤の痕跡：heteml（GMOペパボ）が SPF に残存

## 推奨した対応（参考記録）

1. SPF を Google Workspace 含む形に修正（heteml 併用なら併記）
2. DMARC を `p=none`（監視モード）から導入し、2〜4週後に段階的に厳格化
3. Google Workspace 管理コンソールで DKIM 有効化
4. Cloudflare Transform Rules でセキュリティヘッダを付与（HSTS / X-Content-Type-Options / X-Frame-Options / Referrer-Policy）
5. Cloudflare → SSL/TLS → Edge Certificates で Minimum TLS Version を 1.2 以上に

## 成果物

- 社内提出用レポート（技術編・別紙の平易説明編）をチャット上で生成
- 本ログは作業ブランチ `claude/dreamy-bell-zxdyv` のみに保持（main にはマージしない）

## 備考

本格的な脆弱性診断（SQLi / XSS 等の能動的検査）は今回スコープ外。動的機能の拡張時には専門ベンダー診断を別途検討する。
