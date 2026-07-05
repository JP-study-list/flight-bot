"""
機票低價監控機器人（單程版）
- 雙向監控 config.CITY_PAIRS 的單程票（台→日、日→台）
- 單程低於門檻或明顯低於歷史均價時，透過 LINE / Discord 推播
- 自動組合每個城市對「最便宜去程＋回程」（間隔 <= MAX_TRIP_DAYS 天）
- 查價結果寫入 data/price_history.json，供 GitHub Pages 儀表板讀取
"""
import json
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fast_flights import FlightQuery, Passengers, create_query, get_flights

import config
from notifiers import notify

DATA_FILE = Path(__file__).parent.parent / "data" / "price_history.json"


# ---------- 資料存取 ----------

def load_data():
    """讀取資料檔；若不存在或為舊格式，回傳全新結構"""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "history" in data:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"updated_at": None, "latest": [], "history": {}}


def save_data(data):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


# ---------- 日期與查詢 ----------

def generate_dates():
    """產生未來 LOOKAHEAD_WEEKS 週內、指定星期幾的出發日期（ISO 字串）"""
    dates = []
    today = date.today()
    end = today + timedelta(weeks=config.LOOKAHEAD_WEEKS)
    d = today + timedelta(days=1)
    while d <= end:
        if d.weekday() in config.WEEKDAYS:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    return dates


def directional_routes():
    """把城市對展開成雙向的 (出發, 目的) 清單"""
    routes = []
    for tw, jp in config.CITY_PAIRS:
        routes.append((tw, jp))
        routes.append((jp, tw))
    return routes


def search_oneway(origin, destination, depart_date):
    try:
        query = create_query(
            flights=[FlightQuery(date=depart_date, from_airport=origin, to_airport=destination)],
            trip="one-way",
            seat="economy",
            passengers=Passengers(adults=1),
            currency="TWD",
        )
        return get_flights(query)
    except Exception as e:
        print(f"[warn] 查詢失敗 {origin}->{destination} {depart_date}: {e}")
        return None


