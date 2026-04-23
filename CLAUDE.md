# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Raspberry Pi 向けの USB カメラ監視システム。uStreamer + Python 製アップロード/ギャラリーサービスを 1 コンテナ (Containerfile) にまとめ、Raspberry Pi OS 上の podman で常駐させる構成。`tasks/lessons.md` と `tasks/todo.md` に現場で得た具体的なハマりどころが大量に蓄積されているため、関連作業の前に必ず参照すること。

## Architecture

3 プロセスを `supervisord` が並列起動する（`supervisord.conf`）。

1. **uStreamer** (`:8080`) — `/dev/video0` の MJPEG ストリーム配信 (`config/ustreamer.conf` のオプションで起動)。
2. **capture_uploader.py** — 定期ループ。`USTREAMER_HOST:PORT/?action=snapshot` から JPEG を取得 → `Gallery` に保存 → `uploader.upload_image()` で HTTPS POST → 失敗時は `RetryQueue` に退避、次ループで再送。
3. **web_server.py** (`:8888`) — `http.server` ベースの軽量サーバー。`/api/images`, `/api/status`, `/api/dates`, `/gallery/<file>`, `/stream` (uStreamer へ 302) を提供し、`src/static/index.html` を配信。

共通モジュール:

- `src/config.py` — 環境変数一元管理。`validate()` で必須項目 (`UPLOAD_URL`, `API_KEY`) と HTTPS 強制、`CAPTURE_INTERVAL_SEC >= 10` をチェック。不正時は `sys.exit(1)`。
- `src/gallery.py` — `/var/lib/kanshi/gallery` を JPEG ストアとして管理。`MAX_GALLERY_IMAGES` 超過時に古いファイルを evict。`get_image_path` / `save` でディレクトリトラバーサルを拒否。ファイル名は `YYYYMMDD_HHMMSS.jpg`。
- `src/retry_queue.py` — `/var/lib/kanshi/retry` のファイルキュー。保存前に 100MB の空きディスクチェック、`MAX_RETRY_FILES` で FIFO evict。
- `src/health.py` — ラズパイ側 healthcheck で `python3 /app/src/health.py` として実行される。uStreamer 疎通 / ディスク空き / retry キュー滞留を JSON で stdout に出し、非ゼロ exit で unhealthy。

永続データは podman named volume (`kanshi_retry`, `kanshi_gallery`) にマウント。`.env` は `/etc/kanshi_camera/.env` をコンテナにリードオンリーマウントする (`deploy/kanshi_camera.conf`)。

## Development Commands

テスト実行 (リポジトリルートから):

```bash
# 全テスト (test_config / test_uploader / test_retry_queue / test_gallery / test_web_server)
python3 -m pytest tests/ -v

# 単一ファイル / 単一テスト
python3 -m pytest tests/test_uploader.py -v
python3 -m pytest tests/test_gallery.py::TestSave -v
```

テストは `sys.path.insert(0, ".../src")` で `src/` を import path に追加する方式。`pytest.ini` や `conftest.py` は無いので、IDE やツール経由では PYTHONPATH 調整が必要な場合あり。

コンテナビルド / 配備 (WSL2 → Raspberry Pi):

```bash
# armv7 向けクロスビルド。docker グループ未参加なら sg docker -c "..." で包む
docker build --platform linux/arm/v7 -t kanshi_camera:latest -f Containerfile .
docker save kanshi_camera:latest | gzip > kanshi_camera.tar.gz
scp kanshi_camera.tar.gz raspberrypi:/var/tmp/
# ラズパイ側
ssh raspberrypi 'gunzip -c /var/tmp/kanshi_camera.tar.gz | podman load'
ssh raspberrypi 'podman_start kanshi_camera'
```

## Project-Specific Gotchas

**必ず `tasks/lessons.md` を先に読むこと**。以下は特に頻出する落とし穴:

- **Python 3.9 互換**: ランタイムは Debian bullseye の Python 3.9。`bytes | None` / `list[str]` 等の PEP 604/585 構文は使えない。新規モジュールには必ず `from __future__ import annotations` を付けること。
- **ラズパイ OS の podman_start 設定書式**: `deploy/kanshi_camera.conf` は標準 compose や systemd 形式ではなく `add_ports` / `add_args` / `add_volumes` / `set_image` / `set_healthcheck` の独自書式。healthcheck の interval は `"1 min"` 形式（`60s` は不可）。
- **video グループ GID 不一致**: ホスト video GID=27、コンテナ内 video GID=44。`/dev/video0` アクセスには `add_args --group-add 27` が必須。`--group-add keep-groups` は他の `--group-add` と併用不可。
- **USB カメラ (Ricoh 05ca:18fe)**: 接続直後は未認証 (`authorized=0`) で `suspend` 状態になる。`deploy/99-usb-camera.rules` で `ATTR{authorized}="1"` を設定し、power/control は起動スクリプト (`/etc/local.d/usb-camera-power.start` など) 側で持続的に `on` にする。udev の `RUN` でやるとタイミングで落ちる。
- **uStreamer 引数**: このカメラは `--desired-fps` 非対応。`--device-timeout 10 --persistent --format MJPEG` を揃えないと `Device select() timeout` が頻発する (`config/ustreamer.conf` 参照)。
- **No Signal 判定**: カメラが給電されていない時 uStreamer は約 9311 バイトの「No Signal」JPEG を返す。実フレームは 12KB 前後。健全性判断に使えるが空でないので `capture_uploader.capture_snapshot` の 0 バイトチェックだけでは検知できない。
- **podman ストレージ**: graphroot がデフォルトで `/run` (tmpfs, 約 100MB) のため `podman load` が容量不足で失敗する。`/var/app/volumes/containers/storage` 等 eMMC 側に切り替える。
- **ファイル永続化**: ラズパイ OS は overlayfs のため、`/etc` 配下や起動スクリプトを変更したら `persist_file <path>` を呼ばないと再起動で消える。
- **セキュリティ境界**: `upload_url` は `https://` 強制。`Authorization: Bearer` でキー送信。`API_KEY` を URL やログ本文に出さないこと (`uploader.py` は既に準拠)。`web_server` の `/gallery/` とファイル保存系はトラバーサル検証済み、この防御を崩さない。

## Workflow Reminders

- 非自明な変更は先に `tasks/todo.md` を更新し、完了後に review 節を追記する運用。
- ユーザーからの指摘・挙動のズレが出たら `tasks/lessons.md` にパターンを追記（ファイル末尾が既に時系列になっている）。
- ユーザー向けのすべての説明・回答は日本語。ソース内の識別子・コメントは英語のまま。
