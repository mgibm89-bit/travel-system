import os
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import bcrypt
import joblib
import jwt
import pandas as pd
import psycopg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg import sql
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

# .env файлаас тохиргоог уншина (кодонд хаяг, нууц үг бичихгүй)
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
MODEL_PATH = os.getenv("MODEL_PATH", "demand_model.joblib")
SECRET_KEY = os.getenv("SECRET_KEY")  # Токен гарын үсэг зурах нууц түлхүүр
TOKEN_EXPIRE_MINUTES = int(os.getenv("TOKEN_EXPIRE_MINUTES", "480"))

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY .env файлд алга")

app = FastAPI(title="Travel Admin API")

# CORS: зөвхөн .env-д заасан frontend хаягууд хандаж чадна
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_conn():
    # dict_row: мөр бүрийг {"tour_id": 1, "name": ...} хэлбэрийн dict болгож буцаана
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


# =====================================================================
# Нэвтрэлт ба токен
# =====================================================================

class LoginIn(BaseModel):
    email: str
    password: str


bearer = HTTPBearer(auto_error=False)


def create_token(admin_id: int, email: str) -> str:
    payload = {
        "sub": str(admin_id),
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def require_admin(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    """Authorization: Bearer <токен> толгойг шалгана. Буруу/дууссан бол 401."""
    if cred is None:
        raise HTTPException(status_code=401, detail="Нэвтрэх шаардлагатай")
    try:
        return jwt.decode(cred.credentials, SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Токен буруу эсвэл хугацаа дууссан")


@app.post("/login")
def login(body: LoginIn):
    with get_conn() as conn:
        admin = conn.execute(
            "SELECT admin_id, email, password_hash FROM admins WHERE email = %s",
            (body.email.strip().lower(),),
        ).fetchone()
    # И-мэйл олдоогүй эсвэл нууц үг таараагүй: аль нь болохыг илчлэхгүй, ижил хариу
    if not admin or not bcrypt.checkpw(body.password.encode(), admin["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="И-мэйл эсвэл нууц үг буруу")
    return {
        "access_token": create_token(admin["admin_id"], admin["email"]),
        "token_type": "bearer",
        "email": admin["email"],
    }


@app.get("/health")
def health():
    with get_conn() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


# =====================================================================
# Ерөнхий CRUD туслах функцууд (4 цэс бүгд эдгээрийг ашиглана)
# =====================================================================

def insert_row(conn, table: str, id_col: str, data: dict) -> int:
    """INSERT INTO table (...) VALUES (...) RETURNING id"""
    cols = list(data)
    q = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING {}").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        sql.Identifier(id_col),
    )
    return conn.execute(q, list(data.values())).fetchone()[id_col]


def update_row(conn, table: str, id_col: str, row_id: int, data: dict) -> None:
    """UPDATE table SET ... WHERE id = ... (мөр олдохгүй бол 404)"""
    q = sql.SQL("UPDATE {} SET {} WHERE {} = %s").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in data),
        sql.Identifier(id_col),
    )
    if conn.execute(q, [*data.values(), row_id]).rowcount == 0:
        raise HTTPException(status_code=404, detail="Олдсонгүй")


def delete_row(conn, table: str, id_col: str, row_id: int,
               block_if: tuple[str, str, str] | None = None) -> None:
    """
    DELETE FROM table WHERE id = ...
    block_if = (хүснэгт, багана, мессеж): холбоотой мөр байвал устгахгүй, 409 буцаана.
    """
    if block_if:
        ref_table, ref_col, message = block_if
        q = sql.SQL("SELECT COUNT(*) AS n FROM {} WHERE {} = %s").format(
            sql.Identifier(ref_table), sql.Identifier(ref_col))
        n = conn.execute(q, (row_id,)).fetchone()["n"]
        if n:
            raise HTTPException(status_code=409, detail=message.format(n=n))
    q = sql.SQL("DELETE FROM {} WHERE {} = %s").format(
        sql.Identifier(table), sql.Identifier(id_col))
    if conn.execute(q, (row_id,)).rowcount == 0:
        raise HTTPException(status_code=404, detail="Олдсонгүй")


def ensure_exists(conn, table: str, id_col: str, row_id: int, label: str) -> None:
    q = sql.SQL("SELECT 1 FROM {} WHERE {} = %s").format(
        sql.Identifier(table), sql.Identifier(id_col))
    if not conn.execute(q, (row_id,)).fetchone():
        raise HTTPException(status_code=400, detail=f"{label} олдсонгүй")


# =====================================================================
# Аялал (tours)
# =====================================================================

class TourIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    destination: str = Field(min_length=1, max_length=50)
    days: int = Field(gt=0)
    price: float = Field(gt=0)
    start_date: date | None = None
    guide_id: int | None = None


@app.get("/tours")
def list_tours():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT t.tour_id, t.name, t.destination, t.days,
                   t.price::float AS price, t.start_date, t.guide_id,
                   g.full_name AS guide_name,
                   (SELECT COUNT(*) FROM bookings b WHERE b.tour_id = t.tour_id) AS bookings
            FROM tours t
            LEFT JOIN guides g ON g.guide_id = t.guide_id
            ORDER BY t.tour_id DESC
            """
        ).fetchall()


@app.post("/tours", status_code=201)
def create_tour(body: TourIn, admin: dict = Depends(require_admin)):
    with get_conn() as conn:
        if body.guide_id:
            ensure_exists(conn, "guides", "guide_id", body.guide_id, "Хөтөч")
        new_id = insert_row(conn, "tours", "tour_id", body.model_dump())
    return {"tour_id": new_id}


@app.put("/tours/{tour_id}")
def update_tour(tour_id: int, body: TourIn, admin: dict = Depends(require_admin)):
    with get_conn() as conn:
        if body.guide_id:
            ensure_exists(conn, "guides", "guide_id", body.guide_id, "Хөтөч")
        update_row(conn, "tours", "tour_id", tour_id, body.model_dump())
        # Үнэ өөрчлөгдсөн бол батлагдсан захиалгуудын төлбөрийг шинэчилнэ (дүн = үнэ × хүн)
        conn.execute(
            """
            UPDATE payments p SET amount = t.price * b.people
            FROM bookings b JOIN tours t ON t.tour_id = b.tour_id
            WHERE p.booking_id = b.booking_id AND b.tour_id = %s
            """,
            (tour_id,),
        )
    return {"ok": True}


@app.delete("/tours/{tour_id}")
def delete_tour(tour_id: int, admin: dict = Depends(require_admin)):
    with get_conn() as conn:
        delete_row(conn, "tours", "tour_id", tour_id, block_if=(
            "bookings", "tour_id",
            "Энэ аялалд {n} захиалга бүртгэлтэй тул устгах боломжгүй. "
            "Эхлээд захиалгуудыг нь шийдвэрлэнэ үү."))
    return {"ok": True}


# =====================================================================
# Захиалга (bookings)
# =====================================================================

Status = Literal["pending", "confirmed", "cancelled"]


class BookingIn(BaseModel):
    tour_id: int
    customer_id: int
    people: int = Field(gt=0)
    status: Status = "pending"


class StatusIn(BaseModel):
    status: Status


def sync_payment(conn, booking_id: int) -> None:
    """
    Өгөгдлийн дүрэм: батлагдсан захиалга бүр нэг төлбөртэй, дүн = үнэ × хүн.
    - Батлагдсан ба төлбөргүй бол төлбөр үүсгэнэ.
    - Төлбөртэй бол дүнг нь шинэчилнэ (хүн/аялал өөрчлөгдсөн байж болно).
    """
    b = conn.execute(
        """
        SELECT b.status, b.people, t.price, p.payment_id
        FROM bookings b
        JOIN tours t ON t.tour_id = b.tour_id
        LEFT JOIN payments p ON p.booking_id = b.booking_id
        WHERE b.booking_id = %s
        """,
        (booking_id,),
    ).fetchone()
    amount = b["price"] * b["people"]
    if b["payment_id"]:
        conn.execute("UPDATE payments SET amount = %s WHERE payment_id = %s",
                     (amount, b["payment_id"]))
    elif b["status"] == "confirmed":
        conn.execute(
            "INSERT INTO payments (booking_id, amount, paid_at, method) "
            "VALUES (%s, %s, CURRENT_DATE, 'cash')",
            (booking_id, amount),
        )


@app.get("/bookings")
def list_bookings(status: Status | None = None, limit: int = 50):
    limit = max(1, min(limit, 500))
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT b.booking_id, b.booked_at, b.people, b.status,
                   b.tour_id, t.name AS tour,
                   b.customer_id, c.full_name AS customer,
                   (p.payment_id IS NOT NULL) AS paid
            FROM bookings b
            JOIN tours t     ON t.tour_id     = b.tour_id
            JOIN customers c ON c.customer_id = b.customer_id
            LEFT JOIN payments p ON p.booking_id = b.booking_id
            WHERE (%(status)s::text IS NULL OR b.status = %(status)s)
            ORDER BY b.booked_at DESC, b.booking_id DESC
            LIMIT %(limit)s
            """,
            {"status": status, "limit": limit},
        ).fetchall()


@app.post("/bookings", status_code=201)
def create_booking(body: BookingIn, admin: dict = Depends(require_admin)):
    with get_conn() as conn:
        ensure_exists(conn, "tours", "tour_id", body.tour_id, "Аялал")
        ensure_exists(conn, "customers", "customer_id", body.customer_id, "Үйлчлүүлэгч")
        new_id = insert_row(conn, "bookings", "booking_id", body.model_dump())
        sync_payment(conn, new_id)
    return {"booking_id": new_id}


@app.put("/bookings/{booking_id}")
def update_booking(booking_id: int, body: BookingIn, admin: dict = Depends(require_admin)):
    with get_conn() as conn:
        ensure_exists(conn, "tours", "tour_id", body.tour_id, "Аялал")
        ensure_exists(conn, "customers", "customer_id", body.customer_id, "Үйлчлүүлэгч")
        update_row(conn, "bookings", "booking_id", booking_id, body.model_dump())
        sync_payment(conn, booking_id)
    return {"ok": True}


@app.patch("/bookings/{booking_id}/status")
def set_booking_status(booking_id: int, body: StatusIn, admin: dict = Depends(require_admin)):
    with get_conn() as conn:
        update_row(conn, "bookings", "booking_id", booking_id, {"status": body.status})
        sync_payment(conn, booking_id)
    return {"ok": True}


@app.delete("/bookings/{booking_id}")
def delete_booking(booking_id: int, admin: dict = Depends(require_admin)):
    with get_conn() as conn:
        delete_row(conn, "bookings", "booking_id", booking_id, block_if=(
            "payments", "booking_id",
            "Төлбөр төлөгдсөн захиалгыг устгах боломжгүй. «Цуцлах» товчийг ашиглана уу."))
    return {"ok": True}


# =====================================================================
# Үйлчлүүлэгч (customers)
# =====================================================================

class CustomerIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    country: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=100)


