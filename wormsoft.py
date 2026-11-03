#!/usr/bin/env python3
# Статус платной модели Wormsoft: токены + время до снятия лимита.
# Источник 1: /root/.hermes/logs/agent.log — запросы ассистента (строки "API call #N ... provider=wormsoft").
# Источник 2: база OpenCode — запросы программы oc к wormsoft.
# Время лимита: Hermes пишет "Rate-limit backoff level N: cooldown X s (provider reset)" —
# это ТОЧНОЕ число секунд до снятия лимита, которое Wormsoft сам сообщил в ответе 429.
# Берём последний отказ и считаем окно; если после конца окна были успешные запросы — лимит сбросился.
import glob, json, os, re, sqlite3, time

DB = "/root/.local/share/opencode/opencode.db"
LOGS = sorted(glob.glob("/root/.hermes/logs/agent.log*"))
OUT = "/var/www/site/limits/wormsoft.json"
MSK = 3 * 3600

now_ms = int(time.time() * 1000)
day_start = (now_ms - MSK * 1000) // 86400000 * 86400000 + MSK * 1000  # сегодня 00:00 МСК
win24 = now_ms - 86400000

def blank(): return {"in": 0, "out": 0, "total": 0, "reqs": 0}
def add(d, i, o, t):
    d["in"] += i; d["out"] += o; d["total"] += t; d["reqs"] += 1

pat_ok = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*API call #\d+: model=\S+ provider=wormsoft in=(\d+) out=(\d+) total=(\d+)")
pat_fail = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*API call failed .*error_type=\w+.*provider=wormsoft")
pat_cd = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*Rate-limit backoff level (\d+): cooldown (\d+) s")

def ts(s):  # метки логов — местное время сервера (МСК)
    return int(time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))) * 1000

hermes = {"today": blank(), "24h": blank(), "all": blank()}
cooldowns = []   # (начало_отказа_мс, длительность_мс)
fails = []       # все сбои wormsoft (не только лимитные)
_ok_times = []   # успешные запросы ассистента к wormsoft
for f in LOGS:
    try:
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = pat_ok.search(line)
                if m:
                    t = ts(m.group(1)); i, o, tot = map(int, m.groups()[1:])
                    add(hermes["all"], i, o, tot)
                    if t > win24: add(hermes["24h"], i, o, tot)
                    if t >= day_start: add(hermes["today"], i, o, tot)
                    _ok_times.append(t)
                    continue
                m = pat_cd.search(line)
                if m:
                    cooldowns.append((ts(m.group(1)), int(m.group(3)) * 1000)); continue
                m = pat_fail.search(line)
                if m: fails.append(ts(m.group(1)))
    except OSError:
        pass

# --- программа oc (база OpenCode) ---
oc = {"today": blank(), "24h": blank(), "all": blank()}
try:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute("select time_created, data from message where json_extract(data,'$.role')='assistant' and json_extract(data,'$.providerID')='wormsoft'").fetchall()
    con.close()
    for t, data in rows:
        tok = (json.loads(data).get("tokens") or {})
        i, o = tok.get("input", 0), tok.get("output", 0)
        tot = tok.get("total", i + o)
        add(oc["all"], i, o, tot)
        if t > win24: add(oc["24h"], i, o, tot)
        if t >= day_start: add(oc["today"], i, o, tot)
except Exception:
    pass

# --- окно лимита: последний отказ + точный cooldown от Wormsoft ---
cooldowns.sort()
last_err, last_dur = (cooldowns[-1] if cooldowns else (None, None))
blocked_until = (last_err + last_dur) if last_err else None
ok_after = [t for t in _ok_times if blocked_until and t > blocked_until]
if blocked_until and (now_ms >= blocked_until or ok_after):
    status, reset_at = "ok", (ok_after[0] if ok_after else now_ms)  # сброс уже произошёл
else:
    status, reset_at = "limited", blocked_until

out = {
    "updated": now_ms,
    "status": status,
    "reset_at": reset_at,           # когда лимит снят (факт) или будет снят (прогноз)
    "last_limit_error_at": last_err,
    "limit_events_total": len(cooldowns),
    "other_errors_24h": sum(1 for t in fails if t > win24),
    "sources": {"hermes_agent": hermes, "opencode_program": oc},
    "total_today": {k: hermes["today"][k] + oc["today"][k] for k in blank()},
    "total_24h": {k: hermes["24h"][k] + oc["24h"][k] for k in blank()},
    "total_all": {k: hermes["all"][k] + oc["all"][k] for k in blank()},
}
tmp = OUT + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
os.replace(tmp, OUT)
print(json.dumps({k: out[k] for k in ("status", "reset_at", "total_today", "total_24h", "total_all")}, ensure_ascii=False))
