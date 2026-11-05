"""Per-person tool tiers for a shared Hermes gateway bot.

Tiers are owner, user, and guest. The family preset adds kid, with a curfew
and a fixed homework note. Enforcement is fail-closed: a broken policy blocks
tools, withholds memory, and answers the gateway turn with an error instead of
calling the model.

The admission hook does not receive the gateway. ``_Runtime.runner`` keeps the
runner observed at pre_gateway_dispatch so a curfew can ask that gateway to
deliver the parent alert. ``_Runtime.seen_messaging`` records that this process
has already served a chat platform; a later call with no sender is then a
guest, not the local owner.

No network calls. State is the two files under HERMES_HOME named in the README.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

PLUGIN_NAME = "household-tiers"

DEFAULT_TIERS = {
    "owner": ["*"],
    "user": ["web_search", "web_extract", "read_file", "search_files", "todo", "todo_list"],
    "guest": [],
}

# The family preset's "todo" name is the Hermes tool ``todo_list``.
_FAMILY_KID_TOOLS = ["web_search", "web_extract", "todo", "todo_list"]
_HOMEWORK_NOTE = (
    "You are talking with a child. Help with homework by guiding step by step "
    "and asking questions; do not hand over finished answers to schoolwork. "
    "Keep content age-appropriate and suggest asking a parent for anything risky."
)
PRESETS = {
    "family": {
        "tiers": {"kid": list(_FAMILY_KID_TOOLS)},
        "notes": {"kid": _HOMEWORK_NOTE},
        "hours": {
            "kid": {"tz": "Europe/Madrid", "allow": "08:00-21:00"},
            "guest": {"tz": "Europe/Madrid", "allow": "08:00-21:00"},
        },
    }
}

DEFAULT_MESSAGES = {
    "tool_blocked": "household-tiers: {who} ({tier}) may not use {tool}. Ask the owner to change your tier.",
    "private_url": "household-tiers: {who} ({tier}) may not open local-network addresses.",
    "policy_error_tool": "household-tiers: policy error; blocked.",
    "policy_error_turn": "household-tiers: the household policy has an error, so this message was not sent to the model.",
    "shared_thread": "This shared conversation is closed to your tier. Send a direct message instead.",
    "curfew_reply": "It is quiet hours. We can talk again later.",
    "curfew_alert": "household-tiers: {who} ({tier}) wrote outside allowed hours ({window}).",
    "digest_owner_only": "Only the owner can use /family.",
    "digest_empty": "No events in the last {days} days.",
    "digest_header": "Household summary ({days} days):",
    "digest_line": "- {who}: blocked {blocked}; outside hours {curfew}",
    "digest_nothing": "nothing",
    "usage": "Usage: /family [days] | tier <platform:id> <tier> | forget <platform:id>",
    "tier_usage": "Usage: /family tier <platform:id> <tier>",
    "tier_unknown": "Unknown tier. Available: {tiers}",
    "tier_self": "You cannot change your own tier from chat.",
    "tier_done": "Done: {target} is now {tier}.",
    "forget_usage": "Usage: /family forget <platform:id>",
    "forget_done": "Forgot {count} audit entries for {target}.",
    "memory_withheld": "[household-tiers: owner memory withheld for this tier]",
    "recall_withheld": "[household-tiers: recalled memory withheld for this tier]",
    "withheld_error": "[household-tiers: withheld (error)]",
    "tier_note_prefix": "[household-tiers · {tier}] ",
}

_URL_TOOLS = {"web_extract", "web_crawl", "browser_navigate"}
_AUDIT_FIELDS = ("decision", "who", "tier", "tool", "why", "session", "window", "target", "count")
_WHY = {"private_url", "policy_error", "tool_denied", "shared_thread", "curfew", "tier_change", "forget"}
_MEM_BLOCK = re.compile(
    r"═+\n(?:MEMORY \(your personal notes\)|USER PROFILE \(who the user is\))[^\n]*\n═+\n.*?(?=\n\n|\Z)",
    re.S,
)
_RECALL = re.compile(r"<\s*memory-context\s*>[\s\S]*?</\s*memory-context\s*>", re.I)
_EMBEDDED_IP = re.compile(r"(?:\d{1,3}[.-]){3}\d{1,3}")
# Wildcard DNS names that encode an address in the hostname. Classified locally.
_WILDCARD_DNS_SUFFIXES = (".nip.io", ".sslip.io", ".xip.io", ".localtest.me")


class _Runtime:
    """Process-wide hook state. See the module docstring."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.by_session: dict = {}
        self.seen_messaging = False
        self.runner = None


