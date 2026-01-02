import os, re, time, datetime, sqlite3
import requests, yaml
from bs4 import BeautifulSoup
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

def parse_points_from_text(text: str):
    """
    מחלץ מספרי נקודות מהטקסט (למשל '45,000 Points')
    """
    pts = []
    for m in re.finditer(r"(\d{1,3}(?:,\d{3})+)\s*Points", text, flags=re.I):
        pts.append(int(m.group(1).replace(",", "")))
    return pts

def hilton_search_points_min(city: str, checkin: str, nights: int = 1) -> int | None:
    """
    ניסיון 'פשוט אבל עובד': לבצע חיפוש בהילטון עם Use Points ולחלץ מינימום נקודות שמופיע בתוצאות.
    checkin בפורמט YYYY-MM-DD
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1) פותחים את האתר
        page.goto("https://www.hilton.com/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1200)

        # 2) יעד
        # טיפ: בהילטון לפעמים יש כמה תיבות; אנחנו מחפשים אחת שמקבלת טקסט יעד.
        dest_box = page.get_by_role("textbox").first
        dest_box.click()
        dest_box.fill(city)
        page.wait_for_timeout(800)
        page.keyboard.press("Enter")
        page.wait_for_timeout(800)

        # 3) פתיחת בחירת תאריכים
        # ננסה ללחוץ על אזור התאריכים; אם ה-UI משתנה, זה המקום היחיד שיכול לדרוש התאמה.
        # לרוב יש כפתור/שדה בשם Dates.
        try:
            page.get_by_text(re.compile(r"Dates|Check-in", re.I)).first.click()
        except:
            # fallback: לוחצים על כפתור כללי ראשון שנראה כמו תאריכים
            page.keyboard.press("Tab")
            page.keyboard.press("Enter")

        page.wait_for_timeout(1200)

        # 4) בחירת תאריך צ׳ק-אין (בדרך כלל אפשר להקליד)
        # הרבה פעמים יש input פנימי; ננסה להקליד את התאריך וללחוץ Enter.
        try:
            date_input = page.get_by_role("textbox").nth(1)
            date_input.fill(checkin)
            page.keyboard.press("Enter")
        except:
            pass

        # 5) Use Points (Special rates)
        # ננסה למצוא Toggle/כפתור "Use Points"
        page.wait_for_timeout(1200)
        try:
            page.get_by_text(re.compile(r"Use Points", re.I)).click()
        except:
            # לפעמים זה נמצא תחת Special rates
            try:
                page.get_by_text(re.compile(r"Special rates", re.I)).click()
                page.wait_for_timeout(600)
                page.get_by_text(re.compile(r"Use Points", re.I)).click()
            except:
                pass

        # 6) חיפוש
        page.wait_for_timeout(800)
        try:
            page.get_by_role("button", name=re.compile(r"Find|Search", re.I)).click()
        except:
            # fallback - Enter
            page.keyboard.press("Enter")

        # 7) מחכים לתוצאות ומחלצים נקודות
        page.wait_for_timeout(6000)
        html = page.content()
        browser.close()

    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    pts = parse_points_from_text(text)
    if not pts:
        return None
    return min(pts)

def generate_dates_for_month(month_yyyy_mm: str):
    """
    כדי להיות יציבים ולא כבדים מדי: נבדוק 4 תאריכים בחודש (1, 8, 15, 22).
    """
    y, m = map(int, month_yyyy_mm.split("-"))
    days = [1, 8, 15, 22]
    out = []
    for d in days:
        try:
            dt = datetime.date(y, m, d)
            out.append(dt.isoformat())
        except:
            pass
    return out

def main():
    init_db()
    cfg = yaml.safe_load(open("config.yaml", "r", encoding="utf-8"))

    max_points = cfg["rules"]["hilton"]["max_points_per_night"]
    months = cfg["months"]
    cities = cfg["targets"]["hilton_cities"]

    hits = 0

    for city in cities:
        for month in months:
            for checkin in generate_dates_for_month(month):
                try:
                    min_pts = hilton_search_points_min(city, checkin, nights=1)
                except Exception:
                    min_pts = None

                if min_pts is None:
                    continue

                if min_pts <= max_points:
                    key = f"hilton:{city}:{month}:{checkin}:{min_pts}"
                    if is_new(key):
                        hits += 1
                        tg_send(
                            "🏨 Hilton Points דיל נמצא\n"
                            f"עיר: {city}\n"
                            f"תאריך בדיקה: {checkin}\n"
                            f"מינ׳ נק׳ שזוהו: {min_pts:,}\n"
                            f"סף שלך: {max_points:,}"
                        )

      if hits == 0:
        # הודעת סטטוס קצרה פעם ביום כדי לוודא שהסוכן חי וחילץ נתונים
        tg_send("🏨 Hilton scan: no deals ≤ 50K today (agent is running)")

    main()
