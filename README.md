# 機票低價提醒 LINE Bot（優先顯示華航）

自動監控 TPE↔東京(成田/羽田) 未來航班的來回票價，**不限航空公司**，只要夠便宜就通知你；
但只要查到華航的價格，一律優先列在通知最上面（方便你評估要不要用會員身份訂華航）。
完全免費：資料來源用開源套件 `fast-flights` 查 Google Flights，排程用 GitHub Actions（免費額度足夠每天跑）。

跟著以下步驟做，不需要先懂程式，照著複製貼上就好。

---

## 第一步：建立 LINE 官方帳號（Messaging API）

1. 前往 https://developers.line.biz/console/ ，用你的 LINE 帳號登入。
2. 建立一個 **Provider**（名稱隨意，例如「我的機票小幫手」）。
3. 在該 Provider 底下建立一個 **Channel**，類型選 **Messaging API**。
   - Channel name、Channel description 隨意填
   - Category 選「個人使用」相關類別即可
4. 建立完成後，進入這個 Channel 的頁面：
   - 點選「**Messaging API**」分頁
   - 往下捲動找到 **Channel access token**，點「Issue」產生一組長期權杖（long-lived token）
   - 把這串文字複製下來、先貼到記事本備用（等一下要設定成 GitHub Secret）
5. 同一個頁面會有一個 **QR code**，用你的手機 LINE 掃描，把這個官方帳號加為好友。
   - 這一步很重要：因為程式是用「廣播（broadcast）」的方式發訊息給所有好友，如果沒加好友就收不到通知。
6. 建議把「Auto-reply messages」「Greeting messages」關掉（在 LINE Official Account Manager 後台 https://manager.line.biz/ 設定），這樣才不會有奇怪的自動回覆訊息干擾。

---

## 第二步：把程式碼放到 GitHub

1. 前往 https://github.com/new 建立一個新的 repository
   - 命名例如 `flight-alert-bot`
   - 選 **Private**（避免別人看到你的設定，雖然裡面沒有敏感資訊，但養成習慣比較好）
2. 建立好之後，把你收到的這整個資料夾（`flight-alert-bot/`）裡的檔案全部上傳上去：
   - 最簡單的方式：在 repo 頁面點 **Add file → Upload files**，把資料夾內所有檔案（含 `.github/workflows/monitor.yml`）拖進去上傳、commit。
   - 如果你之後想學用 Git 指令上傳也可以，但網頁上傳對新手最快。

---

## 第三步：設定 LINE Token（GitHub Secrets）

1. 在你的 GitHub repo 頁面，點 **Settings → Secrets and variables → Actions**
2. 點 **New repository secret**
   - Name 填：`LINE_CHANNEL_ACCESS_TOKEN`
   - Secret 填：第一步驟拿到的那串 Channel access token
   - 儲存

這樣程式在 GitHub 的伺服器上執行時，就能安全地讀到這組金鑰，而不會被公開看到。

---

## 第四步：手動測試一次

1. 到你的 repo 頁面，點上方的 **Actions** 分頁
2. 左側會看到 **Flight Price Monitor** 這個工作流程，點進去
3. 右邊有個 **Run workflow** 按鈕，點下去手動觸發一次
4. 等大約 1-3 分鐘執行完後，點進這次的執行紀錄，可以看到每個航班的查詢結果（log 裡會印出價格），確認：
   - 有沒有正確查到華航的價格
   - 印出來的價格數字大概是哪種幣別（可能是美金），再回去調整 `src/config.py` 裡的 `ABSOLUTE_PRICE_THRESHOLD`，改成合理的數字
5. 如果那次剛好有低於門檻的價格，你的 LINE 應該就會收到訊息了！

---

## 之後如何運作

- 每天台灣時間早上 9 點會自動執行一次（可在 `monitor.yml` 裡改 cron 時間）
- 程式會把每天查到的價格記錄在 `data/price_history.json`，並自動 commit 回你的 repo，累積歷史資料
- 累積到 5 筆以上歷史紀錄後，會同時用「絕對門檻」和「比歷史均價便宜 25% 以上」兩種邏輯判斷是否為好價格
- 只有真的判斷為便宜時，才會發 LINE 通知，平常不會每天吵你

---

## 你可以自訂的地方（都在 `src/config.py`）

| 設定 | 說明 |
|---|---|
| `ORIGINS` | 台灣出發機場，預設 `["TPE", "RMQ"]`，可加 `"KHH"`(高雄)、`"TSA"`(松山) |
| `DESTINATIONS` | 日本目的地機場，預設含東京(NRT/HND)、大阪(KIX)、福岡(FUK)、仙台(SDJ)、札幌(CTS) |
| `TRIP_LENGTH_DAYS` | 去程回程間隔天數 |
| `LOOKAHEAD_WEEKS` | 往後掃描幾週的週五出發航班 |
| `ABSOLUTE_PRICE_THRESHOLD` | 全網最便宜航班的絕對門檻（第一次執行後依實際幣別調整） |
| `THRESHOLD_PERCENT` | 全網最便宜航班相對歷史均價的便宜比例 |
| `MIN_HISTORY_POINTS` | 至少要幾筆歷史資料才啟用「相對比較」 |
| `CI_NAME_KEYWORDS` | 辨識華航航班的關鍵字 |
| `CI_ABSOLUTE_PRICE_THRESHOLD` | 華航自己的絕對門檻，可跟整體門檻不同 |
| `CI_THRESHOLD_PERCENT` | 華航自己相對歷史均價的便宜比例，可設寬鬆一點 |

改完直接在 GitHub 網頁上編輯、commit 即可生效，不需要重新部署。

---

## 常見問題

**Q: 為什麼查詢有時候會失敗（log 顯示 [warn] 查詢失敗）？**
A: `fast-flights` 是透過解析 Google Flights 網頁運作的，偶爾會因為對方網站改版或防爬蟲機制而暫時查不到，屬正常現象，程式會自動跳過該筆、繼續查其他日期，不會整個中斷。

**Q: 可以同時監控其他航空公司嗎？**
A: 可以，把 `monitor.py` 裡 `extract_china_airlines_price` 的篩選條件拿掉或改成其他航空公司名稱即可。

**Q: LINE 的 broadcast 訊息會不會有數量限制？**
A: LINE 官方帳號的免費方案有每月訊息則數上限（依方案而定），但因為程式只有在真的發現便宜票時才會發送，正常使用量不會超過限制。
