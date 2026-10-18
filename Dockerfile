FROM python:3.12.8-slim

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 5000
CMD ["python", "app.py"]