@app.get("/customers")
def list_customers():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT c.customer_id, c.full_name, c.country, c.email,
                   (SELECT COUNT(*) FROM bookings b WHERE b.customer_id = c.customer_id) AS bookings
            FROM customers c
            ORDER BY c.customer_id DESC
            """
        ).fetchall()


@app.post("/customers", status_code=201)
def create_customer(body: CustomerIn, admin: dict = Depends(require_admin)):
    with get_conn() as conn:
        new_id = insert_row(conn, "customers", "customer_id", body.model_dump())
    return {"customer_id": new_id}


@app.put("/customers/{customer_id}")
def update_customer(customer_id: int, body: CustomerIn, admin: dict = Depends(require_admin)):
    with get_conn() as conn:
        update_row(conn, "customers", "customer_id", customer_id, body.model_dump())
    return {"ok": True}


@app.delete("/customers/{customer_id}")
def delete_customer(customer_id: int, admin: dict = Depends(require_admin)):
    with get_conn() as conn:
        delete_row(conn, "customers", "customer_id", customer_id, block_if=(
            "bookings", "customer_id",
            "Энэ үйлчлүүлэгч {n} захиалгатай тул устгах боломжгүй."))
    return {"ok": True}


# =====================================================================
# Хөтөч (guides)
# =====================================================================

class GuideIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    language: str | None = Field(default=None, max_length=30)
    experience_years: int | None = Field(default=None, ge=0)


@app.get("/guides")
def list_guides():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT g.guide_id, g.full_name, g.language, g.experience_years,
                   (SELECT COUNT(*) FROM tours t WHERE t.guide_id = g.guide_id) AS tours
            FROM guides g
            ORDER BY g.guide_id DESC
            """
        ).fetchall()


