# kanshi_camera

Raspberry Pi 向け USB カメラ監視システム。uStreamer + Python 製の軽量コンテナで、定期スナップショットのアップロード・ローカルギャラリー表示・MJPEG ストリーム配信を提供します。

## アーキテクチャ

`supervisord` が 3 プロセスを並列管理するシングルコンテナ構成です。

```
┌─────────────────────────────────────────────┐
│  Container (podman)                         │
│                                             │
│  uStreamer :8080  ──┐                       │
│                     ├── capture_uploader.py │
│  web_server :8888  ─┘        │              │
│                           HTTPS POST        │
└───────────────────────────────┼─────────────┘
                                ▼
                        外部アップロード先
```

| プロセス | ポート | 役割 |
|---------|--------|------|
| uStreamer | 8080 | `/dev/video0` の MJPEG ストリーム配信 |
| capture_uploader.py | — | 定期スナップショット取得 → HTTPS アップロード |
| web_server.py | 8888 | ローカルギャラリー・ステータス API |

永続データは podman named volume (`kanshi_retry`, `kanshi_gallery`, `kanshi_state`) で管理します。

## 必要環境

**ハードウェア**
- Raspberry Pi（armv7 以上）
- USB カメラ（V4L2 対応）

**ソフトウェア（ラズパイ側）**
- podman
- `podman_start` コマンド（Raspberry Pi OS の atmark コンテナ管理）

**ビルド環境（開発機）**
- Docker（`--platform linux/arm/v7` 対応）
- WSL2 または Linux

## セットアップ

### 1. 環境変数ファイルの準備

ラズパイ上で設定ファイルを作成します。

```bash
sudo mkdir -p /etc/kanshi_camera
sudo cp .env.example /etc/kanshi_camera/.env
sudo chmod 600 /etc/kanshi_camera/.env
sudo vi /etc/kanshi_camera/.env   # UPLOAD_URL と API_KEY を設定
```

**必須項目：**

| 変数 | 説明 |
|------|------|
| `UPLOAD_URL` | スナップショット送信先の HTTPS URL |
| `API_KEY` | Bearer トークン（Authorization ヘッダーで送信） |

**任意項目：**

| 変数 | デフォルト | 説明 |
|------|-----------|------|
| `DEVICE_ID` | `kanshi-001` | デバイス識別子 |
| `CAPTURE_INTERVAL_MIN` | `1` | キャプチャ間隔（分）。Web UI からも変更可 |
| `MAX_GALLERY_IMAGES` | `50000` | ギャラリー保持枚数の上限 |
| `MAX_GALLERY_DAYS` | `30` | ギャラリー保持日数 |
| `WEB_PORT` | `8888` | Web サーバーのポート番号 |
| `USTREAMER_EXTERNAL_HOST` | — | ブラウザ向けストリーム IP（空の場合は Host ヘッダーから自動取得） |
| `STREAM_USER` / `STREAM_PASSWORD` | — | ストリームへの Basic 認証（任意） |

### 2. コンテナイメージのビルド（開発機）

```bash
# armv7 向けクロスビルド。docker グループ未参加なら sg docker -c "..." で包む
docker build --platform linux/arm/v7 -t kanshi_camera:latest -f Containerfile .
docker save kanshi_camera:latest | gzip > kanshi_camera.tar.gz
scp kanshi_camera.tar.gz raspberrypi:/var/tmp/
```

### 3. ラズパイへのデプロイ

```bash
# イメージのロード
ssh raspberrypi 'gunzip -c /var/tmp/kanshi_camera.tar.gz | podman load'

# コンテナ設定のコピー（初回のみ）
scp deploy/kanshi_camera.conf raspberrypi:/etc/atmark/containers/
ssh raspberrypi 'persist_file /etc/atmark/containers/kanshi_camera.conf'

# コンテナ起動
ssh raspberrypi 'podman_start kanshi_camera'
```

## Web UI

`http://<ラズパイのIP>:8888` にアクセスします。

- **ギャラリー** — 保存されたスナップショットの一覧（日付フィルタ付き）
- **ストリーム** — リアルタイム MJPEG ストリームへのリンク
- **設定** — キャプチャ間隔のリアルタイム変更

## ヘルスチェック

コンテナ内で自動実行されるヘルスチェックスクリプト：

```bash
python3 /app/src/health.py
```

以下の項目を確認し、JSON で結果を標準出力します：
- uStreamer の疎通確認
- ディスク空き容量（100MB 未満で警告）
- リトライキューの滞留数

非ゼロ終了で `unhealthy` 判定されます。

## 開発・テスト

```bash
# 全テスト実行
python3 -m pytest tests/ -v

# 単一テストファイル
python3 -m pytest tests/test_uploader.py -v
```

テストは `src/` を直接 import するため、`pytest.ini` や `conftest.py` は不要です。

## プロジェクト構造

```
kanshi_camera/
├── Containerfile           # マルチステージビルド（uStreamer + Python）
├── supervisord.conf        # 3プロセス管理設定
├── config/
│   └── ustreamer.conf      # uStreamer 起動オプション
├── deploy/
│   ├── kanshi_camera.conf  # podman_start 設定
│   └── 99-usb-camera.rules # udev ルール（USB カメラ認証）
├── src/
│   ├── capture_uploader.py # スナップショット取得・アップロードループ
│   ├── web_server.py       # ローカル Web サーバー
│   ├── gallery.py          # ローカル JPEG ストア管理
│   ├── retry_queue.py      # 再送キュー（ファイルベース）
│   ├── config.py           # 環境変数一元管理・バリデーション
│   ├── health.py           # ヘルスチェックスクリプト
│   ├── uploader.py         # HTTPS アップロード
│   └── static/index.html   # Web UI
└── tests/                  # pytest テストスイート
```

## セキュリティ上の注意

- `UPLOAD_URL` は HTTPS 必須（`http://` を設定すると起動時にエラー終了）
- API キーは `Authorization: Bearer` ヘッダーで送信し、URL やログには出力しません
- Web UI（ポート 8888）はプライベート LAN 専用です。インターネット公開時はファイアウォールや認証プロキシを設置してください
- `.env` ファイルのパーミッションは `600` を推奨します