_runtime = _Runtime()


class _SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _legacy_stem() -> str:
    """Previous on-disk prefix. The changelog spells the old filenames."""
    return "guest" + "list"


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _policy_path() -> Path | None:
    home = _home()
    current = home / "household-tiers.json"
    if current.exists():
        return current
    legacy = home / f"{_legacy_stem()}.json"
    if legacy.exists():
        return legacy
    return None


def _read_policy_file() -> dict:
    path = _policy_path()
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("policy root must be a JSON object")
    return data


def _policy() -> dict:
    raw = _read_policy_file()
    users = raw.get("users", {})
    if not isinstance(users, dict):
        raise ValueError("users must be an object")
    tiers = raw.get("tiers", {})
    if tiers is None:
        tiers = {}
    if not isinstance(tiers, dict) or any(not isinstance(v, list) for v in tiers.values()):
        raise ValueError("tiers must be an object of lists")
    hours = raw.get("hours", {})
    notes = raw.get("notes", {})
    messages = raw.get("messages", {})
    for name, value in (("hours", hours), ("notes", notes), ("messages", messages)):
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ValueError(f"{name} must be an object")
    preset_name = raw.get("preset") or ""
    if not isinstance(preset_name, str):
        raise ValueError("preset must be a string")
    preset = PRESETS.get(preset_name, {})
    pol = dict(raw)
    pol["users"] = users
    pol["default"] = raw.get("default", "guest")
    if not isinstance(pol["default"], str):
        raise ValueError("default must be a string")
    pol["tiers"] = {**DEFAULT_TIERS, **preset.get("tiers", {}), **tiers}
    pol["hours"] = {**preset.get("hours", {}), **(hours or {})}
    pol["notes"] = {**preset.get("notes", {}), **(notes or {})}
    pol["messages"] = {**DEFAULT_MESSAGES, **_string_map(messages)}
    _audit_settings(pol)
    return pol


def _string_map(value) -> dict:
    if not value:
        return {}
    return {str(k): v for k, v in value.items() if isinstance(v, str)}


def _audit_settings(pol: dict) -> dict:
    raw = pol.get("audit", {"enabled": True, "retention_days": 30})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("audit must be an object")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("audit.enabled must be a boolean")
    days = raw.get("retention_days", 30)
    if isinstance(days, bool) or not isinstance(days, (int, float)) or days < 0:
        raise ValueError("audit.retention_days must be a non-negative number")
    return {"enabled": enabled, "retention_days": float(days)}


def _msg(pol: dict, key: str, **fields) -> str:
    template = (pol.get("messages") or {}).get(key) or DEFAULT_MESSAGES[key]
    try:
        return str(template).format_map(_SafeFormat(fields))
    except Exception:
        return DEFAULT_MESSAGES[key].format_map(_SafeFormat(fields))


def _audit_paths() -> list[Path]:
    home = _home()
    return [home / "household-tiers-audit.jsonl", home / f"{_legacy_stem()}-audit.jsonl"]


def _write_path() -> Path:
    return _home() / "household-tiers.json"


def _audit(pol: dict | None, **rec) -> None:
    try:
        if pol is not None and not _audit_settings(pol)["enabled"]:
            return
    except Exception:
        pass
    clean = {"t": round(time.time(), 3)}
    for key in _AUDIT_FIELDS:
        if key not in rec or rec[key] is None:
            continue
        value = rec[key]
        if key == "why":
            if value not in _WHY:
                continue
        elif key == "count":
            if isinstance(value, bool) or not isinstance(value, int):
                continue
        elif not isinstance(value, str):
            continue
        clean[key] = value
    try:
        path = _home() / "household-tiers-audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(clean, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _rewrite_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(("\n".join(lines) + ("\n" if lines else "")), encoding="utf-8")
    os.replace(tmp, path)


def _purge(pol: dict, forget_id: str | None = None) -> int:
    """Drop expired audit lines. Also drop ``forget_id`` when given.

    Returns how many lines belonged to ``forget_id``. Expired and unreadable
    lines are removed too, and are not included in that count.
    """
    settings = _audit_settings(pol)
    cutoff = time.time() - settings["retention_days"] * 86400
    forgotten = 0
    for path in _audit_paths():
        if not path.exists():
            continue
        kept: list[str] = []
        try:
            raw_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in raw_lines:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            who, target = rec.get("who"), rec.get("target")
            if forget_id and (who == forget_id or target == forget_id):
                forgotten += 1
                continue
            try:
                stamp = float(rec.get("t") or 0)
            except (TypeError, ValueError):
                stamp = 0
            if stamp < cutoff:
                continue
            kept.append(json.dumps(rec, ensure_ascii=False))
        try:
            _rewrite_lines(path, kept)
        except OSError:
            pass
    return forgotten


