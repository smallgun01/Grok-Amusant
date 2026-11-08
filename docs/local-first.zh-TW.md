# Local-first Jellii：先在本地完成第一次 FAST 語音

**第一次自己做 Jellii，從這裡開始。** 這是 Amusant 當初開發的順序：先讓 StickS3 在家中 Wi-Fi 連本地 Relay、透過 Gemini Live 開口說話；再加上 Grok SLOW，最後才考慮 OCI 或常駐部署。

**已知實績與安裝教學是兩件事：**Amusant Owner 曾完成**本地 Ubuntu Relay＋實體 StickS3 的 FAST E2E**，後來也在 OCI 驗證 **Gemini FAST／Grok SLOW／Callback／Inbox／B 鍵朗讀**。這不代表新的公開 DIY 指引已由陌生使用者從乾淨環境完整驗證。

## 準備事項

- 一台可執行 Docker Engine／Compose v2 的 **Linux AMD64 或 ARM64** 電腦（Ubuntu、迷你主機或 Linux Server），安裝 Git、Python 3.13。Windows／macOS 可以另用 Linux 主機或 VM，但不在本路徑驗證範圍。
- M5Stack StickS3、USB 資料線、安裝相容 ESP BLE Provisioning App 的手機，以及 **2.4GHz Wi-Fi**。
- 自己的 Gemini API Key（須可使用 Gemini Live）。**第一次 FAST 不需要 Grok Bot、網域、公開 IP、Caddy、Cloudflare 或打開對外 Port。**

Linux 主機與 StickS3 必須互相連得到（不要用 Guest Wi-Fi／AP Client Isolation）。確認主機的固定 **RFC1918 LAN IPv4**（例如 `192.168.68.57`），最好在路由器預留 DHCP 位址。即使在區網，裝置仍使用 **TLS 1.2 ECDHE-PSK + WSS／TCP 443**，不退回明文。

## 1. Clone 與套用 Kai Patch

在 Linux 執行：

```sh
git clone https://github.com/smallgun01/Grok-Amusant.git ~/Grok-Amusant
cd ~/Grok-Amusant
git clone https://github.com/zbruceli/kai.git ../kai-build
git -C ../kai-build checkout --detach 1681fb3c11acd28525d8987bba4b9bf4bc2a288f
git -C ../kai-build apply --check "$PWD/patches/amusant-sticks3-base-v0.12.0.patch"
git -C ../kai-build apply "$PWD/patches/amusant-sticks3-base-v0.12.0.patch"
```

**PASS：**固定的 Kai Commit 可乾淨套用 Patch。任何私人設定都不能 Commit 進 Git。

## 2. 產生配對的 Local Relay／StickS3 憑證

用 `ip -4 addr` 找到你的 Linux LAN IPv4；將下方 `192.168.68.57` 改為**你自己主機真正擁有的位址**。允許範圍為 10/8、172.16/12、192.168/16，不接受 localhost。

```sh
cd ~/Grok-Amusant
python3 deploy/selfhost/prepare.py --local \
  --device-host 192.168.68.57 \
  --private-dir "$HOME/amusant-private" \
  --firmware-dir "$HOME/kai-build/firmware"
```

工具會隱藏輸入 Gemini API Key，並在 Repo **外面**建立 `~/amusant-private/private.env`、裝置和 Relay 相符的 PSK／Token、SQLite 目錄，以及 `~/kai-build/firmware/src/secrets.h`。不會把金鑰印出，也不會覆蓋現有設定。

**這階段故意把 Grok Routine 與 Callback URL 保持成無效範例**，讓 FAST 先運作；不要把它說成 SLOW 已可使用。

一般 **rootful Linux Docker** 的 Secret 檔案與 SQLite 權限設定：

```sh
sudo chown root:20000 "$HOME"/amusant-private/secrets/*
sudo chmod 0440 "$HOME"/amusant-private/secrets/*
sudo chown -R 10001:10001 "$HOME"/amusant-private/data/relay
sudo chmod 0700 "$HOME"/amusant-private/data/relay
```

上述指令只用於剛才新產生的目錄，**不要修改** `secrets.h` 或 `private.env`。Rootless Docker 有 UID 映射差異，須另行核對。

## 3. 在本地 Build／啟動容器

```sh
cd ~/Grok-Amusant
docker build -f deploy/oci-a1/Dockerfile.relay -t amusant-relay:selfhost .
docker build -f deploy/oci-a1/Dockerfile.egress-proxy -t amusant-egress:selfhost .
docker build -f deploy/selfhost/Dockerfile.device-ingress -t amusant-device-ingress:selfhost .
docker compose --env-file "$HOME/amusant-private/private.env" \
  -f deploy/oci-a1/compose.grok.yaml -f deploy/selfhost/compose.local.yaml config --quiet
docker compose --env-file "$HOME/amusant-private/private.env" \
  -f deploy/oci-a1/compose.grok.yaml -f deploy/selfhost/compose.local.yaml up -d
docker compose --env-file "$HOME/amusant-private/private.env" \
  -f deploy/oci-a1/compose.grok.yaml -f deploy/selfhost/compose.local.yaml ps
```

