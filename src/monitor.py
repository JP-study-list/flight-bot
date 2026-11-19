"""
機票低價監控機器人（單程版）
- 雙向監控 config.CITY_PAIRS 的單程票（台→日、日→台）
- 單程明顯低於該航線最近的一般價時，透過 LINE / Discord 推播（通知過、沒再降就不重發）
- 自動組合每個城市對「最便宜去程＋回程」（間隔 <= MAX_TRIP_DAYS 天）
- 查價結果寫入 data/price_history.json，供 GitHub Pages 儀表板讀取
"""
import json
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fast_flights import FlightQuery, Passengers, create_query
from fast_flights.fetcher import fetch_flights_html
from selectolax.lexbor import LexborHTMLParser

import config
from notifiers import notify

DATA_FILE = Path(__file__).parent.parent / "data" / "price_history.json"

# 歷史紀錄的算法版本。2 = 2026-11-19 起連 Google「推薦航班」那段一起讀，
# 價格比之前低，相對門檻只跟同一版的紀錄比
DATA_VERSION = 2


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


class QueryFailed(Exception):
    pass


def search_oneway(origin, destination, depart_date):
    """查一天的單程票，回傳 [(航空公司名稱, 價格), ...]；查詢失敗回傳 None"""
    try:
        query = create_query(
            flights=[FlightQuery(date=depart_date, from_airport=origin, to_airport=destination)],
            trip="one-way",
            seat="economy",
            passengers=Passengers(adults=1),
            currency="TWD",
        )
        return extract_all_prices(fetch_flights_html(query))
    except Exception as e:
        print(f"[warn] 查詢失敗 {origin}->{destination} {depart_date}: {e}")
        return None


def extract_all_prices(html):
    """從 Google 機票頁面取出 (航空公司名稱, 價格) 清單。
    Google 把結果分成上面的「推薦航班」（payload[2]）和下面的「其他航班」（payload[3]），
    最便宜的常在上面那段；fast-flights 3.1.0 只讀下面那段，所以這裡自己讀兩段。
    單一航班缺欄位就跳過那一班，不讓整天的查詢失敗。"""
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise QueryFailed("頁面裡沒有航班資料")
    data = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        raise QueryFailed("no flights found; received error")
    payload = json.loads(data)

    flights = []
    for section in (2, 3):
        try:
            items = payload[section][0] or []
        except (IndexError, TypeError):
            continue
        for item in items:
            try:
                name = " / ".join(item[0][1] or ["Unknown"])
                price = float(item[1][0][1])
            except (IndexError, TypeError, ValueError):
                continue
            if price > 0:
                flights.append((name, price))
    return flights


def is_china_airlines(name):
    return any(keyword in name for keyword in config.CI_NAME_KEYWORDS)


def official_site(name):
    """航空公司官網首頁；轉機（「A / B」）用第一段的，沒列到回傳 None"""
    return config.AIRLINE_SITES.get(name.split(" / ")[0].strip())


# ---------- 判斷邏輯 ----------

def route_key(origin, destination):
    return f"{origin}-{destination}"


def recent_typical(past, field):
    """該航線最近 RELATIVE_WINDOW 天（同一版算法）每日最低價的中位數；筆數不夠回傳 None"""
    values = [e[field] for e in past
              if e.get("v") == DATA_VERSION and e.get(field) is not None]
    values = values[-config.RELATIVE_WINDOW:]
    if len(values) < config.MIN_HISTORY_POINTS:
        return None
    return statistics.median(values)


def deal_reason(price, past, field, absolute, percent, label):
    if price is None:
        return None
    if absolute and price <= absolute:
        return f"低於{label}門檻 {absolute}"
    typical = recent_typical(past, field)
    if typical and price <= typical * percent:
        return f"比{label}最近一般價 {typical:.0f} 便宜 {(1 - price / typical) * 100:.0f}%"
    return None


def evaluate_route(history, key, best_price, best_ci):
    """以「該航線的每日最低價歷史」判斷今天的最低價是否為好價"""
    past = history.get(key, [])
    overall_reason = deal_reason(best_price, past, "min", config.ONEWAY_PRICE_THRESHOLD,
                                 config.THRESHOLD_PERCENT, "")
    ci_reason = deal_reason(best_ci, past, "ci", config.CI_ONEWAY_PRICE_THRESHOLD,
                            config.CI_THRESHOLD_PERCENT, "華航")
    return overall_reason, ci_reason


def not_repeated(alerted, tag, reason, price):
    """同一條航線通知過、價格沒有更低就不再發；不再是好價時清掉紀錄，下次便宜再發"""
    if not reason:
        alerted.pop(tag, None)
        return None
    last = alerted.get(tag)
    if last is not None and price >= last:
        return None
    alerted[tag] = price
    return reason


