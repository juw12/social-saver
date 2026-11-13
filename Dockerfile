# Social Saver web app: the same app, served to phones (iPhone and Android) from a small server.
# Set SOCIAL_SAVER_PASSWORD; the page asks for it. See README "Web app".
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir gallery-dl yt-dlp flask requests segno

COPY app.py ui.html ./
COPY saver ./saver
COPY static ./static

ENV SOCIAL_SAVER_HOME=/data \
    SOCIAL_SAVER_OUTPUT_DIR=/data/zips \
    PYTHONUNBUFFERED=1 \
    PORT=8765
EXPOSE 8765
CMD ["sh", "-c", "python app.py --serve --host 0.0.0.0 --port ${PORT}"]
