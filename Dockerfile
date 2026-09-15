ARG PYTHON_IMAGE=python:3.12-alpine
FROM ${PYTHON_IMAGE}

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    WEB_SCRCPY_DATA_DIR=/app/data \
    PATH="/app/venv/bin:$PATH" \
    HOME=/app/data

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
COPY static/ static/
COPY LICENSE THIRD_PARTY.md ./
COPY adb_manager.py scrcpy.py scrcpy-server docker-entrypoint.sh ./
COPY adb/linux/ adb/linux/

RUN mkdir -p /app/data /tmp && \
    chmod +x /app/adb/linux/adb || true && \
    chmod +x /app/docker-entrypoint.sh && \
    chown -R app:app /app /tmp

USER app
EXPOSE 5000
# 端口跟随 WEB_SCRCPY_PORT（宿主网络模式下容器直接绑宿主端口，默认 5000 不变）。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -m app.container_health || exit 1

CMD ["/app/docker-entrypoint.sh"]