def best_combo(outbound, inbound):
    """從去程與回程的 (日期, 價格) 清單中，找出總價最低、
    且回程在去程之後、間隔 <= MAX_TRIP_DAYS 的組合"""
    best = None
    for d1, p1 in outbound:
        dt1 = date.fromisoformat(d1)
        for d2, p2 in inbound:
            dt2 = date.fromisoformat(d2)
            gap = (dt2 - dt1).days
            if gap < config.MIN_TRIP_DAYS or gap > config.MAX_TRIP_DAYS:
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

    # found[(origin, dest)][date] = [(航空公司名稱, 價格), ...]
    routes = directional_routes()
    found = {route: {} for route in routes}
    failed = []
    for origin, destination in routes:
        for depart_date in dates:
            flights = search_oneway(origin, destination, depart_date)
            time.sleep(config.QUERY_DELAY_SECONDS)
            if flights is None:
                failed.append((origin, destination, depart_date))
            elif flights:
                found[(origin, destination)][depart_date] = flights

    # 第一輪查不到的，最後放慢再查一次
    total_queries = len(routes) * len(dates)
    still_failed = 0
    if failed:
        print(f"[info] 第一輪查不到 {len(failed)}／{total_queries} 次，放慢再查一次")
        for origin, destination, depart_date in failed:
            flights = search_oneway(origin, destination, depart_date)
            time.sleep(config.RETRY_DELAY_SECONDS)
            if flights is None:
                still_failed += 1
            elif flights:
                found[(origin, destination)][depart_date] = flights
    fail_rate = still_failed / total_queries if total_queries else 0
    print(f"[info] 重查後仍查不到 {still_failed}／{total_queries} 次（{fail_rate:.0%}）")

    for origin, destination in routes:
        route_results = []
        for depart_date in dates:
            flights = found[(origin, destination)].get(depart_date)
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
    alerted = data.setdefault("alerted", {})
    for (origin, destination), route_results in results.items():
        key = route_key(origin, destination)
        if not route_results:
            continue

        best = min(route_results, key=lambda r: r["price"])
        ci_list = [r for r in route_results if r["ci"] is not None]
        best_ci_entry = min(ci_list, key=lambda r: r["ci"]) if ci_list else None
        best_ci = best_ci_entry["ci"] if best_ci_entry else None

        overall_reason, ci_reason = evaluate_route(history, key, best["price"], best_ci)
        overall_reason = not_repeated(alerted, key, overall_reason, best["price"])
        ci_reason = not_repeated(alerted, key + "|ci", ci_reason, best_ci)

        history.setdefault(key, []).append(
            {"c": today_str, "min": best["price"], "ci": best_ci, "al": best["airline"],
             "v": DATA_VERSION}
        )
        history[key] = history[key][-config.HISTORY_KEEP_DAYS:]

        if overall_reason or ci_reason:
            deals.append(
                {"origin": origin, "destination": destination,
                 "best": best, "best_ci_entry": best_ci_entry,
                 "overall_reason": overall_reason, "ci_reason": ci_reason}
            )

    # ---- 來回組合（每個城市對，從 COMBO_DEPART 那邊出發） ----
    jp_depart = config.COMBO_DEPART == "JP"
    combos = []
    for tw, jp in config.CITY_PAIRS:
        home, away = (jp, tw) if jp_depart else (tw, jp)
        outbound = [(r["date"], r["price"]) for r in results.get((home, away), [])]
        inbound = [(r["date"], r["price"]) for r in results.get((away, home), [])]
        combo = best_combo(outbound, inbound)
        if combo:
            combos.append({"home": home, "away": away, **combo})

    # ---- 寫入資料檔（供儀表板） ----
    latest = []
    for (origin, destination), route_results in results.items():
        for r in route_results:
            latest.append({"o": origin, "d": destination, "date": r["date"],
                           "price": r["price"], "airline": r["airline"], "ci": r["ci"]})
    data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["latest"] = latest
    data["history"] = history
    prev_fail_rate = data.get("fail_rate") or 0
    data["fail_rate"] = round(fail_rate, 3)
    save_data(data)
    print(f"[info] 已寫入 {len(latest)} 筆最新價格")

    # ---- 通知 ----
    dashboard = config.DASHBOARD_URL + ("?rg=JP" if jp_depart else "")
    fail_line = None
    if fail_rate > config.FAIL_ALERT_RATE:
        fail_line = (f"注意：今天有 {fail_rate:.0%} 的日期查不到價格，"
                     "價格和好價判斷可能不準")

    if deals:
        lines = ["✈️ 機票低價提醒（單程）\n"]

        ci_deals = [d for d in deals if d["ci_reason"]]
        if ci_deals:
            lines.append("★ 華航好價")
            for d in ci_deals:
                e = d["best_ci_entry"]
                lines.append(f"{d['origin']}→{d['destination']} {e['date']}："
                             f"{e['ci']:.0f}（{d['ci_reason']}）")
                lines.append(f"  官網 {official_site('China Airlines')}")
            lines.append("")

        other_deals = [d for d in deals if d["overall_reason"]]
        if other_deals:
            lines.append("▼ 單程好價")
            for d in other_deals:
                b = d["best"]
                lines.append(f"{d['origin']}→{d['destination']} {b['date']}："
                             f"{b['price']:.0f} {b['airline']}（{d['overall_reason']}）")
                site = official_site(b["airline"])
                if site:
                    lines.append(f"  官網 {site}")
            lines.append("")

        if combos:
            depart_label = "日本出發" if jp_depart else "台灣出發"
            lines.append(f"▼ 最便宜來回組合（{depart_label}，"
                         f"{config.MIN_TRIP_DAYS}–{config.MAX_TRIP_DAYS} 天）")
            for c in sorted(combos, key=lambda x: x["total"]):
                lines.append(f"{c['home']}↔{c['away']}：去 {c['out_date']} {c['out_price']:.0f}"
                             f" + 回 {c['in_date']} {c['in_price']:.0f}"
                             f" ＝ {c['total']:.0f}（{c['days']} 天）")
            lines.append("")

        if fail_line:
            lines.append(fail_line)
            lines.append("")

        lines.append(f"📊 完整資料與趨勢：{dashboard}")

        notify("\n".join(lines))
    else:
        print("[info] 今天沒有找到符合條件的低價")
        # 沒有好價時平常不發；查不到的比例剛超過門檻那天發一次，免得壞掉沒人知道
        if fail_line and prev_fail_rate <= config.FAIL_ALERT_RATE:
            notify(f"機票 Bot：{fail_line}\n\n{dashboard}")


if __name__ == "__main__":
    main()
