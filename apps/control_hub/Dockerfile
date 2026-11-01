# Control Hub (sahte robotla): ROS yok, yalnızca Python.  docker compose up hub-fake
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY control_hub control_hub
COPY config config
EXPOSE 8765
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=2)"
CMD ["python", "-m", "control_hub", "--fake", "--no-qr"]
