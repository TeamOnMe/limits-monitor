#!/usr/bin/env python3
# Сборщик статуса бесплатных лимитов OpenCode Zen.
# Считает вызовы бесплатных моделей по базе OpenCode (скользящее окно 24 часа),
# берёт время сброса из лога фолбэк-плагина и пишет status.json для сайта.
import json, sqlite3, time, os, re

DB = "/root/.local/share/opencode/opencode.db"
FB_LOG = "/root/.config/opencode/opencode-fallback.log"
OUT = "/var/www/site/limits/status.json"
LIMIT = 200  # оценка: примерно 200 запросов за 24 часа (сервер не отдаёт точное число)
MSK = 3 * 3600 * 1000

now_ms = int(time.time() * 1000)
day_start_utc = (now_ms - MSK) // 86400000 * 86400000 + MSK  # начало суток по МСК

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = con.execute(
    "select time_created, data from message where json_extract(data,'$.role')='assistant' "
    "and json_extract(data,'$.providerID') in ('opencode','wormsoft')"
).fetchall()
con.close()

# Каждое assistant-сообщение = один запрос к модели.
# Бесплатная модель отдаёт нулевые токены, поэтому считаем все сообщения,
# а неудачные (ошибка) показываем отдельно.
free_ts, free_err_ts, worm_ts = [], [], []
for ts, data in rows:
    try:
        j = json.loads(data)
    except Exception:
        continue
    prov = j.get("providerID")
    has_err = bool(j.get("error"))
    if prov == "opencode":
        (free_err_ts if has_err else free_ts).append(ts)
    elif prov == "wormsoft":
        worm_ts.append(ts)

free_24 = [t for t in free_ts if t > now_ms - 86400000]
worm_24 = [t for t in worm_ts if t > now_ms - 86400000]
free_today = [t for t in free_ts if t >= day_start_utc]
free_err_24 = [t for t in free_err_ts if t > now_ms - 86400000]

# --- лог фолбэка: попытки, упёршиеся в лимит, и время сброса ---
blocked_today = 0
last_block_ms = 0
reset_at = None
pat_ts = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)Z\]")
pat_retry = re.compile(r'"nextRetryMs":\s*(\d+)')
pat_block = re.compile(r"Free usage exceeded|Free usage limit|Rate limit exceeded")
try:
    with open(FB_LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not pat_block.search(line):
                continue
            m = pat_ts.match(line)
            if not m:
                continue
            t_ms = int(time.mktime(time.strptime(m.group(1)[:19], "%Y-%m-%dT%H:%M:%S"))) * 1000
            if t_ms >= day_start_utc:
                blocked_today += 1
            if t_ms > last_block_ms:
                last_block_ms = t_ms
                r = pat_retry.search(line)
                if r:
                    reset_at = int(r.group(1))
except FileNotFoundError:
    pass

# последняя удачная бесплатная попытка
last_free_ok = max(free_ts) if free_ts else 0
limit_active = last_block_ms > last_free_ok

free_sorted = sorted(free_24)
first_slot_at = (free_sorted[0] + 86400000) if free_sorted else None
full_reset_at = (free_sorted[-1] + 86400000) if free_sorted else None

status = {
    "updated": now_ms,
    "limit": LIMIT,
    "used_24h": len(free_24),
    "remaining_24h": max(0, LIMIT - len(free_24)),
    "used_today": len(free_today),
    "free_errors_24h": len(free_err_24),
    "blocked_today": blocked_today,
    "limit_active": limit_active,
    "last_block": last_block_ms or None,
    "reset_at": reset_at if (reset_at and reset_at > now_ms - 86400000) else None,
    "first_slot_at": first_slot_at,
    "full_reset_at": full_reset_at,
    "wormsoft_24h": len(worm_24),
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
tmp = OUT + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(status, f, ensure_ascii=False)
os.replace(tmp, OUT)
print(json.dumps(status, ensure_ascii=False))
