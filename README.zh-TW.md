# Amusant 本地候選

這是 PTT 裝置 Relay 的實驗候選，使用 Gemini Live、Grok callback 與 SQLite；尚未完成公開或部署驗證。需自行提供 Grok profile 與本地憑證，範例不含真實端點。

離線測試：先執行 `uv sync --locked --extra hardware-opus --group dev`，再執行 README 所列 unittest。韌體 preflash 必須提供 `--expected-host`，並與本地 secrets.h 一致。請勿依本候選直接刷寫或部署。專案授權待 Owner 決定。