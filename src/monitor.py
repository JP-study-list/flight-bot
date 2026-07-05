"""
機票低價監控機器人
監控 TPE <-> NRT/HND 未來週末航班的來回票價（不限特定航空公司），
當價格明顯便宜時透過 LINE 推播提醒；若查到華航的價格，一律優先顯示在通知最上面。
"""
import json
import statistics
from datetime import date, timedelta
from pathlib import Path

from fast_flights import FlightQuery, Passengers, create_query, get_flights

import config
from notifiers import notify

HISTORY_FILE = Path(__file__).parent.parent / "data" / "price_history.json"


def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_history(history):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def generate_date_pairs():
    """產生未來 LOOKAHEAD_WEEKS 週內、每個週五出發的來回日期組合"""
    pairs = []
    today = date.today()
    days_until_friday = (4 - today.weekday()) % 7  # 0=Mon ... 4=Fri
    first_friday = today + timedelta(days=days_until_friday or 7)
    for w in range(config.LOOKAHEAD_WEEKS):
        depart = first_friday + timedelta(weeks=w)
        ret = depart + timedelta(days=config.TRIP_LENGTH_DAYS)
        pairs.append((depart.isoformat(), ret.isoformat()))
    return pairs


def search_round_trip(origin, destination, depart_date, return_date):
    try:
        query = create_query(
            flights=[
                FlightQuery(date=depart_date, from_airport=origin, to_airport=destination),
                FlightQuery(date=return_date, from_airport=destination, to_airport=origin),
            ],
            trip="round-trip",
            seat="economy",
            passengers=Passengers(adults=1),
            currency="TWD",   # 價格直接用新台幣回傳
        )
        return get_flights(query)
    except Exception as e:
        print(f"[warn] 查詢失敗 {origin}->{destination} {depart_date}->{return_date}: {e}")
        return None

def extract_all_prices(result):
    """回傳這次查詢結果裡，每個航班的 (航空公司名稱, 價格) 清單
    新版 fast-flights 的結果本身就是清單，每項有 .price（數字）與 .airlines（清單）"""
    if not result:
        return []
    flights = []
    for f in result:
        airlines = getattr(f, "airlines", None) or ["Unknown"]
        name = " / ".join(airlines)
        try:
            price = float(f.price)
        except (TypeError, ValueError):
            continue
        if price <= 0:  # 0 或負值代表無報價
            continue
        flights.append((name, price))
    return flights




def is_china_airlines(name):
    return any(keyword in name for keyword in config.CI_NAME_KEYWORDS)


def evaluate(history, key, cheapest_price, ci_price):
    """比較今天查到的價格跟歷史紀錄，分別判斷「全網最便宜」與「華航」是否為好價格"""
    past = history.get(key, [])
    past_cheapest = [e["cheapest_price"] for e in past if e.get("cheapest_price") is not None]
    past_ci = [e["ci_price"] for e in past if e.get("ci_price") is not None]

    overall_reason = None
    if cheapest_price <= config.ABSOLUTE_PRICE_THRESHOLD:
        overall_reason = f"低於絕對門檻 {config.ABSOLUTE_PRICE_THRESHOLD}"
    elif len(past_cheapest) >= config.MIN_HISTORY_POINTS:
        avg = statistics.mean(past_cheapest)
        if cheapest_price <= avg * config.THRESHOLD_PERCENT:
            overall_reason = f"比歷史均價 {avg:.0f} 便宜超過 {(1 - config.THRESHOLD_PERCENT) * 100:.0f}%"

    ci_reason = None
    if ci_price is not None:
        if ci_price <= config.CI_ABSOLUTE_PRICE_THRESHOLD:
            ci_reason = f"低於華航絕對門檻 {config.CI_ABSOLUTE_PRICE_THRESHOLD}"
        elif len(past_ci) >= config.MIN_HISTORY_POINTS:
            avg_ci = statistics.mean(past_ci)
            if ci_price <= avg_ci * config.CI_THRESHOLD_PERCENT:
                ci_reason = f"比華航歷史均價 {avg_ci:.0f} 便宜超過 {(1 - config.CI_THRESHOLD_PERCENT) * 100:.0f}%"

    return overall_reason, ci_reason


def main():
    history = load_history()
    deals = []
    today_str = date.today().isoformat()

    for origin in config.ORIGINS:
        for destination in config.DESTINATIONS:
            for depart_date, return_date in generate_date_pairs():
                result = search_round_trip(origin, destination, depart_date, return_date)
                flights = extract_all_prices(result)
                if not flights:
                    continue

                cheapest_airline, cheapest_price = min(flights, key=lambda x: x[1])
                ci_matches = [p for name, p in flights if is_china_airlines(name)]
                ci_price = min(ci_matches) if ci_matches else None

                key = f"{origin}_{destination}_{depart_date}_{return_date}"
                overall_reason, ci_reason = evaluate(history, key, cheapest_price, ci_price)

                history.setdefault(key, []).append(
                    {
                        "date_checked": today_str,
                        "cheapest_price": cheapest_price,
                        "cheapest_airline": cheapest_airline,
                        "ci_price": ci_price,
                    }
                )
                history[key] = history[key][-60:]  # 只保留最近 60 筆，避免檔案無限增長

                print(
                    f"[info] {key}: 最便宜={cheapest_airline} {cheapest_price} | "
                    f"華航={ci_price} | overall_deal={bool(overall_reason)} | ci_deal={bool(ci_reason)}"
                )

                if overall_reason or ci_reason:
                    deals.append(
                        {
                            "origin": origin,
                            "destination": destination,
                            "depart": depart_date,
                            "return": return_date,
                            "cheapest_airline": cheapest_airline,
                            "cheapest_price": cheapest_price,
                            "ci_price": ci_price,
                            "overall_reason": overall_reason,
                            "ci_reason": ci_reason,
                        }
                    )

    save_history(history)

    if deals:
        lines = ["✈️ 機票低價提醒！\n"]
        for d in deals:
            lines.append(f"{d['origin']}↔{d['destination']}｜去 {d['depart']} / 回 {d['return']}")

            # 只要查到華航價格，一律優先顯示在最上面
            if d["ci_price"] is not None:
                tag = "（划算！）" if d["ci_reason"] else "（供比較）"
                lines.append(f"★ 華航 {tag}：{d['ci_price']:.0f}")
                if d["ci_reason"]:
                    lines.append(f"　原因：{d['ci_reason']}")

            # 如果全網最便宜的不是華航，也列出來讓你比較
            if not is_china_airlines(d["cheapest_airline"]):
                lines.append(f"最便宜：{d['cheapest_airline']} {d['cheapest_price']:.0f}")
                if d["overall_reason"]:
                    lines.append(f"　原因：{d['overall_reason']}")

            lines.append("")  # 空行分隔

        notify("\n".join(lines))
    else:
        print("[info] 今天沒有找到符合條件的低價")


if __name__ == "__main__":
    main()
