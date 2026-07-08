FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Platforms like Render/Railway/Fly inject $PORT; default to 8000 for local runs.
ENV PORT=8000
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
