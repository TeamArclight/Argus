FROM python:3.11-slim

WORKDIR /app

COPY services/intelligence /app

CMD ["python", "-c", "print('ARGUS worker placeholder')"]
