import os, re, time, sqlite3
import requests, yaml
from bs4 import BeautifulSoup

DB_PATH = "seen.db"

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
      CREATE TABLE IF NOT EXISTS seen(
        k TEXT PRIMARY KEY,
        first_seen INTEGER,
        last_seen INTEGER
      )
    """)
    con.commit()
    con.close()

def is_new(key: str) -> bool:
    now = int(time.time())
    con = sqlite3.connect(DB_PATH)
    cur = con.execute("SELECT k FROM seen WHERE k=?", (key,))
    row = cur.fetchone()
    if row:
        con.execute("UPDATE seen SET last_seen=? WHERE k=?", (now, key))
        con.commit()
        con.close()
        return False
    con.execute("INSERT INTO seen(k, first_seen, last_seen) VALUES(?,?,?)", (key, now, now))
    con.commit()
    con.close()
    return True

def tg_send(text: str):
    token = os.getenv("TG_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=20)

def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return soup.get_text(" ", strip=True)

def scan_elal(cfg):
    max_price = cfg["rules"]["flights"]["max_price_usd"]
    for t in cfg["targets"]["flights"]:
        try:
            text = fetch_text(t["url"])
        except:
            continue

        prices = [int(x) for x in re.findall(r"\$\s*([0-9]{2,4})", text)]
        if not prices:
            continue

        min_price = min(prices)
        if min_price <= max_price:
            key = f"elal:{t['url']}:{min_price}"
            if is_new(key):
                tg_send(
                    f"✈️ EL AL דיל נמצא\n"
                    f"מחיר: ${min_price}\n"
                    f"{t['name']}\n"
                    f"{t['url']}"
                )

def main():
    init_db()
    tg_send("✅ Deal Agent test: Telegram connected")
    cfg = yaml.safe_load(open("config.yaml", "r", encoding="utf-8"))
    scan_elal(cfg)

if __name__ == "__main__":
    main()
