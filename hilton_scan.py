import os, re, time, sqlite3, datetime
import requests, yaml

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

def build_hilton_search_url(query: str, arrival: str, departure: str) -> str:
    # הסרנו sessionToken בכוונה — הוא לא יציב.
    return (
        "https://www.hilton.com/en/search/"
        f"?query={query}"
        f"&arrivalDate={arrival}"
        f"&departureDate={departure}"
        "&flexibleDates=false"
        "&numRooms=1"
        "&numAdults=1"
        "&numChildren=0"
        "&room1ChildAges="
        "&room1AdultAges="
        "&redeemPts=true"
    )

def extract_points_candidates(text: str) -> list[int]:
    # דוגמאות נפוצות שמופיעות בטקסט של העמוד:
    # "45,000 Points" / "From 45,000 Points"
    pts = []
    for m in re.finditer(r"(\d{1,3}(?:,\d{3})+)\s*Points", text, flags=re.I):
        pts.append(int(m.group(1).replace(",", "")))
    return pts

def fetch_min_points_for_search(url: str) -> int | None:
    r = requests.get(url, timeout=45, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    text = r.text
    pts = extract_points_candidates(text)
    if not pts:
        return None
    return min(pts)

def month_probe_dates(month_yyyy_mm: str) -> list[str]:
    # כדי לכסות את החודש בלי להיות כבדים: 1, 8, 15, 22, 28
    y, m = map(int, month_yyyy_mm.split("-"))
    days = [1, 8, 15, 22, 28]
    out = []
    for d in days:
        try:
            out.append(datetime.date(y, m, d).isoformat())
        except ValueError:
            pass
    return out

def add_days(date_iso: str, days: int) -> str:
    y, m, d = map(int, date_iso.split("-"))
    dt = datetime.date(y, m, d) + datetime.timedelta(days=days)
    return dt.isoformat()

def main():
    init_db()
    cfg = yaml.safe_load(open("config.yaml", "r", encoding="utf-8"))

    max_points = cfg["rules"]["hilton"]["max_points_per_night"]
    months = cfg["months"]
    queries = cfg["targets"]["hilton_queries"]

    found_any = False
    for q in queries:
        for month in months:
            for arrival in month_probe_dates(month):
                departure = add_days(arrival, 1)
                url = build_hilton_search_url(q, arrival, departure)

                try:
                    min_pts = fetch_min_points_for_search(url)
                except Exception:
                    min_pts = None

                if min_pts is None:
                    continue

                # רק אם באמת מתחת לסף שלך
                if min_pts <= max_points:
                    key = f"hilton:{q}:{arrival}:{min_pts}"
                    if is_new(key):
                        found_any = True
                        tg_send(
                            "🏨 Hilton Points דיל\n"
                            f"יעד: {q}\n"
                            f"תאריך: {arrival}\n"
                            f"מינ׳ נק׳ שזוהו: {min_pts:,}\n"
                            f"קישור: {url}"
                        )

    # שקט מוחלט אם אין דילים — כמו שביקשת
    if not found_any:
        pass

if __name__ == "__main__":
    main()
