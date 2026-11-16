# Travel Admin API: AWS App Runner-т байршуулах зориулалттай
FROM python:3.12-slim

# Python-ы гаралтыг шууд лог руу бичих, .pyc файл үүсгэхгүй
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Эхлээд зөвхөн сангуудын жагсаалтыг хуулна: код өөрчлөгдөхөд дахин суулгахгүй (хурдан build)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код ба ML загвар (.env-ийг ХУУЛАХГҮЙ: .dockerignore-д бий)
COPY main.py demand_model.joblib ./

# root биш энгийн хэрэглэгчээр ажиллуулна (аюулгүй байдал)
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

# Тохиргоо (DATABASE_URL, SECRET_KEY, ...) нь App Runner-ийн environment variable-аас ирнэ
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
