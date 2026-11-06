# 自行架設 Amusant / Jellii（Public Alpha）

本文件說明如何重現原始碼建置，以及讓自架 StickS3 真正能使用還需要哪些項目。**這不是一鍵安裝程式，也不是已完成網際網路部署驗證的範例。** Repo 內的 Compose 刻意沒有提供完整 Ingress；請勿在未自行設計、驗證安全邊界前直接公開服務。

## 系統需求

| 元件 | 自行準備 |
| --- | --- |
| 裝置 | M5Stack StickS3（ESP32-S3）；USB 用於刷機與首次 Wi-Fi 配網 |
| 韌體 | 固定版本 Kai v0.12.0，加 Amusant Patch；PlatformIO Core 6.2.0 |
| Relay | 離線測試用 Python 3.13 / uv；部署用 Docker / Docker Compose |
| 語音 | 自己的 Google Gemini Live API 憑證 |
| 委派任務 | 可接收 HTTPS Webhook、完成後自行發送 Callback 的 Grok Bot Routine |
| 網路 | 獨立且安全的 Device TLS-PSK/WSS 與 Grok HTTPS Callback 入口 |
| 儲存 | 私有 SQLite 持久化目錄及 Secret 檔案 |

目前的 Compose 是以 **Linux/ARM64（OCI A1）** 為基礎；要在 AMD64 執行，需使用相符 Image 並修改寫死的 `platform`。Repo 沒有發布可直接部署的 Image，也沒有完整 Ingress 範例。

## 1. 確認原始碼與離線測試

