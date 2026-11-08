# 設定與安全

`deploy/oci-a1/grok-staging.env.example` 的 image 和服務網址刻意設為無效 placeholder；它是設定參考，並非可直接部署的 Profile。請在版本控制之外準備私人設定，勿提交真實憑證或可識別部署資訊。

Compose 範例選用 `grok_callback`、SQLite 與獨立 Egress Proxy。Relay 使用本地檔案掛載秘密，entrypoint 會驗證檔案權限並拒絕直接放入環境變數的秘密。SQLite 使用持久化的 bind-backed volume。範例沒有發布裝置或 callback 連接埠，也沒有提供所需的獨立 ingress。

正式部署需另行設計私人裝置 ingress、具驗證機制的 callback ingress、Proxy 和 Owner 管理的憑證，並驗證完整路由與 Provider 設定。未經明確部署授權，不要讓範例連接真實服務。

韌體設定保存在獨立的 Kai 建置目錄。CI 的合成設定只供編譯，刻意無法用於真實部署；詳見[韌體說明](../patches/amusant-sticks3-base-v0.12.0.md)。

## 出站 Host 政策

在私有 Compose 環境中設定 `EGRESS_PROXY_ALLOWED_HOSTS`，以逗號分隔部署實際需要的精確 DNS 名稱。未設定、空白、格式錯誤、萬用字元或 IP literal 均 fail-closed；CONNECT 名稱正規化後精確比對，每次 DNS 結果也必須全部為公開位址。固定版本 `google-genai` SDK 的 API-key Live 預設位址為 `generativelanguage.googleapis.com`，但輔助端點與 Owner 自行設定的 Grok Routine Host 仍須依實際部署確認；此資訊不是完整白名單。真實 Routine URL 不得放進 repository。

兩種 Provider 共用同一 Relay 程序與網路身分，這項政策不能隔離其憑證。CONNECT Host 比對不檢查加密的 TLS SNI，也無法區分共用允許 IP 的其他服務。部署前須私下確認所需 Host，並由 Owner 核准 FAST／SLOW 連線驗證及回滾；清單不完整會阻擋連線。