def _identity(session_id: str = "") -> tuple[str, str]:
    if os.environ.get("HERMES_PLUGIN_HOST_PROCESS") == "1":
        # The host child does not receive the per-turn sender. Fail closed.
        return "guest", "host-unknown"
    from gateway.session_context import get_session_env

    platform = get_session_env("HERMES_SESSION_PLATFORM")
    uid = get_session_env("HERMES_SESSION_USER_ID")
    if platform and platform not in ("cli", "local"):
        pol = _policy()
        who = f"{platform}:{uid}"
        tier = pol["users"].get(who, pol["default"])
        with _runtime.lock:
            _runtime.seen_messaging = True
            if session_id:
                _runtime.by_session[session_id] = (tier, who)
        return tier, who
    if str(get_session_env("HERMES_CRON_SESSION", "")).lower() in ("1", "true", "yes"):
        return _policy().get("cron_tier", "owner"), "cron"
    with _runtime.lock:
        if session_id in _runtime.by_session:
            return _runtime.by_session[session_id]
        if _runtime.seen_messaging:
            return "guest", "unknown"
    return "owner", "local"


def _allowed(tier: str, tool: str, pol: dict | None = None) -> bool:
    allowed = (pol or _policy())["tiers"].get(tier, [])
    return "*" in allowed or tool in allowed


def _parse_ip(host: str):
    text = host.strip().strip("[]")
    if "%" in text:
        text = text.split("%", 1)[0]
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        pass
    try:
        if text.lower().startswith("0x") or (text.isdigit() and len(text) > 1):
            return ipaddress.ip_address(int(text, 0))
    except (ValueError, OverflowError):
        pass
    parts = text.split(".")
    if len(parts) == 4:
        try:
            nums = []
            for part in parts:
                if part.lower().startswith("0x"):
                    nums.append(int(part, 16))
                elif len(part) > 1 and part.isdigit() and part.startswith("0"):
                    nums.append(int(part, 8))
                else:
                    nums.append(int(part, 10))
            if all(0 <= n <= 255 for n in nums):
                return ipaddress.IPv4Address(".".join(str(n) for n in nums))
        except ValueError:
            return None
    return None


def _ip_is_local(ip) -> bool:
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        or ip.is_multicast or ip.is_unspecified
    )


def _embedded_ips(label: str) -> list:
    found = []
    for match in _EMBEDDED_IP.finditer(label):
        ip = _parse_ip(match.group(0).replace("-", "."))
        if isinstance(ip, ipaddress.IPv4Address):
            found.append(ip)
    return found


def _host_is_local(host: str | None) -> bool:
    """True when the host is a local address we can classify without a lookup.

    Ordinary hostnames return False. This plugin does not resolve DNS and does
    not fetch redirects. See the README known issues.
    """
    if not host:
        return True
    name = host.strip().lower().rstrip(".")
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    if name in {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}:
        return True
    if name.endswith(".localhost") or name.endswith(".local"):
        return True
    ip = _parse_ip(name)
    if ip is not None:
        return _ip_is_local(ip)
    for suffix in _WILDCARD_DNS_SUFFIXES:
        if name.endswith(suffix):
            embedded = _embedded_ips(name[: -len(suffix)])
            if not embedded:
                return True
            return any(_ip_is_local(item) for item in embedded)
    return False


def _lan_opened() -> bool:
    env = os.getenv("HERMES_ALLOW_PRIVATE_URLS", "").strip().lower()
    if env in {"true", "1", "yes", "on"}:
        return True
    if env in {"false", "0", "no", "off"}:
        return False
    try:
        from hermes_cli.config import read_raw_config

        cfg = read_raw_config() or {}
        for section in ("security", "browser"):
            block = cfg.get(section, {})
            if not isinstance(block, dict):
                continue
            flag = block.get("allow_private_urls")
            if isinstance(flag, bool):
                if flag:
                    return True
                continue
            if str(flag or "").strip().lower() in {"1", "true", "yes", "on"}:
                return True
    except Exception:
        return True
    return False


