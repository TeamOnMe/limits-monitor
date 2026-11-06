#!/usr/bin/env python3
# Сборщик расхода Hermes: обращения к Wormsoft напрямую (чат Telegram, кроны, сабагенты).
# Читает /root/.hermes/state.db -> session_model_usage, где custom = Wormsoft (ai.wormsoft.ru).
# ВАЖНО: last_seen в СЕКУНДАХ (unix, дробные). Пишет /var/www/site/limits/hermes.json для дашборда.
import json, sqlite3, time, os

DB = "/root/.hermes/state.db"
OUT = "/var/www/site/limits/hermes.json"
DAYS = 7
MSK = 3 * 3600

now = time.time()
today_start = (now + MSK) // 86400 * 86400 - MSK
win_start = today_start - (DAYS - 1) * 86400
cutoff_s = win_start

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = con.execute(
    """
    select last_seen, billing_provider, billing_base_url, task, api_call_count, input_tokens, output_tokens
    from session_model_usage
    where last_seen >= ?
    """,
    (cutoff_s,),
).fetchall()
con.close()

def bucket(provider, base_url):
    u = (base_url or "") + "|" + (provider or "")
    if "wormsoft" in u:
        return "wormsoft"
    if "pollinations" in u:
        return "pollinations"
    if "bynara" in u:
        return "nara"
    if "127.0.0.1:8000" in u:
        return "local"
    return "other"

days = []
for i in range(DAYS):
    days.append({"day": win_start + i * 86400, "hermes_wormsoft_calls": 0,
                 "hermes_tokens": 0, "pollinations_calls": 0})
models = {}
total_24h = {"calls": 0, "tokens": 0}
cut24 = now - 86400

for last_seen, provider, base_url, task, calls, it, ot in rows:
    b = bucket(provider, base_url)
    ls = float(last_seen)
    day_idx = int(((ls + MSK) // 86400 * 86400 - win_start) // 86400)
    if not (0 <= day_idx < DAYS):
        continue
    toks = (it or 0) + (ot or 0)
    if b == "wormsoft":
        days[day_idx]["hermes_wormsoft_calls"] += calls or 0
        days[day_idx]["hermes_tokens"] += toks
        models["glm-5.3-flash (Hermes)"] = models.get("glm-5.3-flash (Hermes)", 0) + (calls or 0)
    elif b == "pollinations":
        days[day_idx]["pollinations_calls"] += calls or 0
    if ls >= cut24 and b == "wormsoft":
        total_24h["calls"] += calls or 0
        total_24h["tokens"] += toks

out = {
    "updated": int(now * 1000),
    "window_days": DAYS,
    "days": days,
    "models": sorted(({"model": m, "count": c} for m, c in models.items()), key=lambda x: -x["count"]),
    "total_24h": total_24h,
}
tmp = OUT + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
os.replace(tmp, OUT)
print(json.dumps({"hermes_calls_24h": total_24h["calls"], "hermes_tokens_24h": total_24h["tokens"],
                  "days": [(time.strftime('%d.%m', time.gmtime(d["day"])), d["hermes_wormsoft_calls"]) for d in days]}))
