# 設定與安全

`deploy/oci-a1/grok-staging.env.example` 的 image 和服務網址刻意設為無效 placeholder；它是設定參考，並非可直接部署的 Profile。請在版本控制之外準備私人設定，勿提交真實憑證或可識別部署資訊。

Compose 範例選用 `grok_callback`、SQLite 與獨立 Egress Proxy。Relay 使用本地檔案掛載秘密，entrypoint 會驗證檔案權限並拒絕直接放入環境變數的秘密。SQLite 使用持久化的 bind-backed volume。範例沒有發布裝置或 callback 連接埠，也沒有提供所需的獨立 ingress。

正式部署需另行設計私人裝置 ingress、具驗證機制的 callback ingress、Proxy 和 Owner 管理的憑證，並驗證完整路由與 Provider 設定。未經明確部署授權，不要讓範例連接真實服務。

韌體設定保存在獨立的 Kai 建置目錄。CI 的合成設定只供編譯，刻意無法用於真實部署；詳見[韌體說明](../patches/amusant-sticks3-base-v0.12.0.md)。