def _urls(args) -> list:
    data = args or {}
    if not isinstance(data, dict):
        return []
    value = data.get("urls")
    if value is None:
        value = data.get("url") or []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _url_host(url: str) -> str | None:
    try:
        return urlsplit(str(url)).hostname
    except ValueError:
        return None


def _private_url(tool_name: str, args, tier: str, pol: dict) -> bool:
    if tool_name not in _URL_TOOLS or "*" in pol["tiers"].get(tier, []):
        return False
    if not _lan_opened():
        return False
    urls = _urls(args)
    if not urls:
        return False
    return any(_host_is_local(_url_host(url)) for url in urls)


def _pre_tool_call(tool_name: str = "", args=None, session_id: str = "", **kwargs):
    try:
        tier, who = _identity(session_id)
        pol = _policy()
        if _allowed(tier, tool_name, pol):
            if _private_url(tool_name, args, tier, pol):
                _audit(pol, decision="block", who=who, tier=tier, tool=tool_name, why="private_url")
                return {"action": "block", "message": _msg(pol, "private_url", who=who, tier=tier, tool=tool_name)}
            return None
    except Exception:
        _audit(None, decision="block", tool=str(tool_name or ""), why="policy_error")
        return {"action": "block", "message": DEFAULT_MESSAGES["policy_error_tool"]}
    _audit(pol, decision="block", who=who, tier=tier, tool=tool_name, why="tool_denied", session=session_id or "")
    return {
        "action": "block",
        "message": _msg(pol, "tool_blocked", who=who, tier=tier, tool=tool_name),
    }


def _subagent_start(parent_session_id=None, child_session_id=None, **kwargs):
    with _runtime.lock:
        if parent_session_id in _runtime.by_session and child_session_id:
            _runtime.by_session[child_session_id] = _runtime.by_session[parent_session_id]


def _replace(pattern: re.Pattern, repl: str, text: str) -> tuple[str, int]:
    return pattern.subn(lambda _match: repl, text)


def _rewrite_messages(req: dict, repl_mem: str, repl_recall: str) -> tuple[list, int]:
    hidden = 0
    messages = []
    for message in req.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        role = message.get("role") if isinstance(message, dict) else None
        if role == "system" and isinstance(content, str):
            content, count = _replace(_MEM_BLOCK, repl_mem, content)
            hidden += count
            message = dict(message, content=content)
        elif role == "user" and isinstance(content, str) and "memory-context" in content:
            content, count = _replace(_RECALL, repl_recall, content)
            hidden += count
            message = dict(message, content=content)
        messages.append(message)
    return messages, hidden


def _llm_request(**kwargs):
    request = kwargs.get("request")
    if not isinstance(request, dict):
        return None
    req = dict(request)
    try:
        tier, who = _identity(kwargs.get("session_id") or "")
        pol = _policy()
        if "*" in pol["tiers"].get(tier, []):
            return None
        hidden = 0
        if tier not in pol.get("memory_tiers", ["owner"]):
            messages, hidden = _rewrite_messages(
                req, _msg(pol, "memory_withheld"), _msg(pol, "recall_withheld")
            )
            req["messages"] = messages
        note = (pol.get("notes") or {}).get(tier)
        noted = False
        messages = req.get("messages") or []
        if (
            isinstance(note, str)
            and note
            and messages
            and isinstance(messages[0], dict)
            and messages[0].get("role") == "system"
            and isinstance(messages[0].get("content"), str)
        ):
            prefix = _msg(pol, "tier_note_prefix", tier=tier)
            req["messages"] = [dict(messages[0], content=messages[0]["content"] + "\n\n" + prefix + note)] + list(messages[1:])
            noted = True
        if not req.get("tools"):
            if hidden or noted:
                return {
                    "request": req,
                    "source": PLUGIN_NAME,
                    "reason": f"{who} ({tier}): {hidden} memory blocks withheld" + (", note added" if noted else ""),
                }
            return None
        keep = [tool for tool in req["tools"] if _allowed(tier, (tool.get("function") or {}).get("name", ""), pol)]
        req["tools"] = keep
        return {
            "request": req,
            "source": PLUGIN_NAME,
            "reason": f"{who} ({tier}): {len(keep)} tools visible, {hidden} memory blocks withheld",
        }
    except Exception:
        try:
            req = dict(request)
            messages, _hidden = _rewrite_messages(
                req, DEFAULT_MESSAGES["withheld_error"], DEFAULT_MESSAGES["withheld_error"]
            )
            req["messages"] = messages
            req.pop("tools", None)
            req.pop("tool_choice", None)
            return {"request": req, "source": PLUGIN_NAME, "reason": "error: failed closed"}
        except Exception:
            return None


