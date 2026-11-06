# 離線測試與建置

## Python

使用 Python 3.13 與 uv，在 repository 根目錄執行：

```sh
uv sync --locked --extra hardware-opus --group dev
uv run --locked python -m unittest relay.tests.test_grok_callback relay.tests.test_inbox fast_voice.tests.test_contract
```

以上三個模組是快速檢查。完整離線測試選集位於 `.github/workflows/offline.yml`，包括裝置與 callback 安全、preflash 及 Wi-Fi provisioning。測試使用假元件與本地 loopback，不呼叫真實 Gemini 或 Grok Bot 服務。

## Image

手動 `build-images` workflow 以無快取方式建置 Relay 與 Egress Proxy。選擇 `amd64` 或 `arm64`；ARM64 使用 QEMU 並獨立執行。Image 只載入 runner，不會發布。Image 建置不代表正式 ingress 或付費 Provider 整合已驗證。

## 韌體

手動 `firmware-build` workflow 取得固定 Kai、套用整合 patch、執行指定 host regression，並使用合成設定編譯 StickS3。請參閱[韌體說明](../patches/amusant-sticks3-base-v0.12.0.md)。Workflow 不會刷寫實體裝置。
