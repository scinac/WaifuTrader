FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY . .
RUN pip install --no-cache-dir --no-deps . && useradd --create-home tt
USER tt

EXPOSE 8000
# Migrate, then serve. One worker: the market data service runs in-process.
CMD ["sh", "-c", "alembic upgrade head && uvicorn tt.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
