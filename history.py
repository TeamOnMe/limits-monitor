#!/usr/bin/env python3
# Сборщик дашборда: история запросов к бесплатным/платным моделям по часам (7 дней)
# + разбивка по моделям. Пишет history.json рядом со status.json.
import json, sqlite3, time, os

DB = "/root/.local/share/opencode/opencode.db"
OUT = "/var/www/site/limits/history.json"
HOURS = 24 * 7  # окно истории — неделя

now_ms = int(time.time() * 1000)
hour_start = now_ms // 3600000 * 3600000
win_start = hour_start - HOURS * 3600000

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = con.execute(
    "select time_created, data from message where json_extract(data,'$.role')='assistant' "
    "and time_created >= ?", (win_start,)
).fetchall()
con.close()

hours = [{"h": win_start + i * 3600000, "free": 0, "paid": 0} for i in range(HOURS)]
models = {}
for ts, data in rows:
    try:
        j = json.loads(data)
    except Exception:
        continue
    prov = j.get("providerID")
    mid = j.get("modelID") or "?"
    if prov == "opencode" and not j.get("error"):
        idx = (ts - win_start) // 3600000
        if 0 <= idx < HOURS:
            hours[idx]["free"] += 1
        models[mid] = models.get(mid, 0) + 1
    elif prov == "wormsoft":
        idx = (ts - win_start) // 3600000
        if 0 <= idx < HOURS:
            hours[idx]["paid"] += 1

total_free = sum(h["free"] for h in hours)
total_paid = sum(h["paid"] for h in hours)
best_day = max(range(0, HOURS, 24), key=lambda i: sum(h["free"] for h in hours[i:i+24]))
days = [sum(h["free"] for h in hours[best_day + k * 24: best_day + (k + 1) * 24]) for k in range(7)]

out = {
    "updated": now_ms,
    "window_hours": HOURS,
    "hours": hours,
    "totals": {"free_7d": total_free, "paid_7d": total_paid},
    "models": sorted(({"model": m, "count": c} for m, c in models.items()),
                     key=lambda x: -x["count"]),
}
tmp = OUT + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
os.replace(tmp, OUT)
print(json.dumps({"free_7d": total_free, "paid_7d": total_paid, "models": len(models)}))
