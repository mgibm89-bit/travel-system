"""
Prompt 8: бүх системийг шалгах скрипт.

Ажиллуулах (backend асаалттай үед):
    .venv\\Scripts\\python test_system.py

Туршилтаар нэмсэн/өөрчилсөн бүх мөрийг эцэст нь буцааж цэвэрлэнэ.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()
API = os.getenv("TEST_API_URL", "http://localhost:8000")
ADMIN_EMAIL = os.getenv("TEST_ADMIN_EMAIL", "admin@travel.mn")
ADMIN_PASSWORD = os.getenv("TEST_ADMIN_PASSWORD")  # нууц үгийг кодонд бичихгүй: .env-ээс
ROOT = Path(__file__).parent

results = []  # (үйлдэл, хүлээгдэх, бодит, тэнцсэн эсэх)


def check(action, expected, actual, ok):
    results.append((action, expected, str(actual), bool(ok)))


def call(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


# ---------------------------------------------------------------- 0. Код
def scan_code():
    files = [ROOT / "main.py", ROOT.parent / "travel-frontend" / "app.js",
             ROOT.parent / "travel-frontend" / "index.html"]
    # Нууц үгийн жинхэнэ утгыг .env-ээс авч шалгана (кодонд бичихгүй)
    secrets = [s for s in (os.getenv("POSTGRES_PASSWORD"), ADMIN_PASSWORD) if s]
    parts = [r"localhost", r"127\.0\.0\.1", r"postgresql://"] + [re.escape(s) for s in secrets]
    bad = re.compile("|".join(parts), re.I)
    hits = []
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if bad.search(line):
                hits.append(f"{f.name}:{n}")
    check("Кодонд localhost, нууц үг", "байхгүй (зөвхөн .env, config.js)",
          "байхгүй" if not hits else ", ".join(hits), not hits)


def main():
    scan_code()

    # ------------------------------------------------------------ 1. Нэвтрэлт
    st, r = call("POST", "/login", {"email": ADMIN_EMAIL, "password": "буруу-нууц-үг"})
    check("Буруу нууц үг", "«И-мэйл эсвэл нууц үг буруу»", f"{st} {r.get('detail')}",
          st == 401 and r.get("detail") == "И-мэйл эсвэл нууц үг буруу")

    st, r = call("POST", "/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if st != 200:
        print("Зөв нууц үгээр нэвтэрч чадсангүй, шалгалт зогслоо:", st, r)
        sys.exit(1)
    token = r["access_token"]
    check("Зөв нууц үг", "токен", "токен ирсэн", True)

    # ------------------------------------------------------------ 2. Хяналт
    st, s = call("GET", "/admin/stats", token=token)
    got = f"{s['total_tours']} · {s['total_bookings']} · {s['pending_bookings']} · {s['total_revenue']:,} ₮"
    check("Хяналт", "150 · 1227 · 10 · 4,622,330,000 ₮", got,
          got == "150 · 1227 · 10 · 4,622,330,000 ₮")

    # ------------------------------------------------------------ 3. 7 цэс
    html = (ROOT.parent / "travel-frontend" / "index.html").read_text(encoding="utf-8")
    pages = re.findall(r'data-page="(\w+)"', html)
    check("7 цэс", "7", f"{len(pages)}: {', '.join(pages)}", len(pages) == 7)

    created = {}
    db = psycopg.connect(os.getenv("DATABASE_URL"), autocommit=True)
    try:
        # -------------------------------------------------------- 4. Нэмэх, засах
        specs = [
            ("guides", "guide_id", {"full_name": "Тест Хөтөч", "language": "Англи", "experience_years": 5},
             {"full_name": "Тест Хөтөч (зассан)"}, "full_name"),
            ("customers", "customer_id", {"full_name": "Тест Хэрэглэгч", "country": "Монгол", "email": "t@example.mn"},
             {"full_name": "Тест Хэрэглэгч (зассан)"}, "full_name"),
        ]
        for table, idk, body, change, field in specs:
            st, r = call("POST", f"/{table}", body, token)
            new_id = created[table] = r.get(idk)
            _, rows = call("GET", f"/{table}")
            top_ok = rows[0][idk] == new_id
            st2, _ = call("PUT", f"/{table}/{new_id}", {**body, **change}, token)
            _, rows = call("GET", f"/{table}")
            upd_ok = rows[0][field] == change[field]
            check(f"{table}: нэмэх, засах", "шинэ мөр дээр, утга шинэчлэгдэнэ",
                  f"#{new_id} {'дээр' if top_ok else 'ДЭЭР БИШ'}, {'шинэчлэгдсэн' if upd_ok else 'ШИНЭЧЛЭГДЭЭГҮЙ'}",
                  st == 201 and st2 == 200 and top_ok and upd_ok)

        tour = {"name": "Тест аялал", "destination": "Архангай", "days": 4, "price": 990000,
                "start_date": "2026-06-01", "guide_id": created["guides"]}
        st, r = call("POST", "/tours", tour, token)
        created["tours"] = r.get("tour_id")
        _, rows = call("GET", "/tours")
        top_ok = rows[0]["tour_id"] == created["tours"]
        st2, _ = call("PUT", f"/tours/{created['tours']}", {**tour, "price": 1100000}, token)
        _, rows = call("GET", "/tours")
        upd_ok = rows[0]["price"] == 1100000
        check("tours: нэмэх, засах", "шинэ мөр дээр, утга шинэчлэгдэнэ",
              f"#{created['tours']} {'дээр' if top_ok else 'ДЭЭР БИШ'}, үнэ {rows[0]['price']:,.0f}",
              st == 201 and st2 == 200 and top_ok and upd_ok)

        bk = {"tour_id": created["tours"], "customer_id": created["customers"], "people": 2}
        st, r = call("POST", "/bookings", bk, token)
        created["bookings"] = r.get("booking_id")
        _, rows = call("GET", "/bookings")
        top_ok = rows[0]["booking_id"] == created["bookings"]
        st2, _ = call("PUT", f"/bookings/{created['bookings']}", {**bk, "people": 3, "status": "pending"}, token)
        _, rows = call("GET", "/bookings")
        upd_ok = rows[0]["people"] == 3
        check("bookings: нэмэх, засах", "шинэ мөр дээр, утга шинэчлэгдэнэ",
              f"#{created['bookings']} {'дээр' if top_ok else 'ДЭЭР БИШ'}, хүн {rows[0]['people']}",
              st == 201 and st2 == 200 and top_ok and upd_ok)

        # -------------------------------------------------------- 5. Устгах (энгийн)
        st, _ = call("DELETE", f"/bookings/{created['bookings']}", token=token)
        ok = st == 200
        if ok:
            created.pop("bookings")
        check("Төлбөргүй захиалга устгах", "устгагдана", st, ok)

        # -------------------------------------------------------- 6. Холбоотой мөр
        st1, r1 = call("DELETE", "/tours/1", token=token)    # Хөвсгөлийн аялал
        st2, r2 = call("DELETE", "/guides/1", token=token)   # Ганбат
        check("Хөвсгөл, Ганбат устгах", "холбоотой тул болохгүй",
              f"{st1} / {st2}: {r1.get('detail')} | {r2.get('detail')}", st1 == 409 and st2 == 409)

        # -------------------------------------------------------- 7. Төлбөртэй захиалга
        paid = db.execute("SELECT booking_id FROM payments ORDER BY booking_id LIMIT 1").fetchone()[0]
        st, r = call("DELETE", f"/bookings/{paid}", token=token)
        check("Төлбөртэй захиалга устгах", "«Цуцлах»-ыг ашигла",
              f"{st} {r.get('detail')}", st == 409 and "Цуцлах" in r.get("detail", ""))

        # -------------------------------------------------------- 8. Батлах
        pending_id = db.execute(
            "SELECT booking_id FROM bookings WHERE status='pending' ORDER BY booking_id LIMIT 1").fetchone()[0]
        before = call("GET", "/admin/stats", token=token)[1]["pending_bookings"]
        call("PATCH", f"/bookings/{pending_id}/status", {"status": "confirmed"}, token)
        after = call("GET", "/admin/stats", token=token)[1]["pending_bookings"]
        created["confirmed"] = pending_id
        check("Батлах", "Хүлээгдэж буй 10 → 9", f"{before} → {after}", before == 10 and after == 9)

        # -------------------------------------------------------- 9. Эрэлт
        st, r = call("GET", "/predict-demand?price_per_day=180000&guide_experience=12&days=5")
        check("Эрэлт: 180,000 · 12 · 5", "12.5 захиалга", r.get("predicted_bookings"),
              r.get("predicted_bookings") == 12.5)

        # -------------------------------------------------------- 10. Токенгүй
        st, r = call("POST", "/tours", tour)
        check("Токенгүй POST", "401", f"{st} {r.get('detail')}", st == 401)

        # -------------------------------------------------------- 11. Health
        st, r = call("GET", "/health")
        check("/health", "{status: ok}", r, r == {"status": "ok"})

    finally:
        # -------------------------------------------------------- Цэвэрлэх
        if "confirmed" in created:
            db.execute("DELETE FROM payments WHERE booking_id = %s", (created["confirmed"],))
            db.execute("UPDATE bookings SET status='pending' WHERE booking_id = %s", (created["confirmed"],))
        if "bookings" in created:
            db.execute("DELETE FROM payments WHERE booking_id = %s", (created["bookings"],))
            db.execute("DELETE FROM bookings WHERE booking_id = %s", (created["bookings"],))
        for table, idk in [("tours", "tour_id"), ("customers", "customer_id"), ("guides", "guide_id")]:
            if created.get(table):
                db.execute(f"DELETE FROM {table} WHERE {idk} = %s", (created[table],))
        for table, idk in [("payments", "payment_id"), ("bookings", "booking_id"), ("tours", "tour_id"),
                           ("customers", "customer_id"), ("guides", "guide_id")]:
            db.execute(f"SELECT setval('{table}_{idk}_seq', (SELECT MAX({idk}) FROM {table}))")
        final = db.execute(
            "SELECT (SELECT COUNT(*) FROM tours), (SELECT COUNT(*) FROM bookings),"
            " (SELECT COUNT(*) FROM bookings WHERE status='pending'),"
            " (SELECT SUM(amount)::bigint FROM payments)").fetchone()
        db.close()

    # ------------------------------------------------------------ Тайлан
    w = max(len(a) for a, *_ in results)
    print()
    for action, expected, actual, ok in results:
        print(f"{'OK  ' if ok else 'FAIL'}  {action:<{w}}  | хүлээгдэх: {expected}\n{'':6}{'':<{w}}  | бодит:     {actual}")
    passed = sum(ok for *_, ok in results)
    print(f"\n{passed}/{len(results)} тэнцсэн")
    print(f"Цэвэрлэсний дараа сан: {final[0]} · {final[1]} · {final[2]} · {final[3]:,} ₮")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
