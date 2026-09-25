"""Bounded, on-demand Android resource snapshots; never part of video delivery."""

from __future__ import annotations

from collections import OrderedDict
import re
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
    "sleep 1; head -n 1 /proc/stat 2>/dev/null; printf '\\nCPU_FALLBACK\\n'; "
    "if ! head -n 1 /proc/stat >/dev/null 2>&1; then "
    "dumpsys -t 1 cpuinfo 2>/dev/null | grep ' TOTAL:' | head -c 1024; fi; "
    "true"
)
_TEMPERATURE_COMMAND = (
    "printf 'THERMAL_HAL\\n'; dumpsys -t 1 thermalservice 2>/dev/null | head -c 8192; "
    "printf '\\nTHERMAL_SYSFS\\n'; sg_count=0; "
    "for sg_zone in /sys/class/thermal/thermal_zone*; do "
    "[ \"$sg_count\" -ge 64 ] && break; sg_count=$((sg_count + 1)); "
    "[ -r \"$sg_zone/type\" ] && [ -r \"$sg_zone/temp\" ] || continue; "
    "IFS= read -r sg_type < \"$sg_zone/type\"; "
    "case \"$sg_type\" in *cpu*|*CPU*) "
    "IFS= read -r sg_temp < \"$sg_zone/temp\"; printf '%s|%s\\n' \"$sg_type\" \"$sg_temp\";; esac; "
    "done; true"
)


def parse_cpu_temperature(raw: str) -> dict:
    """Use current CPU sensors only; never substitute battery, skin or thresholds."""
    unknown = {"temperature_celsius": None, "temperature_source": None}
    if len(raw) > 16384:
        return unknown
    hal, _, sysfs = raw.replace("\r\n", "\n").partition("\nTHERMAL_SYSFS\n")
    current = re.search(
        r"^Current temperatures from HAL:\s*\n(.*?)(?=^[^\s]|\Z)", hal, re.MULTILINE | re.DOTALL
    )
    values = []
    if current:
        for value, kind in re.findall(
            r"Temperature\{mValue=(-?\d{1,3}(?:\.\d{1,6})?),\s*mType=(-?\d{1,2}),", current[1]
        ):
            if kind == "0" and -20 <= float(value) <= 150:
                values.append(float(value))
    if values:
        return {"temperature_celsius": round(max(values), 1), "temperature_source": "cpu_thermal_hal"}
    for line in sysfs.splitlines()[:64]:
        name, separator, value = line.partition("|")
        if not separator or not re.fullmatch(r"cpu(?:[0-9]+|[-_](?:thermal|big|little|cluster[0-9]*|[0-9]+))*", name, re.I):
            continue
        # Linux thermal sysfs uses millidegrees Celsius. Do not guess vendor units.
        if re.fullmatch(r"-?\d{1,6}", value) and -20000 <= int(value) <= 150000:
            values.append(int(value) / 1000)
    if values:
        return {"temperature_celsius": round(max(values), 1), "temperature_source": "cpu_thermal_sysfs"}
    return unknown


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
    result = {"cpu_percent": None, "cpu_source": None, "memory_total_bytes": None, "memory_used_bytes": None,
              "temperature_celsius": None, "temperature_source": None}
    if len(raw) > 24576:
        return result
    raw = raw.replace("\r\n", "\n")
    start, separator, rest = raw.partition("\nMEMORY\n")
    memory, end_separator, end = rest.partition("\nCPU_END\n")
    if not separator or not end_separator:
        return result
    end, _, fallback = end.partition("\nCPU_FALLBACK\n")
    before, after = _cpu_ticks(start), _cpu_ticks(end)
    if before and after:
        total, idle = after[0] - before[0], after[1] - before[1]
        if total > 0 and 0 <= idle <= total:
            result["cpu_percent"] = round(100 * (total - idle) / total, 1)
            result["cpu_source"] = "proc_stat"
    if result["cpu_percent"] is None:
        match = re.search(r"^\s*(\d+(?:\.\d+)?)%\s+TOTAL:", fallback, re.MULTILINE)
        if match and 0 <= float(match[1]) <= 100:
            result.update(cpu_percent=round(float(match[1]), 1), cpu_source="dumpsys_cpuinfo")
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
        adb = ADBManager()
        ok, output = adb._run_adb_command(["shell", _COMMAND], device_id=address, timeout=4)
        result = parse_snapshot(output if ok else "")
        known = sum(result[key] is not None for key in ("cpu_percent", "memory_used_bytes"))
        reason = "" if known == 2 else "metrics_unsupported"
        if not ok:
            reason = "adb_timeout" if (adb.last_error_info or {}).get("timed_out") else "adb_unavailable"
        if ok:
            # Optional temperature failure must not erase successful CPU/memory samples.
            thermal_ok, thermal_output = adb._run_adb_command(
                ["shell", _TEMPERATURE_COMMAND], device_id=address, timeout=2
            )
            if thermal_ok:
                result.update(parse_cpu_temperature(thermal_output))
        result.update(status="ok" if known == 2 else "partial" if known else "unavailable",
                      reason=reason, sampled_at=int(time.time()), cached=False)
        with _lock:
            _cache[address] = (time.monotonic(), result)
            _cache.move_to_end(address)
            while len(_cache) > MAX_CACHE_ENTRIES:
                _cache.popitem(last=False)
        return dict(result)
    finally:
        with _lock:
            _pending.discard(address)
