# UroCat Paper

**長期留置尿道カテーテル研究に特化した、個人用 PubMed 新着論文アプリです。**

毎日 PubMed を検索し、尿道/尿路カテーテルとの直接性、閉塞・encrustation・struvite・urease/Proteus、biofilm/microbiome、長期管理、新素材・コーティングとの関連性を採点します。重要度 ★4–5 の新着論文だけを Slack に通知し、全論文はスマホ対応 PWA に保存します。

## できること

- PubMed を毎日自動検索
- `urinary / urethral / Foley / indwelling urinary catheter` を検索の中心に固定
- vascular / central venous / dialysis catheter などを強く減点
- 一般的な CAUTI incidence / surveillance だけの論文を下位化
- 閉塞・結晶、Biofilm・微生物叢、管理・予防、新素材・コーティングに分類
- ★1–5 の関連度
- OpenAI API を設定した場合、日本語タイトル・1行要約・対象・研究デザイン・関連性を生成
- ★4–5 の新着のみ Slack に Block Kit 形式で通知
- GitHub Pages でスマホアプリ風に閲覧
- PWAとしてホーム画面に追加
- 既読、⭐お気に入り、👍関連あり、👎関連なしを端末内に保存
- フィードバックJSONを書き出し可能

## 1. GitHub にアップロード

このフォルダの**中身をすべて**新しい GitHub repository の `main` branch に置きます。

推奨 repository 名：`urocat-paper`

## 2. GitHub Pages を有効化

Repository → **Settings → Pages → Build and deployment → Source: GitHub Actions** を選択します。

`pages.yml` が自動でサイトを公開します。

## 3. GitHub Secrets を登録

Repository → **Settings → Secrets and variables → Actions → New repository secret**

必須：

- `NCBI_EMAIL`：NCBI E-utilities 利用時の連絡先メール

Slack通知を使う場合：

- `SLACK_WEBHOOK_URL`：Slack Incoming Webhook URL

AI日本語要約を使う場合：

- `OPENAI_API_KEY`：OpenAI API key

任意：

- `NCBI_API_KEY`：NCBI API key

OpenAIモデルを変更したい場合は **Variables → New repository variable** で：

- `OPENAI_MODEL`：初期値は `gpt-5.6`

## 4. 初回実行

Repository → **Actions → Daily PubMed update → Run workflow**

成功すると `data/papers.json` がPubMed実データに更新され、★4–5 の新着があればSlackへ通知されます。

その後は毎日 **07:07 Asia/Tokyo** に自動実行します。

## 5. Slack Incoming Webhook

Slack App を作成し、Incoming Webhooks を有効化して、通知先チャンネルに Webhook を追加します。取得した URL を GitHub Secret `SLACK_WEBHOOK_URL` に登録します。

Webhook URL は公開リポジトリのコードに直接書かないでください。

## 検索設計

検索は4本に分けています。

1. **閉塞・結晶・ストルバイト**
2. **Biofilm・微生物叢**
3. **管理・予防**
4. **新素材・コーティング**

技術開発論文は in vitro / animal でも拾う一方、臨床系は尿道/尿路カテーテルとの直接性を強く要求します。

## 関連度の考え方

高得点：

- urinary / urethral / Foley / indwelling urinary catheter
- blockage / obstruction / encrustation / crystalline biofilm
- struvite / urease / Proteus mirabilis
- biofilm / microbiome / urobiome
- coating / hydrogel / antibiofilm / antifouling / sensor

低得点：

- central venous / central line
- vascular catheter
- dialysis catheter
- PICC
- cardiac / epidural / intrathecal catheter
- blockage/biofilm/long-term catheterとの接点がない一般的CAUTI surveillance

検索と採点は `scripts/fetch_papers.py` にまとまっています。

## ファイル構成

```text
urocat-paper/
├─ app/
│  ├─ index.html
│  ├─ styles.css
│  ├─ app.js
│  ├─ manifest.webmanifest
│  ├─ service-worker.js
│  └─ icon.svg
├─ data/
│  ├─ papers.json
│  └─ state.json
├─ scripts/
│  └─ fetch_papers.py
├─ .github/workflows/
│  ├─ daily.yml
│  └─ pages.yml
├─ requirements.txt
└─ README.md
```

## スマホ通知について

V1では **SlackアプリのPush通知をスマホ通知として使用**します。これは独自Pushサーバーを持たずに安定して通知できるためです。UroCat Paper自体はPWAとしてホーム画面に追加できます。

将来は Web Push / Firebase Cloud Messaging を追加できますが、最初は Slack 通知の方が保守性が高い構成です。
