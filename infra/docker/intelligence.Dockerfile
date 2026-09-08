FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY services/intelligence/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/intelligence /app

EXPOSE 8100

CMD ["uvicorn", "argus_ai.http_service:app", "--host", "0.0.0.0", "--port", "8100"]