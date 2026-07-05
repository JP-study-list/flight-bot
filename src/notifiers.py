"""
通知模組：負責把訊息推播到 LINE 與 Discord。
設計原則：有設定對應金鑰的管道就發送，沒設定的自動跳過（不會報錯中斷）。
- LINE   需要環境變數 LINE_CHANNEL_ACCESS_TOKEN
- Discord 需要環境變數 DISCORD_WEBHOOK_URL
"""
import os

import requests

# 各平台單則訊息的長度上限（保守值，留安全餘裕）
LINE_CHAR_LIMIT = 4900      # LINE 單則 text 上限 5000
DISCORD_CHAR_LIMIT = 1900   # Discord 單則 content 上限 2000


def _split_message(text, limit):
    """把長訊息在空行處切成多段，每段不超過 limit 字元"""
    blocks = text.split("\n\n")
    chunks = []
    current = ""
    for block in blocks:
        candidate = (current + "\n\n" + block) if current else block
        if len(candidate) > limit and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)

    # 保險：若單一區塊本身就超長，硬切
    final = []
    for c in chunks:
        while len(c) > limit:
            final.append(c[:limit])
            c = c[limit:]
        final.append(c)
    return final


def send_line(text):
    """用 LINE 官方帳號的 broadcast API 推播（發給所有加入好友的人）"""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("[info] 未設定 LINE_CHANNEL_ACCESS_TOKEN，跳過 LINE 推播")
        return

    # LINE broadcast 一次最多帶 5 則訊息
    parts = _split_message(text, LINE_CHAR_LIMIT)[:5]
    resp = requests.post(
        "https://api.line.me/v2/bot/message/broadcast",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json={"messages": [{"type": "text", "text": p} for p in parts]},
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"[error] LINE 推播失敗: {resp.status_code} {resp.text}")
    else:
        print("[info] LINE 推播成功")


def send_discord(text):
    """用 Discord Webhook 推播到指定頻道"""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("[info] 未設定 DISCORD_WEBHOOK_URL，跳過 Discord 推播")
        return

    ok = True
    for part in _split_message(text, DISCORD_CHAR_LIMIT):
        resp = requests.post(webhook_url, json={"content": part}, timeout=15)
        # Discord webhook 成功通常回 204（無內容）
        if resp.status_code not in (200, 204):
            print(f"[error] Discord 推播失敗: {resp.status_code} {resp.text}")
            ok = False
    if ok:
        print("[info] Discord 推播成功")


def notify(text):
    """對所有已設定的管道發送通知"""
    send_line(text)
    send_discord(text)
