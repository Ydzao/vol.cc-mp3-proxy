FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY sc_mp3_proxy.py .
CMD ["python", "vol.cc-sc_mp3_proxy.py"]