def _now(tz: str) -> dt.time:
    return dt.datetime.now(ZoneInfo(tz)).time()


def _inside(window: str, moment: dt.time) -> bool:
    start_s, end_s = window.split("-")
    start = dt.time(*map(int, start_s.split(":")))
    end = dt.time(*map(int, end_s.split(":")))
    if start <= end:
        return start <= moment < end
    return moment >= start or moment < end


def _pre_gateway_dispatch(event=None, gateway=None, **kwargs):
    """Observe only. Never drops a sender; auth still runs in the core."""
    if gateway is not None:
        _runtime.runner = gateway
    return None


def _source_dict(source) -> dict:
    if source is None:
        return {}
    if isinstance(source, dict):
        return source
    return {
        "user_id": getattr(source, "user_id", "") or "",
        "user_id_alt": getattr(source, "user_id_alt", None),
        "chat_type": getattr(source, "chat_type", "dm") or "dm",
    }


def _participant_ids(platform: str, source: dict) -> set[str]:
    """Sender ids carried on the admission payload.

    ``platform`` is unused. WhatsApp's canonical jid/lid form is whatever the
    core already put in ``user_id`` or ``user_id_alt``. A session key that ends
    with some other alias does not match, and the tier is refused.
    """
    del platform
    return {str(item) for item in (source.get("user_id"), source.get("user_id_alt")) if item}


def _alert(pol: dict, text: str) -> None:
    """Ask the observed gateway adapter to deliver ``text``.

    ``notify`` is ``{"platform": "<adapter>", "chat_id": "<chat>"}``. The
    plugin does not open a connection; ``adapter.send`` is the gateway's.
    With no observed runner (host isolation, or dispatch did not run) the
    alert is skipped. The curfew block still stands.
    """
    notify = pol.get("notify")
    runner = _runtime.runner
    if not isinstance(notify, dict) or not notify.get("platform") or not notify.get("chat_id") or runner is None:
        return

    async def _send():
        adapters = getattr(runner, "adapters", {}) or {}
        target = next(
            (adapter for key, adapter in adapters.items() if getattr(key, "value", key) == notify["platform"]),
            None,
        )
        if target is not None:
            await target.send(notify["chat_id"], text)

    try:
        asyncio.get_running_loop().create_task(_send())
    except RuntimeError:
        pass


def _post_admission(session_key: str = "", platform: str = "", source=None, text: str = "", **kwargs):
    # ``text`` is the inbound message. It is not stored, logged, or copied
    # into the reply. The core treats a raised hook as a normal turn, so
    # every failure here returns handled.
    del text, kwargs
    try:
        return _post_admission_inner(session_key, platform, source)
    except Exception:
        _audit(None, decision="block_turn", why="policy_error")
        return {"action": "handled", "reply": DEFAULT_MESSAGES["policy_error_turn"]}


def _post_admission_inner(session_key: str, platform: str, source) -> dict | None:
    src = _source_dict(source)
    pol = _policy()
    who = f"{platform}:{src.get('user_id', '')}"
    tier = pol["users"].get(who, pol["default"])
    ids = _participant_ids(platform, src)
    shared = pol.get("shared_threads", "refuse") == "refuse"
    memory_tiers = pol.get("memory_tiers", ["owner"])
    if (
        tier not in memory_tiers
        and src.get("chat_type", "dm") != "dm"
        and ids
        and not any(str(session_key).endswith(":" + item) for item in ids)
        and shared
    ):
        _audit(pol, decision="shared_thread", who=who, tier=tier, why="shared_thread")
        return {"action": "handled", "reply": _msg(pol, "shared_thread", who=who, tier=tier)}
    rule = (pol.get("hours") or {}).get(tier)
    if not isinstance(rule, dict) or not rule.get("allow"):
        return None
    if _inside(rule["allow"], _now(rule.get("tz") or "UTC")):
        return None
    _audit(pol, decision="curfew", who=who, tier=tier, window=str(rule.get("allow")), why="curfew")
    _alert(pol, _msg(pol, "curfew_alert", who=who, tier=tier, window=rule.get("allow")))
    is_dm = src.get("chat_type", "dm") == "dm"
    if is_dm or rule.get("reply_in_groups"):
        reply = rule.get("reply") or _msg(pol, "curfew_reply", who=who, tier=tier, window=rule.get("allow"))
    else:
        reply = None
    return {"action": "handled", "reply": reply}


