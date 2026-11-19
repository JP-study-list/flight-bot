# ============================================
# 監控設定 - 依需求自行調整
# ============================================

# 監控的城市對（台灣機場, 日本機場），每一對都會「雙向」監控單程票
#   TPE = 桃園   RMQ = 台中   TSA = 台北松山
#   NRT = 東京成田   HND = 東京羽田   KIX = 大阪關西   FUK = 福岡   SDJ = 仙台
CITY_PAIRS = [
    ("TPE", "NRT"),
    ("TPE", "HND"),
    ("TPE", "KIX"),
    ("TPE", "FUK"),
    ("TPE", "SDJ"),
    ("RMQ", "NRT"),
    ("RMQ", "KIX"),
    ("TSA", "HND"),
]

LOOKAHEAD_WEEKS = 12        # 往後掃描幾週
WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]  # 掃描的出發星期：0=一 1=二 2=三 3=四 4=五（週一至週五）

# 每次查詢之間的間隔秒數（降低被 Google 暫時封鎖的機率）
QUERY_DELAY_SECONDS = 0.8

# 第一輪查不到的，最後用這個間隔再查一次
RETRY_DELAY_SECONDS = 2

# 重查後仍有超過這個比例查不到，就在通知裡說（價格可能不準）
FAIL_ALERT_RATE = 0.5

# ============================================
# 便宜判斷邏輯（單程票，全航空公司都算）
# ============================================

# 1) 絕對門檻：單程票低於這個價格（新台幣）就算好價；None = 不用
#    2026-11-19 拿掉 4500：大阪→台北平常就在 3,400 左右，變成每天通知
ONEWAY_PRICE_THRESHOLD = None

# 2) 相對門檻：低於「該航線最近 RELATIVE_WINDOW 天每日最低價的中位數 * 這個比例」算好價
#    （0.85 = 比平常便宜 15% 以上）。同一條航線通知過、價格沒再降就不重發
THRESHOLD_PERCENT = 0.85
RELATIVE_WINDOW = 30

# 3) 至少累積幾天歷史資料，才啟用「相對門檻」判斷
MIN_HISTORY_POINTS = 7

# ============================================
# 華航優先設定（你有會員，門檻放寬）
# ============================================

CI_NAME_KEYWORDS = ["China Airlines", "華航"]
CI_ONEWAY_PRICE_THRESHOLD = 5500   # 華航單程門檻（新台幣）
CI_THRESHOLD_PERCENT = 0.85

# ============================================
# 來回組合設定
# ============================================

# 自動組合「去程＋回程」時，回程需在去程之後、間隔至少 MIN_TRIP_DAYS 天、
# 且不超過 MAX_TRIP_DAYS 天
MIN_TRIP_DAYS = 2
MAX_TRIP_DAYS = 30

# 通知裡的來回組合從哪邊出發：
#   "JP" = 從日本出發，去台灣再回日本（2026-10 起住日本）
#   "TW" = 從台灣出發，去日本再回台灣
# 儀表板的「組合」也會跟著打開這一邊（頁面上的「台灣出發／日本出發」按鈕隨時可以換）
COMBO_DEPART = "JP"

# 好價通知裡附的官網首頁（Google 給的航空公司名稱 → 官網）。轉機的用第一段的航空公司；
# 沒列到的就不附。2026-11-19 網址都確認過存在（有些官網擋程式，瀏覽器開是正常的）
AIRLINE_SITES = {
    "China Airlines": "https://www.china-airlines.com/",
    "EVA Air": "https://www.evaair.com/",
    "STARLUX Airlines": "https://www.starlux-airlines.com/",
    "Tigerair Taiwan": "https://www.tigerairtw.com/",
    "Peach Aviation": "https://www.flypeach.com/",
    "Jetstar": "https://www.jetstar.com/",
    "Scoot": "https://www.flyscoot.com/",
    "Hong Kong Express": "https://www.hkexpress.com/",
    "Greater Bay Airlines": "https://www.greaterbay-airlines.com/",
    "Cathay Pacific": "https://www.cathaypacific.com/",
    "JAL": "https://www.jal.co.jp/",
    "ANA": "https://www.ana.co.jp/",
    "Jin Air": "https://www.jinair.com/",
    "Jeju Air": "https://www.jejuair.net/",
    "T'Way Air": "https://www.trinityairways.com/",
    "Trinity Airways": "https://www.trinityairways.com/",   # 德威航空改名
    "Asiana Airlines": "https://flyasiana.com/",
    "Korean Air": "https://www.koreanair.com/",
    "AirAsia": "https://www.airasia.com/",
    "Thai Lion Air": "https://www.lionairthai.com/",
    "Thai Vietjet Air": "https://www.vietjetair.com/",
    "Philippine Airlines": "https://www.philippineairlines.com/",
    "Cebu Pacific": "https://www.cebupacificair.com/",
    "China Southern": "https://www.csair.com/",
}

# 儀表板網址（附在每則通知結尾）
DASHBOARD_URL = "https://JP-study-list.github.io/flight-bot/"

# 歷史資料保留天數（供儀表板趨勢圖使用）
HISTORY_KEEP_DAYS = 90