@app.post("/guides", status_code=201)
def create_guide(body: GuideIn, admin: dict = Depends(require_admin)):
    with get_conn() as conn:
        new_id = insert_row(conn, "guides", "guide_id", body.model_dump())
    return {"guide_id": new_id}


@app.put("/guides/{guide_id}")
def update_guide(guide_id: int, body: GuideIn, admin: dict = Depends(require_admin)):
    with get_conn() as conn:
        update_row(conn, "guides", "guide_id", guide_id, body.model_dump())
    return {"ok": True}


@app.delete("/guides/{guide_id}")
def delete_guide(guide_id: int, admin: dict = Depends(require_admin)):
    with get_conn() as conn:
        delete_row(conn, "guides", "guide_id", guide_id, block_if=(
            "tours", "guide_id",
            "Энэ хөтөч {n} аялал хариуцаж байгаа тул устгах боломжгүй."))
    return {"ok": True}


# =====================================================================
# ML: эрэлт таамаглах (demand_model.joblib)
# =====================================================================

FEATURES = ["price_per_day", "guide_experience", "days"]


def load_model():
    """Сервер асахад загварыг нэг удаа ачаална. Файл байхгүй бол None."""
    path = Path(MODEL_PATH)
    if not path.is_absolute():
        path = Path(__file__).parent / path
    if not path.exists():
        # Консолын лог латин үсгээр: Windows консол кирилл үсэг хэвлэхэд унадаг
        print(f"[ML] model file not found: {path}")
        return None
    with warnings.catch_warnings():
        # Colab-ийн scikit-learn хувилбар өөр байж болно; LinearRegression-д нөлөөгүй
        warnings.simplefilter("ignore")
        model = joblib.load(path)
    print(f"[ML] model loaded: {path.name}")
    return model


