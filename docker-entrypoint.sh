#!/bin/sh
set -eu

# 数据库/日志含会话令牌：进程内新建文件一律仅属主可访问。
umask 077

max_size="${SCRCPYGATE_WS_MAX_SIZE:-65536}"
case "$max_size" in
  ''|*[!0-9]*)
    echo "SCRCPYGATE_WS_MAX_SIZE must be an integer between 1024 and 67108864" >&2
    exit 2
    ;;
esac
if [ "$max_size" -lt 1024 ] || [ "$max_size" -gt 67108864 ]; then
  echo "SCRCPYGATE_WS_MAX_SIZE must be an integer between 1024 and 67108864" >&2
  exit 2
fi

# 监听地址/端口。两种部署方式共用这两个变量：
#   1) compose（默认）：compose.yaml 用 ports: "${BIND}:${PORT}:5000" 做映射，容器内部
#      仍是 0.0.0.0:5000 —— 那两个变量不会传进容器，所以这里取默认值。
#   2) 宿主网络（docker run --net=host / compose.host.yaml）：没有端口映射，容器直接按
#      这两个值绑在宿主上，因此必须与 PUBLIC_BASE_URL 一致。
bind="${WEB_SCRCPY_BIND:-0.0.0.0}"
port="${WEB_SCRCPY_PORT:-5000}"
case "$port" in
  ''|*[!0-9]*)
    echo "WEB_SCRCPY_PORT must be an integer between 1 and 65535" >&2
    exit 2
    ;;
esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
  echo "WEB_SCRCPY_PORT must be an integer between 1 and 65535" >&2
  exit 2
fi

uvicorn_bin=/app/venv/bin/uvicorn
if [ ! -x "$uvicorn_bin" ]; then
  echo "[ERR] 未找到 uvicorn；此入口应在 ScrcpyGate 容器内运行。宿主机请执行 ./deploy.sh --install 或 docker compose up -d --build" >&2
  exit 127
fi

# 空的 ADB_SERVER_SOCKET 必须彻底去掉而不能留着：adb 只要看到这个变量就会拿它当
# server 地址，空值会让每条 adb 命令都失败（cannot connect to daemon at : unknown
# socket specification），设备永远上不了线。compose.yaml 用 ${ADB_SERVER_SOCKET:-}
# 转发，未配置时正好传进来一个空字符串，所以这里统一归一化。
if [ -z "${ADB_SERVER_SOCKET:-}" ]; then
  unset ADB_SERVER_SOCKET
fi
if [ -z "${ADB_PATH:-}" ]; then
  unset ADB_PATH
fi

# Provision only for fresh/unencrypted data. Existing ciphertext without its
# matching key is preserved; core service recovery remains available.
if ! python -m app.cli generate-alas-key; then
  echo "[WARN] ALAS key provisioning failed; restore the matching key or explicitly clear the token. Existing credentials were preserved." >&2
fi

# --no-proxy-headers 是**必须**的：应用自己实现「从右向左跳过可信代理」的 X-Forwarded-For
# 解析（app/security.py::client_ip + TRUST_PROXY/TRUSTED_PROXY_IPS）。若让 uvicorn 处理
# 转发头（默认开启），它会先把 request.client.host 改写成 XFF 里的客户端地址——于是应用看到
# 的「直连对端」变成了客户端而不是代理，TRUSTED_PROXY_IPS 判定必然失败，经环回代理进来的
# 每个请求都会以 403（untrusted_proxy_headers）被拒。关掉它，转发头只有应用一份判定逻辑。
exec "$uvicorn_bin" app.main:app \
  --host "$bind" \
  --port "$port" \
  --workers 1 \
  --no-proxy-headers \
  --ws-max-size "$max_size"