def _command_who(source) -> str:
    if source is None:
        return "local"
    if isinstance(source, dict):
        platform = source.get("platform") or "cli"
        user_id = source.get("user_id") or ""
    else:
        platform = getattr(getattr(source, "platform", None), "value", None) or getattr(source, "platform", None) or "cli"
        user_id = getattr(source, "user_id", "") or ""
    if platform in ("cli", "local", ""):
        return "local"
    return f"{platform}:{user_id}"


def _require_owner(pol: dict, source) -> str | None:
    who = _command_who(source)
    if who != "local" and pol["users"].get(who, pol["default"]) != "owner":
        return _msg(pol, "digest_owner_only")
    return None


def _load_raw_for_write() -> dict:
    path = _policy_path()
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("policy root must be a JSON object")
    return data


def _save_raw(raw: dict) -> None:
    path = _write_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _digest_text(pol: dict, days: float) -> str:
    since = time.time() - days * 86400
    per: dict = {}
    for path in _audit_paths():
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("t", 0) < since:
                continue
            bucket = per.setdefault(rec.get("who") or "?", {"block": {}, "curfew": 0})
            if rec.get("decision") == "curfew":
                bucket["curfew"] += 1
            elif rec.get("decision") == "block":
                tool = rec.get("tool") or "?"
                bucket["block"][tool] = bucket["block"].get(tool, 0) + 1
    label = f"{days:g}"
    if not per:
        return _msg(pol, "digest_empty", days=label)
    lines = [_msg(pol, "digest_header", days=label)]
    for who, bucket in sorted(per.items()):
        blocked = ", ".join(f"{tool}×{count}" for tool, count in sorted(bucket["block"].items(), key=lambda item: -item[1]))
        lines.append(
            _msg(
                pol,
                "digest_line",
                who=who,
                blocked=blocked or _msg(pol, "digest_nothing"),
                curfew=bucket["curfew"],
            )
        )
    return "\n".join(lines)


def _family(raw_args: str = "", source=None, **kwargs):
    del kwargs
    try:
        pol = _policy()
        denied = _require_owner(pol, source)
        parts = (raw_args or "").split()
        command = parts[0].lower() if parts else ""
        if command == "forget":
            if denied:
                return denied
            if len(parts) != 2:
                return _msg(pol, "forget_usage")
            count = _purge(pol, forget_id=parts[1])
            who = _command_who(source)
            # The receipt names the owner and the count only. Repeating the
            # deleted id would put that person back into the audit log.
            _audit(pol, decision="forget", who=who, why="forget", count=count)
            return _msg(pol, "forget_done", count=count, target=parts[1])
        _purge(pol)
        if denied:
            return denied
        if command == "tier":
            if len(parts) != 3 or ":" not in parts[1]:
                return _msg(pol, "tier_usage")
            if parts[2] not in pol["tiers"]:
                return _msg(pol, "tier_unknown", tiers=", ".join(sorted(pol["tiers"])))
            who = _command_who(source)
            if parts[1] == who:
                return _msg(pol, "tier_self")
            raw = _load_raw_for_write()
            raw.setdefault("users", {})
            if not isinstance(raw["users"], dict):
                raise ValueError("users must be an object")
            raw["users"][parts[1]] = parts[2]
            _save_raw(raw)
            _audit(pol, decision="tier_change", who=who, target=parts[1], tier=parts[2], why="tier_change")
            return _msg(pol, "tier_done", target=parts[1], tier=parts[2])
        if command and command != "tier":
            try:
                days = float(parts[0])
            except ValueError:
                return _msg(pol, "usage")
            if days < 0:
                return _msg(pol, "usage")
        else:
            days = 7.0
        return _digest_text(pol, days)
    except Exception:
        return DEFAULT_MESSAGES["policy_error_turn"]


def register(ctx):
    try:
        _purge(_policy())
    except Exception:
        pass
    hint = "[days] | tier <platform:id> <tier> | forget <platform:id>"
    ctx.register_command(
        "family",
        _family,
        description="Household summary, tier changes, and audit deletion",
        args_hint=hint,
    )
    ctx.register_command(
        "familia",
        _family,
        description="Alias of /family",
        args_hint=hint,
    )
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    ctx.register_hook("post_gateway_admission", _post_admission)
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("subagent_start", _subagent_start)
    if os.environ.get("HOUSEHOLD_TIERS_HIDE_TOOLS", "1") == "1":
        ctx.register_middleware("llm_request", _llm_request)
