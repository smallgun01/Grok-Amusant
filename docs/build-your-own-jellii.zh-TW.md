# Build Your Own Jellii — ARM64 單機參考建置

**狀態：Public Alpha 可執行參考流程，尚未通過獨立全新環境實機 E2E 驗收。** 這份文件提供從原始碼到 FAST／SLOW 的逐步操作，不會為了簡單而移除 TLS-PSK 或 Callback 的驗證。適用一台安裝 Docker Engine 28+、Compose v2、Python 3.13、Git 的 **Ubuntu Linux ARM64** 主機，搭配 M5Stack StickS3、USB 刷機連線及自己的 Gemini API Key；SLOW 另需 Grok Bot Routine。

### 網路前提

準備兩個**實際由你控制**、直接指向主機的 DNS 名稱（分別供 Device／Callback 使用）。對外需 TCP **443**（裝置端 TLS-PSK 原始 TCP）、**9443**（Caddy HTTPS Callback）、**80**（ACME HTTP-01 簽憑證）。443 不得由一般 HTTPS Reverse Proxy 佔用。家用主機要設定路由器 Port Forwarding。

**如果你的 Grok Bot HTTP 工具不支援 HTTPS Port 9443，這份單 IP 方案就不適用。** 必須另行設計及驗證標準 443 的獨立 Callback HTTPS 入口，不可以放寬驗證機制。

## 1. 取得原始碼並套用固定 Kai Patch

```sh
git clone https://github.com/smallgun01/Grok-Amusant.git ~/Grok-Amusant
cd ~/Grok-Amusant
git clone https://github.com/zbruceli/kai.git ../kai-build
git -C ../kai-build checkout --detach 1681fb3c11acd28525d8987bba4b9bf4bc2a288f
git -C ../kai-build apply --check "$PWD/patches/amusant-sticks3-base-v0.12.0.patch"
git -C ../kai-build apply "$PWD/patches/amusant-sticks3-base-v0.12.0.patch"
```

**PASS：**Patch 成功套用到指定 Kai Commit。更詳細請見[韌體說明](../patches/amusant-sticks3-base-v0.12.0.md)。韌體 Secret／私人配置**不可**放入 Git。如果後端在遠端 VPS，請於可信任的刷機電腦另行建置韌體，透過安全途徑提供相符的私人配置。

## 2. 產生配對的 Relay／StickS3 金鑰

請把下方兩個 Domain 替換成**真實且屬於你的網域**：

```sh
python3 deploy/selfhost/prepare.py \
  --device-host device.your-real-domain.net \
  --callback-host callback.your-real-domain.net \
  --private-dir "$HOME/amusant-private" \
  --firmware-dir "$HOME/kai-build/firmware"
```

工具以**隱藏輸入**取得 Gemini API Key 與 Grok Routine 的 Webhook Bearer Token；第二項留白會產生隨機 Token，之後必須將**相同 Token**配置到 Grok。已有 Routine URL 可以加上 `--routine-url https://你的實際Routine/你的Webhook路徑`，否則後續在私人 `private.env` 補入。沒有正確 Routine URL 時只做 **FAST-first**，不能宣稱 SLOW 可用。

工具會建立 `~/amusant-private/private.env`、四份 Secret 檔案（`gemini_api_key`、`device_token`、`device_psks`、`grok_routine_token`）、`Caddyfile`，及 `~/kai-build/firmware/src/secrets.h`。裝置與 Relay 的 PSK／Token 一致；工具不輸出金鑰，也不覆寫既有憑證。

一般 **rootful Linux Docker** 環境下，依 Relay 現有驗證設權限：

```sh
sudo chown root:20000 "$HOME"/amusant-private/secrets/*
sudo chmod 0440 "$HOME"/amusant-private/secrets/*
sudo chown -R 10001:10001 "$HOME"/amusant-private/data/relay
sudo chmod 0700 "$HOME"/amusant-private/data/relay
```

勿將上述 `chown` 套用到 Firmware `secrets.h` 或 `private.env`。Docker Compose 的 File-backed Secret 並不保證重新映射 UID／GID；若啟動失敗，要看**容器內**檔案的 Owner／Mode，不得取消 Relay 的 Fail-closed 檢查。Rootless Docker／NAS 等環境可能需要不同作法。

## 3. Build / Start Relay 與 Egress Proxy

```sh
cd ~/Grok-Amusant
docker build --platform linux/arm64 -f deploy/oci-a1/Dockerfile.relay -t amusant-relay:selfhost .
docker build --platform linux/arm64 -f deploy/oci-a1/Dockerfile.egress-proxy -t amusant-egress:selfhost .
docker compose --env-file "$HOME/amusant-private/private.env" \
  -f deploy/oci-a1/compose.grok.yaml -f deploy/selfhost/compose.ports.yaml config --quiet
docker compose --env-file "$HOME/amusant-private/private.env" \
  -f deploy/oci-a1/compose.grok.yaml -f deploy/selfhost/compose.ports.yaml up -d
docker compose --env-file "$HOME/amusant-private/private.env" \
  -f deploy/oci-a1/compose.grok.yaml -f deploy/selfhost/compose.ports.yaml ps
```

**PASS：**Relay／Egress 都 Running、Healthy，SQLite 經容器重啟仍有資料。[Ports Override](../deploy/selfhost/compose.ports.yaml) 讓 Host 443 保留 **TLS-PSK 原始 TCP** 直接對接 Relay 私有 8443；Callback 8645 **僅**轉到 Host 本地 `127.0.0.1:18645`。注意私網 CIDR 衝突、Secret 權限、埠口佔用。不可直接公開內部 Callback 或 Egress Proxy。

