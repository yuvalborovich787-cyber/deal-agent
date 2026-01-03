import os, re, time, sqlite3, datetime, urllib.parse
import yaml
import requests
from playwright.sync_api import sync_playwright

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

def month_probe_dates(month_yyyy_mm: str) -> list[str]:
    # 5 דגימות בחודש: 1/8/15/22/28
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

def build_hilton_search_url(query: str, place_id: str, arrival: str, departure: str) -> str:
    return (
        "https://www.hilton.com/en/search/"
        f"?query={urllib.parse.quote(query)}"
        f"&placeId={urllib.parse.quote(place_id)}"
        f"&arrivalDate={arrival}"
        f"&departureDate={departure}"
        "&flexibleDates=false"
        "&numRooms=1"
        "&numAdults=1"
        "&numChildren=0"
        "&room1ChildAges="
        "&room1AdultAges="
        "&specialRateTokens="
        "&sortBy=DISTANCE"
    )

def extract_points(text: str) -> list[int]:
    # "45,000 Points"
    pts = []
    for m in re.finditer(r"(\d{1,3}(?:,\d{3})+)\s*Points", text, flags=re.I):
        pts.append(int(m.group(1).replace(",", "")))
    return pts

def fetch_min_points_playwright(url: str) -> int | None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # מחכים לרינדור של "Points" (אם לא מופיע — יכול להיות שאין תוצאות/אין נק׳ לתאריך הזה)
        try:
            page.wait_for_selector("text=/Points/i", timeout=25000)
        except:
            pass

        # קוראים טקסט לאחר הרינדור
        body_text = page.inner_text("body")
        browser.close()

    pts = extract_points(body_text)
    if not pts:
        return None
    return min(pts)

def main():
    init_db()
    cfg = yaml.safe_load(open("config.yaml", "r", encoding="utf-8"))

    max_points = cfg["rules"]["hilton"]["max_points_per_night"]
    months = cfg["months"]
    places = cfg["targets"]["hilton_places"]

    found = 0

    for key, place in places.items():
        query = place["query"]
        place_id = place["placeId"]

        for month in months:
            for arrival in month_probe_dates(month):
                departure = add_days(arrival, 1)
                url = build_hilton_search_url(query, place_id, arrival, departure)

                try:
                    min_pts = fetch_min_points_playwright(url)
                except Exception as e:
                    print("DEBUG_ERROR", key, arrival, str(e)[:120])
                    continue

                # DEBUG: כדי לוודא שהסוכן באמת רואה נקודות
                print("DEBUG", key, arrival, min_pts)

                if min_pts is None:
                    continue

                if min_pts <= max_points:
                    dedupe_key = f"hilton:{key}:{arrival}:{min_pts}"
                    if is_new(dedupe_key):
                        found += 1
                        tg_send(
                            "🏨 Hilton Points דיל\n"
                            f"יעד: {query}\n"
                            f"תאריך: {arrival}\n"
                            f"מינ׳ נק׳ שזוהו: {min_pts:,}\n"
                            f"סף שלך: {max_points:,}\n"
                            f"קישור: {url}"
                        )

    # שקט מוחלט אם אין דילים
    return

if __name__ == "__main__":
    main()
