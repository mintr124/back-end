FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install torch CPU-only first to avoid pulling multi-GB CUDA packages
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# Alembic needs this file at /app when the API runs migrations on startup.
COPY alembic.ini ./alembic.ini
COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
