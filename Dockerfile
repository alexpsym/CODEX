FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r render/requirements.txt \
 && python -m pip install --no-cache-dir -U yt-dlp

ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "uvicorn render.master_service:app --host 0.0.0.0 --port ${PORT:-10000}"]
