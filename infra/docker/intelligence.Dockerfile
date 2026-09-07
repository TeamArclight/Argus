FROM python:3.11-slim

WORKDIR /app

COPY services/intelligence/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/intelligence /app

EXPOSE 8100

CMD ["uvicorn", "argus_ai.http_service:app", "--host", "0.0.0.0", "--port", "8100"]