MODEL = load_model()


def predict(rows: list[dict]) -> list[float]:
    """rows: [{"price_per_day":..., "guide_experience":..., "days":...}, ...]"""
    if MODEL is None:
        raise HTTPException(status_code=503, detail="ML загвар ачаалагдаагүй байна")
    X = pd.DataFrame(rows, columns=FEATURES)
    # Захиалгын тоо сөрөг байж болохгүй тул 0-оос доош гарвал 0 болгоно
    return [round(max(0.0, float(p)), 1) for p in MODEL.predict(X)]


@app.get("/predict-demand")
def predict_demand(price_per_day: float, guide_experience: float, days: int):
    """Шинэ аялал: өдрийн үнэ, хөтчийн туршлага, хоногоос захиалгын тоог таамаглана."""
    if price_per_day <= 0 or days <= 0 or guide_experience < 0:
        raise HTTPException(status_code=422, detail="Үнэ, хоног 0-ээс их, туршлага 0 буюу түүнээс их байх ёстой")
    row = {"price_per_day": price_per_day, "guide_experience": guide_experience, "days": days}
    return {**row, "predicted_bookings": predict([row])[0]}


@app.get("/demand/tours")
def demand_tours(limit: int = 10):
    """Хамгийн их захиалгатай аяллуудын бодит захиалга ба загварын таамаглал."""
    limit = max(1, min(limit, 200))
    with get_conn() as conn:
        tours = conn.execute(
            """
            SELECT t.tour_id, t.name, t.destination, t.days,
                   t.price::float AS price,
                   (t.price / t.days)::float AS price_per_day,
                   t.start_date, (t.start_date < CURRENT_DATE) AS is_past,
                   g.full_name AS guide_name,
                   g.experience_years AS guide_experience,
                   COUNT(b.booking_id) AS bookings
            FROM tours t
            JOIN guides g ON g.guide_id = t.guide_id
            LEFT JOIN bookings b ON b.tour_id = t.tour_id
            WHERE g.experience_years IS NOT NULL
            GROUP BY t.tour_id, g.guide_id
            ORDER BY bookings DESC, t.tour_id
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    if not tours:
        return []
    for t, p in zip(tours, predict(tours)):
        t["predicted"] = p
    return tours


# =====================================================================
# Хяналтын самбар
# =====================================================================

@app.get("/admin/stats")
def admin_stats(admin: dict = Depends(require_admin)):
    with get_conn() as conn:
        counts = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM tours)                                AS total_tours,
              (SELECT COUNT(*) FROM bookings)                             AS total_bookings,
              (SELECT COUNT(*) FROM bookings WHERE status = 'pending')    AS pending_bookings,
              (SELECT COALESCE(SUM(amount), 0)::bigint FROM payments)     AS total_revenue
            """
        ).fetchone()
        pending = conn.execute(
            """
            SELECT b.booking_id, c.full_name AS customer, t.name AS tour,
                   b.people, b.booked_at, b.status
            FROM bookings b
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN tours t     ON t.tour_id     = b.tour_id
            WHERE b.status = 'pending'
            ORDER BY b.booking_id DESC
            LIMIT 5
            """
        ).fetchall()
    return {**counts, "pending_list": pending}
