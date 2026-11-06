# Amusant

Amusant 是實驗性的按鍵通話裝置 Relay：Gemini Live 處理即時語音，Grok Routine 與 callback 處理委派任務；SQLite 保存任務與 Inbox 結果，StickS3 使用 TLS-PSK 與 WSS 連線。本候選仍在準備發布，尚未核准用於正式部署。

## 離線快速開始

準備 Python 3.13 與 [uv](https://docs.astral.sh/uv/)，在 repository 根目錄執行：

```sh
uv sync --locked --extra hardware-opus --group dev
uv run --locked python -m unittest relay.tests.test_grok_callback relay.tests.test_inbox fast_voice.tests.test_contract
```

這些測試使用假 Provider、合成憑證與本地資源，不需要 Gemini 或 Grok 憑證。完整離線測試選集包含裝置安全與 preflash 檢查，定義於 `.github/workflows/offline.yml`，可在 GitHub Actions 手動執行。

## 架構與設定

裝置將音訊送至 Relay，由 Gemini Live 處理即時回應。Slow 任務寫入 SQLite，經 Egress Proxy 送往設定的 Grok Routine；獨立 callback listener 接收結果並保存至 Inbox。支援的 Slow Executor 是 `grok_callback`；缺少或不支援的 Profile 會 fail-closed。

`deploy/oci-a1/grok-staging.env.example` 與 `compose.grok.yaml` 是設定示例，不是可直接部署的服務。Image 與服務網址刻意使用無效 placeholder；範例以本地檔案掛載秘密，並使用持久化 SQLite 目錄。請只在私有設定中替換 placeholder，勿提交真實憑證、主機名稱或私人部署檔案。Compose 沒有發布裝置或 callback 連接埠；仍須另行設計並驗證私人裝置 ingress 與具驗證機制的 callback ingress。

手動 CI 可分別建置 `amd64`／`arm64` Relay 與 Egress Image，並對固定 Kai 套用韌體 patch、執行 host 測試與僅供編譯的 StickS3 build。請參閱[韌體說明](patches/amusant-sticks3-base-v0.12.0.md)。建置成功不代表正式部署、Provider 整合或實體裝置操作已驗證。

## 授權與狀態

第三方聲明與未來韌體 binary 的授權義務見 [NOTICE.md](NOTICE.md)。Amusant 專案本身的正式授權仍待 Owner 決定；本文件不授予專案授權。[English README](README.md)。
