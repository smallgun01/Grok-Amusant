# StickS3 Wi-Fi 配網：BLE＋PoP／QR，以及 USB 備援

Jellii 韌體支援 **Espressif BLE Wi-Fi Provisioning（Security 1）**。手機透過短時間啟用、具驗證的 BLE Session 傳送 Wi-Fi SSID 與密碼。**BLE 不會設定 Relay Host、TLS-PSK、Device Token 或 Gemini／Grok 憑證。** 這些必須另外準備，請參閱[自行架設](self-hosting.zh-TW.md)。

## 準備事項

- 已刷入 **Amusant Patch 且完成私人配置**的 StickS3；不可使用 CI 的合成測試韌體。
- 安裝相容的 **Espressif ESP BLE Provisioning** 手機 App（支援 Security 1／PoP），並允許藍牙權限；**不是**到手機系統藍牙頁面直接配對。
- 可使用的 ESP32 2.4 GHz Wi-Fi、SSID、密碼。
- 首次配網使用 USB 供電；後續手動重新配網可用 USB 或足夠電池。

## 第一次配網：裝置尚未儲存 Wi-Fi

1. 讓**沒有 Wi-Fi 設定檔**的 StickS3 連接 USB 供電並開機。
2. 螢幕顯示 **Secure Wi-Fi setup** 與分兩行的 **26 字元 PoP（Proof of Possession）**。**第一次配網不顯示 QR Code。**
3. 打開 ESP BLE Provisioning App，搜尋 `PROV_XXXXXX` 裝置，選擇 StickS3，於 App 提示時輸入螢幕上當次的 PoP。
4. 在 App 選擇／輸入 Wi-Fi SSID 與密碼，透過 BLE 送出。
5. 裝置確認 Wi-Fi **已連線且取得 IP** 後才儲存設定；BLE Session 結束後恢復一般 Wi-Fi／Relay 連線流程。

首次配網視窗為 **120 秒**。逾時或失敗可使用下方 [USB 備援](#usb-serial-備援)，或在 USB 供電下重啟再試。BLE 傳送成功不代表 Relay 已連上。

## 已配網裝置：長按 B 鍵重新配網（QR）

1. 在正常 **Idle／Offline** 狀態（非聆聽或播放中），**長按側邊 B 鍵 5 秒**。螢幕關閉時先喚醒，再重新長按。
2. 裝置軟體重新啟動，進入獨立 Wi-Fi Setup，螢幕顯示包含 BLE 裝置名稱及當次 PoP 的 **QR Code**。
3. 用支援 ESP Provisioning 格式的 App 掃 QR。如果 App 不支援掃描，按**正面 A 鍵**切換到 PoP 文字頁，在 App 手動選擇 `PROV_XXXXXX`、輸入 PoP。A 鍵可切換 QR／PoP。
4. 在 App 傳送新的 Wi-Fi 設定，等待 Wi-Fi＋IP 驗證、寫入設定並返回正常模式。按 **B 鍵**可取消這次配網，**不會覆蓋原本儲存的 Wi-Fi**。

手動配網視窗是 **180 秒**。若未插 USB，需至少 **20% 電量**才能進入；配網中電量過低會退出。QR 包含的是暫時的 BLE 裝置名稱與 PoP，**不是 Wi-Fi 密碼**；它也**不是**一般 Wi-Fi 分享 QR Code。

## 常見問題

| 狀況 | 檢查項目 |
| --- | --- |
| 手機找不到 `PROV_XXXXXX` | StickS3 必須正處於有限時間的 BLE 配網視窗；確認藍牙權限、電源、重新啟動 Session |
| PoP 驗證失敗 | 輸入螢幕上**當次**完整的 26 字元；不同 Session 的 PoP 會改變 |
| App 顯示送出成功但連不上 Wi-Fi | 核對 SSID／密碼、路由器相容性，確認有取得 IP |
| 重試多次仍失敗 | BLE 對 Credential Failure 有次數上限；重新開始 Session 或使用 USB |
| 首次配網沒有 QR Code | **正常**：USB 供電首次無設定檔時顯示 PoP；長按 B 進 Owner Setup 才顯示 QR |
| 顯示 `reboot to retry BLE` | 重新啟動或 USB 備援，再開始新的配網 |
| Wi-Fi 成功但 Jellii 仍 Offline | 檢查 Relay FQDN、PSK／Token、TLS-PSK Ingress 與伺服器；BLE 不會設定這些 |

正常 PTT／Relay 運作時，BLE 會保持關閉，不會持續廣播。

## USB Serial 備援

Repo 另提供 [`provision_wifi.py`](../patches/kai-v0.12.0-wifi-w1/tools/provision_wifi.py) 工具。先在**電腦端**安裝依賴並確認 USB Serial Port，從 Repo 根目錄執行：

```sh
python -m pip install -r patches/kai-v0.12.0-wifi-w1/tools/requirements.txt
python patches/kai-v0.12.0-wifi-w1/tools/provision_wifi.py --port /dev/ttyACM0 status
python patches/kai-v0.12.0-wifi-w1/tools/provision_wifi.py --port /dev/ttyACM0 set
```

Windows 將 `/dev/ttyACM0` 改成實際埠號（例如 `COM6`）。`set` 會互動式詢問 SSID，並使用隱藏輸入讀取密碼，不必在命令列暴露密碼。要清除已儲存 Wi-Fi：

```sh
python patches/kai-v0.12.0-wifi-w1/tools/provision_wifi.py --port /dev/ttyACM0 clear
```

清除 Wi-Fi 不會清掉韌體內的 Relay PSK／Token。BLE 不可用、逾時或 Wi-Fi 設定損毀時，都可以用 USB 備援。

## 安全與隱私

- BLE 透過 **Security 1＋每次產生的 PoP** 驗證；請勿分享含有效 PoP 的畫面或截圖。
- 成功驗證後 Wi-Fi 資料儲存在裝置 Preferences／NVS。CRC／完整性檢查**不等於儲存加密**。
- TLS-PSK 與 Device Token 必須和自己的 Relay 相符；勿公開或 Commit 真實的 `secrets.h`。
- Repo 的測試涵蓋 BLE／QR 程式接線，但真實手機 App 與無線連線仍需實機驗證。
