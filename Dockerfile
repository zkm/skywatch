FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# KiwiSDR client (kiwirecorder.py); needs numpy, which requirements.txt brings
RUN git clone --depth 1 https://github.com/jks-prv/kiwiclient.git /opt/kiwiclient

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY web ./web
COPY tools ./tools

ENV DATA_DIR=/data KIWIRECORDER=/opt/kiwiclient/kiwirecorder.py PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
