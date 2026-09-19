"""多观看端「投屏记录」协同（服务端只做内存中继，不落盘）。

管理员在工作台点「投屏记录」时可选
  - 单端记录：只记录本浏览器（既有行为，纯本地，不经服务端）；
  - 多端记录：邀请**同一台设备**的其他观看端一起记录，其他端弹窗确认后，
    在管理员停止记录时各自把自己那份记录上传，服务端**实时转发**给发起端浏览器，
    发起端拿到多份记录做对比/导出。服务端不写磁盘、不落库，只有内存态的会话，
    过期即消失。

设计要点：
  - 会话与参与者状态都在内存里（TTL 由 MIRROR_RECORD_TTL_SECONDS 控制），
    上传窗口只在「停止后 MIRROR_RECORD_UPLOAD_GRACE_SECONDS 秒」内开放；
  - 已接受的上传内容保留客户端提供的字段，
    服务端只做结构校验、深拷贝与体积/条数上限兜底，字段与未知键一律保留；
  - 转发失败（发起端队列满/已断开）不算成功投递，参与者会被告知「未送达」，
    它自己那份记录仍留在本地浏览器里。
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("webscrcpy.mirror")


def _bounded_float(name: str, default: str, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, default) or default)
    except (TypeError, ValueError):
        value = float(default)
    return max(minimum, min(maximum, value))


def _bounded_int(name: str, default: str, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, default) or default)
    except (TypeError, ValueError):
        value = int(default)
    return max(minimum, min(maximum, value))


RECORD_TTL_SECONDS = _bounded_float("MIRROR_RECORD_TTL_SECONDS", "900", 60.0, 3600.0)
RECORD_UPLOAD_GRACE_SECONDS = _bounded_float("MIRROR_RECORD_UPLOAD_GRACE_SECONDS", "20", 2.0, 300.0)
# 完整记录不裁剪字段，所以上限只作为防滥用兜底；真超了会明确报错并提示调大环境变量。
RECORD_UPLOAD_MAX_BYTES = _bounded_int(
    "MIRROR_RECORD_UPLOAD_MAX_BYTES", str(2 * 1024 * 1024), 32768, 16 * 1024 * 1024
)
RECORD_MAX_ENTRIES = _bounded_int("MIRROR_RECORD_MAX_ENTRIES", "20000", 50, 200000)
RECORD_MAX_PARTICIPANTS = _bounded_int("MIRROR_RECORD_MAX_PARTICIPANTS", "12", 1, 64)

STATE_INVITED = "invited"
STATE_ACCEPTED = "accepted"
STATE_DECLINED = "declined"
STATE_UPLOADED = "uploaded"
STATE_LEFT = "left"
STATE_UNAVAILABLE = "unavailable"


@dataclass
class RecordParticipant:
    client_id: str
    username: str
    state: str = STATE_INVITED
    responded_at: float = 0.0
    uploaded_at: float = 0.0
    # 浏览器稳定设备标识 + 是否暂时离线（关闭投屏/切走页面不等于退出记录）。
    browser_id: str = ""
    away: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "browser_id": self.browser_id,
            "username": self.username,
            "state": self.state,
            "responded": self.responded_at > 0,
            "uploaded": self.uploaded_at > 0,
            "away": self.away,
        }


@dataclass
class RecordSession:
    id: str
    device_id: str
    initiator_client_id: str
    initiator_username: str
    created_at: float
    expires_at: float
    participants: dict[str, RecordParticipant] = field(default_factory=dict)
    stopped: bool = False
    stopped_at: float = 0.0
    upload_deadline: float = 0.0

    def public(self) -> dict[str, Any]:
        return {
            "session": self.id,
            "device_id": self.device_id,
            "initiator": self.initiator_username,
            # 哪个观看端发起的：前端据此区分「主端（可停止）」与「副端（只能参与/退出）」。
            # 同一个管理员账号在多台设备上同时打开页面时，这个字段是唯一的判据。
            "initiator_client_id": self.initiator_client_id,
            "created_at": int(self.created_at),
            "expires_at": int(self.expires_at),
            "stopped": self.stopped,
            "upload_deadline": int(self.upload_deadline) if self.upload_deadline else 0,
            "participants": [item.public() for item in self.participants.values()],
        }


def validate_timeline(raw: Any) -> tuple[dict[str, Any] | None, str]:
    """校验并**原样**返回客户端上传的记录（不做字段/条数裁剪）。

    服务端保留诊断字段及未知键，不进一步裁剪浏览器上传的记录。这里只做：
      - 结构校验（必须是对象，且 entries 是列表），
      - 深拷贝（避免与调用方共享可变对象），
      - 体积/条数**上限兜底**（防滥用；真超了会明确报错并提示调大环境变量），
    字段、长度、未知键、data 里的任何内容一律保留。
    """
    if not isinstance(raw, dict):
        return None, "record_invalid_payload"
    entries = raw.get("entries")
    if not isinstance(entries, list):
        return None, "record_invalid_payload"
    if len(entries) > RECORD_MAX_ENTRIES:
        return None, "record_too_many_entries"
    try:
        import json

        cleaned = json.loads(json.dumps(raw, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return None, "record_invalid_payload"
    if not isinstance(cleaned, dict):
        return None, "record_invalid_payload"
    return cleaned, ""


def _timeline_size(timeline: dict[str, Any]) -> int:
    import json

    try:
        return len(json.dumps(timeline, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return RECORD_UPLOAD_MAX_BYTES + 1


class MirrorRecordRegistry:
    """内存态的「多端投屏记录」会话表。"""

    def __init__(self) -> None:
        self._sessions: dict[str, RecordSession] = {}
        self._lock = threading.RLock()

    # ---- 内部工具 ----

    @staticmethod
    def _device_session(device_id: str) -> Any:
        from .mirror_manager import manager

        return manager.sessions.get(device_id)

    def _purge_locked(self, now: float) -> None:
        expired = [key for key, item in self._sessions.items() if item.expires_at <= now]
        for key in expired:
            self._sessions.pop(key, None)
            log.info("MIRROR_RECORD_EXPIRED session=%s", key)

    def _find_for_device_locked(self, device_id: str, now: float) -> RecordSession | None:
        self._purge_locked(now)
        for item in self._sessions.values():
            if item.device_id == device_id and not item.stopped:
                return item
        return None

    def _recent_stopped_locked(self, device_id: str, now: float) -> RecordSession | None:
        """刚停止、还在上传窗口内的最近一次会话。"""
        candidates = [
            item
            for item in self._sessions.values()
            if item.device_id == device_id and item.stopped and item.upload_deadline > now
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.stopped_at)

    def _clients_locked(self, device_id: str) -> dict[str, Any]:
        session = self._device_session(device_id)
        clients = getattr(session, "clients", None) if session else None
        return clients if isinstance(clients, dict) else {}

    @staticmethod
    def _push_notice(client: Any, payload: dict[str, Any]) -> bool:
        pusher = getattr(client, "push_notice", None)
        if not callable(pusher):
            return False
        try:
            return bool(pusher(payload))
        except Exception:  # noqa: BLE001 - 通知失败不能影响主流程
            log.exception("MIRROR_RECORD_NOTICE_FAILED type=%s", payload.get("type"))
            return False

    # ---- 对外操作 ----

    def start(self, device_id: str, initiator_client_id: str, initiator_username: str) -> dict[str, Any]:
        now = time.time()
        normalized_client = str(initiator_client_id or "").strip()
        if not normalized_client:
            return {"ok": False, "error": "record_client_required"}
        session = self._device_session(device_id)
        clients = getattr(session, "clients", None) if session else None
        if not session or not isinstance(clients, dict) or not clients:
            return {"ok": False, "error": "record_no_viewers"}
        initiator = clients.get(normalized_client)
        if initiator is None or str(getattr(initiator, "username", "")) != initiator_username:
            return {"ok": False, "error": "record_client_required"}
        with self._lock:
            existing = self._find_for_device_locked(device_id, now)
            if existing is not None:
                return {"ok": False, "error": "record_session_exists", "session": existing.public()}
            record = RecordSession(
                id=secrets.token_urlsafe(24),
                device_id=device_id,
                initiator_client_id=normalized_client,
                initiator_username=initiator_username,
                created_at=now,
                expires_at=now + RECORD_TTL_SECONDS,
            )
            # 宫格观看端不参与投屏记录（用户要求）：宫格一路多端，混进单画面的时间线
            # 只会互相干扰。
            others = [
                client
                for cid, client in clients.items()
                if cid != normalized_client and not getattr(client, "grid_view", False)
            ]
            others.sort(key=lambda item: str(getattr(item, "connected_at", 0)))
            for client in others[:RECORD_MAX_PARTICIPANTS]:
                record.participants[str(client.id)] = RecordParticipant(
                    client_id=str(client.id),
                    username=str(getattr(client, "username", "")),
                    browser_id=str(getattr(client, "browser_id", "") or "")[:64],
                )
            self._sessions[record.id] = record
            invited = list(record.participants.values())
        base_notice = {
            "type": "record_invite",
            "session": record.id,
            "initiator": initiator_username,
            "expires_at": int(record.expires_at),
            "ttl_seconds": int(RECORD_TTL_SECONDS),
        }
        delivered = 0
        for participant in invited:
            client = clients.get(participant.client_id)
            # 带上被邀请端自己的 client_id：宫格视图里一个页面同时有多个观看端，
            # 响应时必须知道是哪一个收到了邀请。
            notice = dict(base_notice, client_id=participant.client_id)
            if client is not None and self._push_notice(client, notice):
                delivered += 1
        log.info(
            "MIRROR_RECORD_START device=%s session=%s initiator=%s invited=%s delivered=%s",
            device_id,
            record.id,
            initiator_username,
            len(invited),
            delivered,
        )
        return {"ok": True, "session": record.public(), "invited": len(invited), "delivered": delivered}

    def status(self, device_id: str) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            active = self._find_for_device_locked(device_id, now)
            if active is not None:
                return {"ok": True, "active": True, "session": active.public()}
            recent = [
                item
                for item in self._sessions.values()
                if item.device_id == device_id and item.stopped and item.upload_deadline > now
            ]
            if recent:
                latest = max(recent, key=lambda item: item.stopped_at)
                return {"ok": True, "active": False, "session": latest.public()}
        return {"ok": True, "active": False, "session": None}

    def respond(self, session_id: str, client_id: str, username: str, accept: bool, *, device_id: str) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            record = self._sessions.get(str(session_id))
            if record is None:
                return {"ok": False, "error": "record_session_missing"}
            # Bind the URL-authorized device to the selected record before
            # changing participant state or relaying any diagnostic data.
            if record.device_id != device_id:
                return {"ok": False, "error": "record_not_invited"}
            participant = record.participants.get(str(client_id))
            if participant is None or participant.username != username:
                return {"ok": False, "error": "record_not_invited"}
            if record.stopped:
                return {"ok": False, "error": "record_already_stopped"}
            participant.state = STATE_ACCEPTED if accept else STATE_DECLINED
            participant.responded_at = now
            initiator = self._device_session(record.device_id)
            clients = getattr(initiator, "clients", None) if initiator else None
            target = clients.get(record.initiator_client_id) if isinstance(clients, dict) else None
            state = participant.public()
        if target is not None:
            self._push_notice(
                target,
                {"type": "record_participant", "session": record.id, "participant": state},
            )
        log.info(
            "MIRROR_RECORD_RESPOND session=%s user=%s accept=%s",
            record.id,
            username,
            accept,
        )
        return {"ok": True, "participant": state}

    def stop(self, session_id: str, username: str) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            record = self._sessions.get(str(session_id))
            if record is None:
                return {"ok": False, "error": "record_session_missing"}
            if record.initiator_username != username:
                return {"ok": False, "error": "record_not_initiator"}
            record.stopped = True
            record.stopped_at = now
            record.upload_deadline = now + RECORD_UPLOAD_GRACE_SECONDS
            participants = list(record.participants.values())
            device_session = self._device_session(record.device_id)
            clients = getattr(device_session, "clients", None) if device_session else None
            clients = clients if isinstance(clients, dict) else {}
            upload_targets = [
                (participant, clients.get(participant.client_id))
                for participant in participants
                if participant.state == STATE_ACCEPTED and participant.uploaded_at <= 0
            ]
            others = [
                client
                for participant, client in (
                    (item, clients.get(item.client_id))
                    for item in participants
                    if item.state != STATE_ACCEPTED
                )
                if client is not None
            ]
            state = record.public()
        upload_notice = {
            "type": "record_upload_request",
            "session": record.id,
            "deadline": int(record.upload_deadline),
            "grace_seconds": int(RECORD_UPLOAD_GRACE_SECONDS),
        }
        requested = 0
        for _participant, client in upload_targets:
            if client is not None and self._push_notice(client, upload_notice):
                requested += 1
        stopped_notice = {"type": "record_stopped", "session": record.id, "reason": "initiator_stopped"}
        for client in others:
            self._push_notice(client, stopped_notice)
        log.info(
            "MIRROR_RECORD_STOP session=%s by=%s upload_requests=%s",
            record.id,
            username,
            requested,
        )
        return {"ok": True, "session": state, "upload_requests": requested}

    def upload(self, session_id: str, client_id: str, username: str, timeline: Any, *, device_id: str) -> dict[str, Any]:
        now = time.time()
        cleaned, error = validate_timeline(timeline)
        if cleaned is None:
            return {"ok": False, "error": error}
        size = _timeline_size(cleaned)
        if size > RECORD_UPLOAD_MAX_BYTES:
            return {"ok": False, "error": "record_payload_too_large", "bytes": size, "limit": RECORD_UPLOAD_MAX_BYTES}
        with self._lock:
            self._purge_locked(now)
            record = self._sessions.get(str(session_id))
            if record is None:
                return {"ok": False, "error": "record_session_missing"}
            if record.device_id != device_id:
                return {"ok": False, "error": "record_not_invited"}
            participant = record.participants.get(str(client_id))
            if participant is None or participant.username != username:
                return {"ok": False, "error": "record_not_invited"}
            # 先判"已上传"再判状态：上传成功会把状态置为 uploaded，顺序反了会报错成
            # "尚未同意参与"，对排查没有帮助。
            if participant.uploaded_at > 0:
                return {"ok": False, "error": "record_already_uploaded"}
            if participant.state != STATE_ACCEPTED:
                return {"ok": False, "error": "record_not_accepted"}
            if record.stopped and now > record.upload_deadline:
                return {"ok": False, "error": "record_window_closed"}
            participant.uploaded_at = now
            participant.state = STATE_UPLOADED
            device_session = self._device_session(record.device_id)
            clients = getattr(device_session, "clients", None) if device_session else None
            target = clients.get(record.initiator_client_id) if isinstance(clients, dict) else None
            payload = {
                "type": "record_bundle",
                "session": record.id,
                "from": {"client_id": participant.client_id, "username": participant.username},
                "received_at": int(now),
                "bytes": size,
                "timeline": cleaned,
                "participant": participant.public(),
            }
        delivered = False
        if target is not None:
            delivered = self._push_notice(target, payload)
        log.info(
            "MIRROR_RECORD_UPLOAD session=%s from=%s bytes=%s delivered=%s",
            record.id,
            username,
            size,
            delivered,
        )
        return {
            "ok": True,
            "delivered": delivered,
            "bytes": size,
            "entries": len(cleaned.get("entries") or []),
        }

    def attach_client(
        self,
        device_id: str,
        client_id: str,
        username: str,
        claim_session: str = "",
        claim_role: str = "",
    ) -> dict[str, Any]:
        """观看端（重新）接入时对齐记录会话。

        邀请只在 `start()` 那一刻发给「当时在线」的观看端，于是有两类端会漏掉：
          - 之后才打开页面/重连的观看端（例如同一账号的手机端晚一步进入工作台）；
          - 发起端自己刷新页面后 client_id 变了，旧会话的 initiator_client_id 成了陈旧值。
        视频通道建立后调用一次这里，把两种情况补齐：
          - 带着有效 claim（本机 localStorage 里存着会话 id + 角色）回来的发起端拿回主导权；
          - 其他新接入的观看端补一条邀请（会话仍在进行）或补一次上传请求（刚停止、还在窗口内）。
        无进行中会话时是纯读操作，直接返回。
        """
        normalized = str(client_id or "").strip()
        if not normalized:
            return {"ok": False, "error": "record_client_required"}
        now = time.time()
        target: Any = None
        payload: dict[str, Any] | None = None
        role = "none"
        with self._lock:
            self._purge_locked(now)
            clients = self._clients_locked(device_id)
            target = clients.get(normalized)
            # 只认「当前确实连着这台设备、且属于调用者本人」的观看端：否则任何人都能
            # 拿别人的 client_id 来抢发起端身份或伪造参与。
            if target is None or str(getattr(target, "username", "")) != username:
                return {"ok": False, "error": "record_client_required"}
            # 宫格观看端不参与记录：不邀请、也不加参与位。
            if getattr(target, "grid_view", False):
                return {"ok": True, "role": "none", "delivered": False}
            record = self._find_for_device_locked(device_id, now)
            if record is not None:
                claimed = (
                    str(claim_session or "") == record.id
                    and str(claim_role or "") == "initiator"
                    and record.initiator_username == username
                )
                if claimed:
                    # 只有能出示会话 id 的本机页面才拿回主导权；同账号的另一台设备
                    # （手机端）没有这条 claim，因此仍然是副端，不会抢走停止权限。
                    record.initiator_client_id = normalized
                    role = "initiator"
                    payload = {
                        "type": "record_session",
                        "session": record.public(),
                        "role": "initiator",
                        "client_id": normalized,
                    }
                else:
                    participant = record.participants.get(normalized)
                    if participant is None:
                        if len(record.participants) < RECORD_MAX_PARTICIPANTS:
                            participant = RecordParticipant(
                                client_id=normalized,
                                username=username,
                                browser_id=str(getattr(target, "browser_id", "") or "")[:64],
                            )
                            record.participants[normalized] = participant
                        else:
                            role = "rejected"
                    if participant is not None:
                        # 重新接入 = 结束暂离（关闭过投屏/切走过页面都算）。
                        participant.away = False
                        if participant.responded_at <= 0:
                            role = "invited"
                            payload = {
                                "type": "record_invite",
                                "session": record.id,
                                "initiator": record.initiator_username,
                                "expires_at": int(record.expires_at),
                                "ttl_seconds": int(RECORD_TTL_SECONDS),
                                "client_id": normalized,
                            }
                        else:
                            role = "participant"
                            payload = {
                                "type": "record_session",
                                "session": record.public(),
                                "role": "participant",
                                "client_id": normalized,
                            }
            else:
                recent = self._recent_stopped_locked(device_id, now)
                if recent is not None:
                    participant = recent.participants.get(normalized)
                    if (
                        participant is not None
                        and participant.state == STATE_ACCEPTED
                        and participant.uploaded_at <= 0
                    ):
                        role = "upload"
                        payload = {
                            "type": "record_upload_request",
                            "session": recent.id,
                            "deadline": int(recent.upload_deadline),
                            "grace_seconds": int(RECORD_UPLOAD_GRACE_SECONDS),
                            "client_id": normalized,
                        }
        delivered = False
        if target is not None and payload is not None:
            delivered = self._push_notice(target, payload)
        if role != "none":
            log.info(
                "MIRROR_RECORD_ATTACH device=%s client=%s user=%s role=%s delivered=%s",
                device_id,
                normalized,
                username,
                role,
                delivered,
            )
        return {"ok": True, "role": role, "delivered": delivered}

    def note_client_left(self, device_id: str, client_id: str) -> None:
        """观看端断开：标记为「暂离」，**不退出记录**。

        用户要求：关闭投屏 / 停流不等于退出这次记录（他要反复开关投屏来复现问题）。
        所以这里只把状态标成 away 并通知发起端，参与位与已上传状态都保留；
        重新接入（attach_client）时自动恢复。真正的「退出参与」只有显式 respond(false)。
        """
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            record = self._find_for_device_locked(device_id, now)
            if record is None:
                return
            participant = record.participants.get(str(client_id))
            if participant is None or participant.uploaded_at > 0:
                return
            participant.away = True
            device_session = self._device_session(device_id)
            clients = getattr(device_session, "clients", None) if device_session else None
            target = clients.get(record.initiator_client_id) if isinstance(clients, dict) else None
            state = participant.public()
        if target is not None:
            self._push_notice(
                target,
                {"type": "record_participant", "session": record.id, "participant": state},
            )

    def cancel_for_device(self, device_id: str, reason: str = "stream_stopped") -> None:
        """设备停流：**不结束记录**，只把参与端标成暂离并通知。

        用户要求：停止投屏/关掉投屏不应终止记录（他要反复开关投屏复现问题）。
        会话由发起端显式停止（`stop`）或 TTL 到期回收；这里只广播「暂时没画面了」。
        """
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            record = self._find_for_device_locked(device_id, now)
            if record is None:
                return
            device_session = self._device_session(device_id)
            clients = getattr(device_session, "clients", None) if device_session else None
            clients = clients if isinstance(clients, dict) else {}
            for participant in record.participants.values():
                if participant.uploaded_at > 0:
                    continue
                if participant.state == STATE_ACCEPTED:
                    participant.away = True
            targets = [clients.get(item.client_id) for item in record.participants.values()]
            initiator = clients.get(record.initiator_client_id)
            session_state = record.public()
        notice = {"type": "record_stream_stopped", "session": record.id, "reason": reason}
        for client in targets:
            if client is not None:
                self._push_notice(client, notice)
        # 发起端也要知道「画面停了但记录还在」，面板据此显示「暂离/等待重连」。
        if initiator is not None:
            self._push_notice(initiator, {"type": "record_session", "session": session_state, "role": "initiator"})
        log.info("MIRROR_RECORD_STREAM_STOPPED device=%s session=%s reason=%s", device_id, record.id, reason)

    def reset(self) -> None:
        """仅供测试/排查：清空所有内存态记录会话。"""
        with self._lock:
            self._sessions.clear()


record_registry = MirrorRecordRegistry()
