"""Container-local health check that follows the configured listen address."""
import ipaddress
import os
import re
import urllib.request


def health_url() -> str:
    host = os.environ.get("WEB_SCRCPY_BIND", "0.0.0.0") or "0.0.0.0"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", host):
            raise ValueError("invalid health host") from None
    else:
        host = f"[{address}]" if address.version == 6 else str(address)
    port = int(os.environ.get("WEB_SCRCPY_PORT", "5000") or "5000")
    if not 1 <= port <= 65535:
        raise ValueError("invalid health port")
    return f"http://{host}:{port}/healthz"


def main() -> int:
    try:
        # Proxy settings must not redirect a container-local health check.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(health_url(), timeout=3) as response:
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
