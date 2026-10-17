FROM python:3.12.8-slim

WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 5000
CMD ["python", "app.py"]
