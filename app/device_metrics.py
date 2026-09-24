"""Bounded, on-demand Android resource snapshots; never part of video delivery."""

from __future__ import annotations

from collections import OrderedDict
import threading
import time

from adb_manager import ADBManager

CACHE_SECONDS = 10.0
MAX_CACHE_ENTRIES = 128
_lock = threading.Lock()
_pending: set[str] = set()
_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
# Fixed commands only. Android may deny /proc/stat; memory remains independently useful.
_COMMAND = (
    "head -n 1 /proc/stat 2>/dev/null; printf '\\nMEMORY\\n'; "
    "head -c 8192 /proc/meminfo 2>/dev/null; printf '\\nCPU_END\\n'; "
    "sleep 1; head -n 1 /proc/stat 2>/dev/null; true"
)


def _cpu_ticks(raw: str) -> tuple[int, int] | None:
    fields = raw.strip().split()
    if len(fields) < 5 or fields[0] != "cpu":
        return None
    try:
        ticks = [int(value) for value in fields[1:9]]
    except ValueError:
        return None
    if any(value < 0 for value in ticks):
        return None
    # Guest time is already included in user/nice; iowait is idle time.
    return sum(ticks), ticks[3] + (ticks[4] if len(ticks) > 4 else 0)


def parse_snapshot(raw: str) -> dict:
    result = {"cpu_percent": None, "memory_total_bytes": None, "memory_used_bytes": None}
    if len(raw) > 16384:
        return result
    start, separator, rest = raw.partition("\nMEMORY\n")
    memory, end_separator, end = rest.partition("\nCPU_END\n")
    if not separator or not end_separator:
        return result
    before, after = _cpu_ticks(start), _cpu_ticks(end)
    if before and after:
        total, idle = after[0] - before[0], after[1] - before[1]
        if total > 0 and 0 <= idle <= total:
            result["cpu_percent"] = round(100 * (total - idle) / total, 1)
    values = {}
    for line in memory.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == "kB" and parts[1].isdigit():
            values[parts[0].rstrip(":")] = int(parts[1]) * 1024
    total, available = values.get("MemTotal"), values.get("MemAvailable")
    if total and available is not None and 0 <= available <= total:
        result.update(memory_total_bytes=total, memory_used_bytes=total - available)
    return result


def snapshot(address: str) -> dict:
    """Limit sampling to two devices at once and cache failures as well as successes."""
    with _lock:
        cached = _cache.get(address)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            return {**cached[1], "cached": True}
        if address in _pending or len(_pending) >= 2:
            return {"status": "busy", "cpu_percent": None, "memory_total_bytes": None,
                    "memory_used_bytes": None, "sampled_at": None, "cached": False}
        _pending.add(address)
    try:
        ok, output = ADBManager()._run_adb_command(["shell", _COMMAND], device_id=address, timeout=4)
        result = parse_snapshot(output if ok else "")
        known = sum(result[key] is not None for key in ("cpu_percent", "memory_used_bytes"))
        result.update(status="ok" if known == 2 else "partial" if known else "unavailable",
                      sampled_at=int(time.time()), cached=False)
        with _lock:
            _cache[address] = (time.monotonic(), result)
            _cache.move_to_end(address)
            while len(_cache) > MAX_CACHE_ENTRIES:
                _cache.popitem(last=False)
        return dict(result)
    finally:
        with _lock:
            _pending.discard(address)