def extract_all_prices(result):
    """回傳 (航空公司名稱, 價格) 清單。
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
        if price <= 0:
            continue
        flights.append((name, price))
    return flights


def is_china_airlines(name):
    return any(keyword in name for keyword in config.CI_NAME_KEYWORDS)


# ---------- 判斷邏輯 ----------

def route_key(origin, destination):
    return f"{origin}-{destination}"


def evaluate_route(history, key, best_price, best_ci):
    """以「該航線的每日最低價歷史」判斷今天的最低價是否為好價"""
    past = history.get(key, [])
    past_min = [e["min"] for e in past if e.get("min") is not None]
    past_ci = [e["ci"] for e in past if e.get("ci") is not None]

    overall_reason = None
    if best_price is not None:
        if best_price <= config.ONEWAY_PRICE_THRESHOLD:
            overall_reason = f"低於單程門檻 {config.ONEWAY_PRICE_THRESHOLD}"
        elif len(past_min) >= config.MIN_HISTORY_POINTS:
            avg = statistics.mean(past_min)
            if best_price <= avg * config.THRESHOLD_PERCENT:
                overall_reason = f"比歷史均價 {avg:.0f} 便宜超過 {(1 - config.THRESHOLD_PERCENT) * 100:.0f}%"

    ci_reason = None
    if best_ci is not None:
        if best_ci <= config.CI_ONEWAY_PRICE_THRESHOLD:
            ci_reason = f"低於華航單程門檻 {config.CI_ONEWAY_PRICE_THRESHOLD}"
        elif len(past_ci) >= config.MIN_HISTORY_POINTS:
            avg_ci = statistics.mean(past_ci)
            if best_ci <= avg_ci * config.CI_THRESHOLD_PERCENT:
                ci_reason = f"比華航歷史均價 {avg_ci:.0f} 便宜超過 {(1 - config.CI_THRESHOLD_PERCENT) * 100:.0f}%"

    return overall_reason, ci_reason


def best_combo(outbound, inbound):
    """從去程與回程的 (日期, 價格) 清單中，找出總價最低、
    且回程在去程之後、間隔 <= MAX_TRIP_DAYS 的組合"""
    best = None
    for d1, p1 in outbound:
        dt1 = date.fromisoformat(d1)
        for d2, p2 in inbound:
            dt2 = date.fromisoformat(d2)
            gap = (dt2 - dt1).days
            if gap < 1 or gap > config.MAX_TRIP_DAYS:
                continue
            total = p1 + p2
            if best is None or total < best["total"]:
                best = {"out_date": d1, "out_price": p1,
                        "in_date": d2, "in_price": p2,
                        "total": total, "days": gap}
    return best


# ---------- 主流程 ----------

def main():
    data = load_data()
    history = data["history"]
    today_str = date.today().isoformat()
    dates = generate_dates()

    # results[(origin, dest)] = list of {date, price, airline, ci}
    results = {}

    for origin, destination in directional_routes():
        route_results = []
        for depart_date in dates:
            result = search_oneway(origin, destination, depart_date)
            time.sleep(config.QUERY_DELAY_SECONDS)
            flights = extract_all_prices(result)
            if not flights:
                continue
            cheapest_airline, cheapest_price = min(flights, key=lambda x: x[1])
            ci_matches = [p for name, p in flights if is_china_airlines(name)]
            ci_price = min(ci_matches) if ci_matches else None
            route_results.append(
                {"date": depart_date, "price": cheapest_price,
                 "airline": cheapest_airline, "ci": ci_price}
            )
        results[(origin, destination)] = route_results
        if route_results:
            cheapest = min(route_results, key=lambda r: r["price"])
            print(f"[info] {origin}->{destination}: 掃描 {len(route_results)} 天, "
                  f"最低 {cheapest['price']:.0f} ({cheapest['airline']} {cheapest['date']})")
        else:
            print(f"[info] {origin}->{destination}: 無資料")

    # ---- 更新歷史（每航線每日最低價，供趨勢圖） ----
    deals = []
    for (origin, destination), route_results in results.items():
        key = route_key(origin, destination)
        if not route_results:
            continue

        best = min(route_results, key=lambda r: r["price"])
        ci_list = [r for r in route_results if r["ci"] is not None]
        best_ci_entry = min(ci_list, key=lambda r: r["ci"]) if ci_list else None
        best_ci = best_ci_entry["ci"] if best_ci_entry else None

        overall_reason, ci_reason = evaluate_route(history, key, best["price"], best_ci)

        history.setdefault(key, []).append(
            {"c": today_str, "min": best["price"], "ci": best_ci, "al": best["airline"]}
        )
        history[key] = history[key][-config.HISTORY_KEEP_DAYS:]

        if overall_reason or ci_reason:
            deals.append(
                {"origin": origin, "destination": destination,
                 "best": best, "best_ci_entry": best_ci_entry,
                 "overall_reason": overall_reason, "ci_reason": ci_reason}
            )

    # ---- 來回組合（每個城市對） ----
    combos = []
    for tw, jp in config.CITY_PAIRS:
        outbound = [(r["date"], r["price"]) for r in results.get((tw, jp), [])]
        inbound = [(r["date"], r["price"]) for r in results.get((jp, tw), [])]
        combo = best_combo(outbound, inbound)
        if combo:
            combos.append({"tw": tw, "jp": jp, **combo})

    # ---- 寫入資料檔（供儀表板） ----
    latest = []
    for (origin, destination), route_results in results.items():
        for r in route_results:
            latest.append({"o": origin, "d": destination, "date": r["date"],
                           "price": r["price"], "airline": r["airline"], "ci": r["ci"]})
    data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["latest"] = latest
    data["history"] = history
    save_data(data)
    print(f"[info] 已寫入 {len(latest)} 筆最新價格")

    # ---- 通知 ----
    if deals:
        lines = ["✈️ 機票低價提醒（單程）\n"]

        ci_deals = [d for d in deals if d["ci_reason"]]
        if ci_deals:
            lines.append("★ 華航好價")
            for d in ci_deals:
                e = d["best_ci_entry"]
                lines.append(f"{d['origin']}→{d['destination']} {e['date']}："
                             f"{e['ci']:.0f}（{d['ci_reason']}）")
            lines.append("")

        other_deals = [d for d in deals if d["overall_reason"]]
        if other_deals:
            lines.append("▼ 單程好價")
            for d in other_deals:
                b = d["best"]
                lines.append(f"{d['origin']}→{d['destination']} {b['date']}："
                             f"{b['price']:.0f} {b['airline']}（{d['overall_reason']}）")
            lines.append("")

        if combos:
            lines.append(f"▼ 最便宜來回組合（{config.MAX_TRIP_DAYS} 天內）")
            for c in sorted(combos, key=lambda x: x["total"]):
                lines.append(f"{c['tw']}↔{c['jp']}：去 {c['out_date']} {c['out_price']:.0f}"
                             f" + 回 {c['in_date']} {c['in_price']:.0f}"
                             f" ＝ {c['total']:.0f}（{c['days']} 天）")

        notify("\n".join(lines))
    else:
        print("[info] 今天沒有找到符合條件的低價")


if __name__ == "__main__":
    main()
