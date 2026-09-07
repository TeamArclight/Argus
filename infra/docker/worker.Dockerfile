FROM python:3.11-slim

WORKDIR /app

COPY services/api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/api /app

CMD ["python", "-m", "app.workers.worker"]
