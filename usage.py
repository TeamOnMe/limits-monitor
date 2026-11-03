#!/usr/bin/env python3
# Сборщик фактического расхода бесплатных моделей OpenCode: по дням (МСК) и моделям,
# плюс события «лимит исчерпан» из лога фолбэк-плагина. Пишет usage.json для дашборда.
# Точный лимит сервис не публикует (динамический) — поэтому дашборд показывает
# эмпирику: сколько запросов реально выдержал каждый день до отказа.
import json, sqlite3, time, os, re, calendar

DB = "/root/.local/share/opencode/opencode.db"
FB_LOG = "/root/.config/opencode/opencode-fallback.log"
OUT = "/var/www/site/limits/usage.json"
DAYS = 7
MSK = 3 * 3600 * 1000

now_ms = int(time.time() * 1000)
today_start = (now_ms - MSK) // 86400000 * 86400000 + MSK
win_start = today_start - (DAYS - 1) * 86400000

# --- база OpenCode: успешные/ошибочные ответы моделей ---
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = con.execute(
    "select time_created, data from message "
    "where json_extract(data,'$.role')='assistant' "
    "and json_extract(data,'$.providerID') in ('opencode','wormsoft') "
    "and time_created >= ?", (win_start,)
).fetchall()
con.close()

days = [{"day": win_start + i * 86400000, "free_ok": 0, "free_err": 0, "paid": 0, "models": {}} for i in range(DAYS)]
for ts, data in rows:
    try:
        j = json.loads(data)
    except Exception:
        continue
    di = (ts - win_start) // 86400000
    if not (0 <= di < DAYS):
        continue
    d = days[di]
    prov = j.get("providerID")
    mid = j.get("modelID") or "?"
    if prov == "opencode":
        if j.get("error"):
            d["free_err"] += 1
        else:
            d["free_ok"] += 1
            d["models"][mid] = d["models"].get(mid, 0) + 1
    elif prov == "wormsoft":
        d["paid"] += 1

# --- лог фолбэка: отказы по лимиту, исчерпание всей цепочки, время сброса ---
pat_ts = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.")
pat_limit = re.compile(r'"message":"[^"]*(?:Free usage exceeded|reached the limit|FreeUsageLimit|Free usage limit)[^"]*"')
pat_next = re.compile(r'"nextRetryMs":\s*(\d+)')
pat_chain = re.compile(r"No available fallback models")
pat_linets = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")

blocks = []      # (ts_ms, reset_ms, msg)
chain_dead = []  # ts_ms — когда вся цепочка была в кулдауне
try:
    with open(FB_LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pat_linets.match(line)
            if not m:
                continue
            t_ms = calendar.timegm(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")) * 1000
            if pat_chain.search(line):
                chain_dead.append(t_ms)
                continue
            if pat_limit.search(line):
                nxt = None
                mn = pat_next.search(line)
                if mn:
                    nxt = int(mn.group(1))
                mm = re.search(r'"message":"([^"]*)"', line)
                blocks.append({"ts": t_ms, "reset": nxt, "msg": (mm.group(1)[:80] if mm else "")})
except FileNotFoundError:
    pass

blocks.sort(key=lambda b: b["ts"])
last_block = blocks[-1] if blocks else None

# --- «эмпирический рекорд»: лучший день по успешным бесплатным запросам ---
best = max(days, key=lambda d: d["free_ok"])

out = {
    "updated": now_ms,
    "window_days": DAYS,
    "days": days,
    "blocks": blocks[-100:],
    "chain_dead_count": len(chain_dead),
    "last_block": last_block,
    "best_day": {"day": best["day"], "free_ok": best["free_ok"]},
}
tmp = OUT + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
os.replace(tmp, OUT)
print(json.dumps({
    "days": [(time.strftime("%d.%m", time.gmtime((d["day"] + MSK) / 1000)), d["free_ok"], d["free_err"], d["paid"]) for d in days],
    "blocks_7d": len(blocks),
    "best_day_free_ok": best["free_ok"],
    "chain_dead": len(chain_dead),
}, ensure_ascii=False))
