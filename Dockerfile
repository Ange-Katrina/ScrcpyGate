ARG PYTHON_IMAGE=python:3.12-alpine
FROM ${PYTHON_IMAGE}

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    WEB_SCRCPY_DATA_DIR=/app/data \
    PATH="/app/venv/bin:$PATH" \
    HOME=/tmp

RUN apk add --no-cache android-tools libstdc++ libffi curl && \
    addgroup -S app && \
    adduser -S -G app app

COPY requirements.txt .
ARG PIP_INDEX_URL=
RUN python -m venv /app/venv && \
    . /app/venv/bin/activate && \
    pip install --no-cache-dir --upgrade pip && \
    if [ -n "$PIP_INDEX_URL" ]; then \
      pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt; \
    else \
      pip install --no-cache-dir -r requirements.txt; \
    fi

COPY app/ app/
COPY templates/ templates/
COPY static/ static/
COPY adb_manager.py scrcpy.py scrcpy-server ./
COPY adb/linux/ adb/linux/

RUN mkdir -p /app/data /tmp && \
    chmod +x /app/adb/linux/adb || true && \
    chown -R app:app /app /tmp

USER app
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:5000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "1"]
