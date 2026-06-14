FROM python:3.12-slim

WORKDIR /app

# System deps for WeasyPrint and asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libffi-dev \
    libxml2-dev \
    libxslt-dev \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
