# Amusant

[English](README.md) | 繁體中文

Amusant 是實驗性的按鍵通話夥伴，透過自架 Relay 連接 StickS3、Gemini Live 語音與 Grok 委派任務。

```text
 +----------+   TLS-PSK / WSS   +----------------------+
 | StickS3  | <---------------> | Relay                |
 | 裝置     |                   |  - Gemini Live 快速路徑 | ---> Gemini Live
 +----------+                   |  - SQLite + Inbox    |
                                |  - Grok callback     |
                                +----------+-----------+
                                           | 委派任務
                                           v
                                +----------------------+
                                | Egress Proxy         | ---> Grok Routine
                                +----------------------+         |
                                         callback ingress <------+
```

Callback ingress 需另外設定；Compose 範例沒有公開該連接埠。支援的慢速執行器為 `grok_callback`；設定缺失或不支援時會 fail-closed。目前仍是 staging 候選，尚未核准正式部署。

## 功能

- **語音路徑：** Gemini Live 與按鍵通話裝置協定。
- **委派任務：** Grok Routine 提交與具驗證機制的 callback。
- **持久化結果：** SQLite 任務紀錄及依使用者區分的 Inbox。
- **裝置傳輸：** TLS-PSK、WSS 與出站 Egress Proxy。
- **離線驗證：** 假 Provider、callback、儲存及裝置測試，搭配固定 Kai 韌體 patch 與 host 測試。

## 快速開始：離線測試

安裝 Python 3.13 與 [uv](https://docs.astral.sh/uv/)，在 repository 根目錄執行：

```sh
uv sync --locked --extra hardware-opus --group dev
uv run --locked python -m unittest relay.tests.test_grok_callback relay.tests.test_inbox fast_voice.tests.test_contract
```

測試使用合成資料與假 Provider，不需要真實 Provider 憑證。完整離線 CI 選集與手動建置方式見[快速開始](docs/quickstart.zh-TW.md)。

## 文件

- [架構與資料流程](docs/architecture.zh-TW.md)
- [設定與安全邊界](docs/configuration.zh-TW.md)
- [離線測試與建置](docs/quickstart.zh-TW.md)
- [韌體建置](patches/amusant-sticks3-base-v0.12.0.md)
- [參與貢獻](CONTRIBUTING.md)與[安全政策](SECURITY.md)
- [第三方聲明](NOTICE.md)

## Repository 結構

```text
fast_voice/    Gemini Live Provider 與語音介面
relay/         裝置、callback、任務及 Inbox runtime
relay/tests/   離線 runtime 與安全測試
deploy/        Dockerfile、Compose 與範例設定
patches/       整合韌體 patch 與 host regression
docs/          架構、設定及快速開始
third_party/   上游授權聲明
```

Repository 保留部分歷史資料相容與測試程式，不代表支援這些舊後端。Compose 範例的 image 與服務網址刻意設為無效 placeholder，裝置與 callback ingress 必須另行設計。

## 授權

Amusant 自有程式採用 [MIT License](LICENSE)。第三方內容仍適用各自條款，詳見 [NOTICE.md](NOTICE.md)；發行韌體 binary 時可能需要額外的依賴聲明。
