# 最短セットアップ

1. GitHubで新規repository `urocat-paper` を作成。
2. このフォルダ内のファイルをすべてアップロード。
3. `Settings → Pages → Source` を **GitHub Actions** にする。
4. `Settings → Secrets and variables → Actions` で `NCBI_EMAIL` を登録。
5. Slack通知を使うなら `SLACK_WEBHOOK_URL` を登録。
6. AI要約を使うなら `OPENAI_API_KEY` を登録。
7. `Actions → Daily PubMed update → Run workflow` を押す。
8. `Actions → Deploy UroCat Paper` が成功したら、Pages URLをスマホで開く。
9. ブラウザの「ホーム画面に追加」でアプリ化。
10. Slackアプリのスマホ通知をONにする。

初回は `data/papers.json` にデモ論文が1報入っています。Daily PubMed updateを実行すると実データに更新されます。