## 4. Callback HTTPS（與裝置 TLS-PSK 不同）

依[Caddy 官方安裝說明](https://caddyserver.com/docs/install)在 Host 安裝。Caddy 在 **9443** 提供 Callback HTTPS，透過 **80** 完成 ACME HTTP-01 憑證驗證；TLS-ALPN 挑戰停用，因為 443 保留給 StickS3。工具已產生正確 Callback Domain 的私人 Caddyfile：

```sh
sudo install -o root -g root -m 0644 "$HOME/amusant-private/Caddyfile" /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

從**外部電腦**發送不帶 Token 的測試，換成真正網域：

```sh
curl -i -X POST 'https://callback.your-real-domain.net:9443/callback/results' \
  -H 'Content-Type: application/json' -d '{}'
```

**PASS：**應收到 Relay 回的 **HTTP 401**，證明 TLS、反向代理與 Callback Receiver 可達，但因未提供 Task Capability 而被拒。404／502／TLS 錯誤／Timeout 都不算成功。代理要保留 `Host`（含 :9443）與 URL Path；Callback 與 Device WSS 是不同端點。

## 5. 真實 StickS3 韌體編譯／刷機

使用生成的私人 `secrets.h`，**不可使用 CI 的全零 PSK**：

```sh
cd ~/Grok-Amusant
python3 patches/sticks3-baseline/preflash.py \
  "$HOME/kai-build/firmware/src/secrets.h" --expected-host device.your-real-domain.net
python3 -m venv .venv-firmware
. .venv-firmware/bin/activate
python -m pip install platformio==6.2.0 -r patches/kai-v0.12.0-wifi-w1/tools/requirements.txt
platformio run -d ../kai-build/firmware -e sticks3
platformio run -d ../kai-build/firmware -e sticks3 -t upload
```

**PASS：**Preflash PASS、PlatformIO 編譯完成、USB Flash 成功。若後端與刷機電腦不同，需在可信任電腦建置／刷機，安全傳送匹配的私人設定，不要將含金鑰的 Firmware Binary 公開。

## 6. 手機 BLE Wi-Fi 配網

依照 [BLE／PoP／QR 與 USB 備援](wifi-provisioning.zh-TW.md)：首次 USB 開機且無既存 Wi-Fi 設定，螢幕顯示 **26 字元 PoP**，**不是 QR**。用支援 Espressif BLE Provisioning 的手機 App 搜尋 `PROV_XXXXXX`，輸入本次 PoP，推送 2.4 GHz Wi-Fi 帳密。

後續更換 Wi-Fi：在 Idle／Offline **長按側邊 B 鍵 5 秒**，裝置重啟進入 QR Setup；正面 A 鍵切換 QR／PoP、B 鍵取消。

**PASS：**裝置成功取得 Wi-Fi IP，且進一步透過 **443 TLS-PSK/WSS** 接上 Relay。Wi-Fi 成功不等於 WSS 成功。

## 7. 第一次 FAST 與 SLOW

**FAST：**Relay Healthy、StickS3 Online 時，按住正面 A 鍵問「Webhook 是什麼？」，放開後確認 **Gemini Live 經 Relay 回傳聲音**。如果 Grok Routine 尚未配置，要標註僅 FAST PASS。

**SLOW：**自行設定 Grok Bot Routine，讓它以 Bearer Token 驗證來自 Relay 的 HTTPS POST，Body 含 `schema_version`、`task_id`、`result_token`、`task_label`、`instruction`，成功接收後回 **200**。任務完成後，把 `result_token` 放在回呼 Header `Authorization: Bearer`，向 `https://callback.你的網域:9443/callback/results` POST JSON，**不可把 Token 寫進 Body 或 Log**。Body 必須符合[真正的 Schema](../relay/grok_result.py)：`schema_version="amusant-grok-pilot-0"`、原 `task_id`、`reported_outcome`、`speak`、`card`（`title` 和 1–4 行 `lines`）。將私有 `AMUSANT_GROK_ROUTINE_URL` 改成實際 Webhook，再重啟 Relay。

**SLOW PASS：**自然語言委派小任務 → Routine 接收 → Callback 200 → SQLite／Inbox 有未讀 → 側邊 B 朗讀結果（**Gemini Live 語音**）→ 未讀徽章清除。

目前 Repo 有 Relay 端合約，但 **Grok Bot Routine 在第三方平台的建立與 HTTP Tool 權限仍須自行操作**；不同平台版本可能不相同，尚未經陌生開發者的公開環境 E2E 實測。

## 驗收標準及限制

| 項目 | PASS 標準 |
| --- | --- |
| Source | 固定 Kai Patch、Preflash、韌體 Build |
| Server | Relay／Egress Healthy；SQLite 持久化、Secret 權限正確 |
| Network | TLS-PSK 443、獨立 HTTPS Callback 9443 |
| Wi-Fi | StickS3 真機完成 BLE／PoP／QR |
| FAST | Gemini Live 回傳實際語音 |
| SLOW | Grok Routine → Callback → Inbox → B 鍵朗讀 |

**注意：**這份新參考架構尚未經全新環境的實際 Docker／HTTPS／StickS3／Grok E2E 驗收。原 Amusant Owner 驗收不能直接冒充新部署的 PASS；CI 和靜態測試也不能。若 Grok Bot 不接受 9443，需另外設計經驗證的安全入口，不能放寬認證。

補充資訊：[架構](architecture.zh-TW.md) · [設定](configuration.zh-TW.md) · [離線測試](quickstart.zh-TW.md)。