在 Repo 根目錄安裝 Python 3.13、[uv](https://docs.astral.sh/uv/) 後執行：

```sh
uv sync --locked --extra hardware-opus --group dev
uv run --locked python -m unittest relay.tests.test_grok_callback relay.tests.test_inbox fast_voice.tests.test_contract
```

完整離線選集可見 [offline.yml](../.github/workflows/offline.yml)。這些測試使用假 Provider，不代表實際 Gemini / Grok 連線成功。

## 2. 建置 StickS3 韌體

依照 [Kai 固定版本 Patch 與 PlatformIO 指引](../patches/amusant-sticks3-base-v0.12.0.md)操作。CI 的 `secrets.h` 使用故意無效的合成值，**不可直接刷到真實裝置**。

在**獨立的私人 Kai 建置目錄**準備 `firmware/src/secrets.h`，填入自己的 Relay FQDN、TLS Port 443、唯一 PSK Identity、64 位十六進位且非全零的 PSK，以及 Device Token。Relay 端設定必須相符。刷機前於 Amusant Repo 根目錄執行：

```sh
python patches/sticks3-baseline/preflash.py /path/to/private/kai/firmware/src/secrets.h --expected-host your-device-host.example.net
```

請將範例網域換成自己的真實設定。此工具驗證格式與 Host 一致性，不驗證實際能否連線。通過後再依 Kai / PlatformIO 流程建置與刷機；本 Repo 尚未提供公開環境的完整刷機 E2E 驗收。

**Wi-Fi 是另外配置的：**刷機後透過 [BLE 或 USB 配網](wifi-provisioning.zh-TW.md)傳送 SSID／密碼。BLE **不能**變更已編入韌體的 Relay Host、PSK、Device Token，也不會配置 Gemini／Grok。

## 3. 準備獨立 Relay

閱讀[設定與安全邊界](configuration.zh-TW.md)、[環境變數範例](../deploy/oci-a1/grok-staging.env.example)及 [Compose](../deploy/oci-a1/compose.grok.yaml)。

自行更換所有無效的 Image Digest、Host、URL、Owner ID、網段與資源限制，並準備 SQLite 持久化目錄。必須建置且固定自己的 Relay／Egress Image；[build-images.yml](../.github/workflows/build-images.yml) 示範 CI 建置，但**不會將 Image 發布到 Registry**。

四種 Secret 都應在 Git 之外管理：

| Secret | 用途 |
| --- | --- |
| `gemini_api_key` | Gemini Live |
| `device_token` | Device → Relay 應用層驗證 |
| `device_psks` | TLS-PSK，格式為 `identity:64hexkey`，多組以逗號分隔 |
| `grok_routine_token` | Relay → Grok Routine Webhook Bearer Token |

請勿使用 CI 的全零 PSK 或任何 Placeholder。

Relay EntryPoint 強制 Secret 檔案為 **UID 10001／mode 0400**，或 **root-owned、GID 20000／mode 0440**（容器加入 supplemental group 20000）。Host 與 Docker Compose 的 Secret 掛載行為可能不同；要以**容器內實際看到的 Owner、Group、Mode** 驗證，而非只看 Host。權限不符時 Relay 會拒絕啟動。SQLite 目錄、備份也需避免對外公開。

準備自己的私有設定後，可先做 Compose 靜態檢查：

```sh
docker compose --env-file /path/to/private.env -f deploy/oci-a1/compose.grok.yaml config
```

`config` 通過**不等於**完成 Ingress、Secret 權限或 Provider 實際連線測試。

## 4. 補齊獨立 Ingress

範例 Compose 使用私人 Docker Networks，**沒有發布 Device／Callback Port**。

- **Device：**StickS3 以 **TLS 1.2 ECDHE-PSK + WSS** 連向指定網域的 **443**；Relay 內部 Device Listener 為 **8443**。入口必須保留端到端 PSK TLS 握手；一般終止憑證 TLS 的 HTTPS Reverse Proxy **無法直接取代**。
- **Grok Callback：**外部需有獨立的 HTTPS `POST /callback/results` 入口，安全轉送到 Relay 私有 Callback Listener（範例 **8645**），且保留預期的 Canonical `Host`。Receiver 驗證每個任務的 Bearer Capability、Host、Method、JSON 格式與長度。這不是 Device WSS 入口。
- **Outbound：**Relay 透過獨立 Egress Proxy 呼叫 Grok Routine；不可將 Proxy 當成公開代理伺服器。

目前**沒有**現成的完整網路部署腳本；請自行設計並驗證入口安全，勿直接把 8443／8645 裸露在外。

## 5. 串接 Grok Bot Routine

`AMUSANT_SLOW_EXECUTOR` 必須為 `grok_callback`。歷史 Hermes／ACP／DynamoDB 模組不屬於本版公開支援的 Runtime。

你的 Grok Bot Routine 必須：

1. 接收 Relay 的 **HTTPS POST**，JSON 內容含 `schema_version`、`task_id`、`result_token`、`task_label`、`instruction`，並驗證 Routine Bearer Token；接受後回傳 **HTTP 200**。
2. 執行任務，最後向指定的 `/callback/results` 發出 **HTTPS POST**。
3. 將該任務的 `result_token` 放入 Callback 的 `Authorization: Bearer`，**不得寫進 JSON Body**。結果內容須符合 [Callback Schema](../relay/grok_result.py) 與[測試範例](../relay/tests/test_grok_callback.py)。

Webhook 接收成功不等於任務已完成。Relay 使用 SQLite 紀錄提交狀態與 Callback，並經 Inbox 回傳結果；狀態不確定時不會盲目重送。

Repo 提供 Relay 端合約與程式，**不會替你申請 Grok Bot 帳號或建立 Routine**。需自行確認 Provider 支援必要的 HTTP 呼叫與權限。

## 6. 獨立環境 Smoke Test

請分階段驗證，不要用 Mock／CI PASS 冒充完整 E2E：

1. 合成設定下離線測試、固定 Kai 韌體建置 PASS。
2. Compose 靜態解析、Relay／Egress 啟動與停止、SQLite 重啟持久化、Secret 掛載均正確。
3. 錯誤 Device Token、未知 PSK、無效 Callback Capability 均被拒絕；真實 StickS3 可以經獨立 TLS-PSK/WSS 入口連線。
4. StickS3 PTT 能取得 Gemini Live 的語音回覆。
5. SLOW 任務成功提交 Grok Routine，獨立 Callback 回報，SQLite／Inbox 收到結果。
6. StickS3 B 鍵朗讀 Inbox 結果；**最後的語音仍經 Gemini Live 播放**，讀取後未讀提示清除。

其中真實硬體、外網 Ingress、Gemini／Grok 串接，都必須使用者自行完成驗收。

## Public Alpha 已知限制

- 沒有一鍵部署、正式公開 Image 或通用雲端部署教學。
- Compose 固定 ARM64，且刻意缺少 Ingress。
- 完整 SLOW 任務需要使用者自行建立可回傳 Callback 的 Grok Routine。
- 韌體 Build／離線 CI 不代表陌生使用者已經在實機複製 E2E。
- Wi-Fi 帳密儲存在裝置 Preferences/NVS，CRC／完整性檢查**不是** Flash Encryption；實體存取風險需另外評估。

實際手機配網與 USB 備援方式請見 [Wi-Fi 配網教學](wifi-provisioning.zh-TW.md)。
