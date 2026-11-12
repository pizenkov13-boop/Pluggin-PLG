# PLG Cloud API — Fly.io / Railway / Docker
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY cloud/requirements.txt /app/cloud/requirements.txt
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r cloud/requirements.txt -r requirements.txt

COPY . /app

ENV PYTHONUNBUFFERED=1
ENV PLG_ENV=production

EXPOSE 8787

CMD ["uvicorn", "cloud.app.main:app", "--host", "0.0.0.0", "--port", "8787", "--workers", "2"]