Local Override 加入原始 TCP ingress 容器，**只在指定 LAN IPv4 開放 TCP 443**，將加密的裝置連線轉送至 Relay。Relay 本身仍使用內部網路；ingress 容器不連接 callback 網路。Callback 不對外發布，而且 Grok 未配置前保持不可用。主機防火牆須允許**區網**連 TCP 443；**不要**在路由器開對外 Port Forward。

**PASS：**Relay／Egress Running、Healthy，device ingress Running，SQLite 可保存。啟動不成功請看 Compose Logs，核對 Secret UID／GID、443 是否被佔用、Docker 私有網段衝突，以及 Gemini API。

## 4. Preflash、編譯及刷入 StickS3

`--allow-private-ip` 是**明確選擇 LAN 模式**才用的參數。雲端模式不允許這個例外，且兩種模式都必須有正確的 PSK／Token／TLS 443。

```sh
cd ~/Grok-Amusant
python3 patches/sticks3-baseline/preflash.py \
  "$HOME/kai-build/firmware/src/secrets.h" \
  --expected-host 192.168.68.57 --allow-private-ip
python3 -m venv .venv-firmware
. .venv-firmware/bin/activate
python -m pip install platformio==6.2.0 -r patches/kai-v0.12.0-wifi-w1/tools/requirements.txt
platformio run -d ../kai-build/firmware -e sticks3
platformio run -d ../kai-build/firmware -e sticks3 -t upload
```

**PASS：**Preflash、Build 與 USB Flash 成功。詳見[固定版韌體建置](../patches/amusant-sticks3-base-v0.12.0.md)。產生的 Binary 含你的憑證，**不要分享或上傳 GitHub**。

## 5. BLE Wi-Fi 配網、第一次 FAST 對話

照 [BLE／PoP／QR 與 USB 備援](wifi-provisioning.zh-TW.md)操作：未儲存 Wi-Fi 的裝置首次以 USB 開機顯示 **PoP 文字（不是 QR）**。ESP BLE Provisioning App 搜尋 `PROV_XXXXXX`、輸入當次 PoP，傳送 2.4GHz Wi-Fi 帳密。後續重新配網可在 Idle／Offline **長按 B 五秒**進 QR 模式。

裝置連上 Wi-Fi 和本地 Relay 後，**按住 A 鍵問「Webhook 是什麼？」再放開**。**PASS：**StickS3 真正播放 Gemini Live 的語音。僅 Wi-Fi 連線成功不算完整 E2E。

## 6. FAST 通過後才加入 Grok SLOW

Runtime 的 Executor 類型仍是 `grok_callback`；但沒配置有效 Routine／Callback 時，不會派送真實 SLOW 任務。

要讓 Grok 回呼到本地，必須有具驗證能力的**公開 HTTPS Callback** 與真正的 Grok Routine。可以先在本地 Linux 試用 [Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)：

```sh
cloudflared tunnel --url http://172.30.74.2:8645
```

上述容器 IP 來自範例 `RELAY_CALLBACK_BIND_IP`；**先用 `docker compose config` 確認實際位址**。這需要 rootful Linux Docker Host 能連上該 Private Bridge Interface。Callback Listener 實際綁的是獨立的 Docker 私有位址，**不是 `127.0.0.1`**。不能將 HTTP 8645 直接公開至網際網路。

Tunnel 會產生暫時的 `https://….trycloudflare.com` 網址。請在私人 `private.env` 把 `AMUSANT_GROK_CALLBACK_URL` 改成 `https://<Tunnel實際網域>/callback/results`，`AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS` 設同一網域、`AMUSANT_GROK_CALLBACK_SERVICE_PORT=443`。填入 Grok 真正的 `AMUSANT_GROK_ROUTINE_URL` 後重啟 Relay。從外部對該 Callback URL 發出**沒有 Bearer Token** 的 POST；預期 **HTTP 401**，代表安全拒絕成功而且 HTTP 路由可達。

之後在 Grok Bot Routine 中設定接收任務、執行完成再以 `Authorization: Bearer <任務 result_token>` 回送符合格式的 JSON。詳見 [Grok Callback 合約](self-hosting.zh-TW.md#5-串接-grok-bot-routine) 與[進階 ARM64 Guide](build-your-own-jellii.zh-TW.md#7-第一次-fast-與-slow)。

**SLOW PASS：**自然語言交辦 → Grok Routine 接收 → Callback → Inbox 未讀 → B 鍵經 Gemini Live 朗讀 → 徽章清除。Quick Tunnel 是開發測試入口，重啟可能換 URL；正式運作請改用進階部署。

## 7. 後續搬上 OCI

見 [進階 ARM64／OCI 部署](build-your-own-jellii.zh-TW.md)。Amusant 專案已做過**本地 FAST 實機驗證**與**OCI FAST／SLOW 完整實機驗證**，但新發布的逐步安裝手冊仍需外部使用者重現驗收。

**安全：**所有私人 `secrets.h`、PSK、Token、Callback Capability、設定檔及 Firmware Binary 都不能上傳 Git；不允許裝置 TLS-PSK 降級，也不要公開私有 HTTP Callback。
