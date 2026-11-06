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

No HTTP calls and no telemetry. URL tools for a non-owner tier may resolve a hostname with the system resolver. State is the two files under HERMES_HOME named in the README. A parsed policy and unchanged memory files are reused; secret-directory paths are reused until a parent directory of those paths changes.
"""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import ipaddress
import json
import os
import re
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

PLUGIN_NAME = "household-tiers"

DEFAULT_TIERS = {
    "owner": ["*"],
    # No file tools. A custom policy may add them; non-owner file access is
    # then limited to file_roots (default empty) and never to credential or
    # browser-profile directories.
    "user": ["web_search", "web_extract", "todo", "todo_list"],
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
            "kid": {"allow": "08:00-21:00"},
            "guest": {"allow": "08:00-21:00"},
        },
    }
}

DEFAULT_MESSAGES = {
    "tool_blocked": "household-tiers: {who} ({tier}) may not use {tool}. Ask the owner to change your tier.",
    "private_url": "household-tiers: {who} ({tier}) may not open local-network addresses.",
    "path_blocked": "household-tiers: {who} ({tier}) may not read that path.",
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
_FILE_TOOLS = {"read_file", "search_files"}
# Always blocked for a non-owner file tool, even inside file_roots.
# Joined onto Path.home() and the OS account home.
_SECRET_DIR_PARTS = (
    (".ssh",),
    (".aws",),
    (".gnupg",),
    (".kube",),
    (".docker",),
    (".azure",),
    (".config", "gh"),
    (".config", "gcloud"),
    (".mozilla",),
    (".config", "google-chrome"),
    (".config", "chromium"),
    (".config", "microsoft-edge"),
    (".config", "BraveSoftware"),
    (".config", "opera"),
    (".config", "vivaldi"),
    ("Library", "Application Support", "Google", "Chrome"),
    ("Library", "Application Support", "Chromium"),
    ("Library", "Application Support", "Microsoft Edge"),
    ("Library", "Application Support", "BraveSoftware"),
    ("Library", "Application Support", "Firefox"),
    ("Library", "Safari"),
    ("AppData", "Local", "Google", "Chrome"),
    ("AppData", "Local", "Chromium"),
    ("AppData", "Local", "Microsoft", "Edge"),
    ("AppData", "Local", "BraveSoftware"),
    ("AppData", "Roaming", "Mozilla"),
)
_AUDIT_FIELDS = ("decision", "who", "tier", "tool", "why", "session", "window", "target", "count")
_WHY = {"private_url", "private_path", "policy_error", "tool_denied", "shared_thread", "curfew", "tier_change", "forget"}
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}\Z")
_HEADER_MARKERS = ("MEMORY (your personal notes)", "USER PROFILE (who the user is)")
# Hermes renders a memory block as a boxed header plus entries joined by §.
# Entries may contain blank lines. The block ends at the entry text from the
# owner's files when that matches, otherwise at the next boxed header or at a
# section Hermes joins after memory (plugin sections, profile, timestamp,
# runtime). A blank line is not the end.
_BLOCK_OPEN = re.compile(
    r"═{10,}\n(?:MEMORY \(your personal notes\)|USER PROFILE \(who the user is\))[^\n]*\n═{10,}\n"
)
# Anchors from agent/system_prompt.py volatile tier, after _memory_parts.
_POST_MEMORY_ANCHORS = (
    "<!-- hermes-plugin-sections:start -->",
    "Active Hermes profile:",
    "Conversation started:",
    "Timezone:",
    "# Hermes runtime environment",
    "MEMORY PROVIDER CONTEXT:",
)
_INJECTED_ROLES = {"system", "developer"}
_RECALL = re.compile(r"<\s*memory-context\s*>[\s\S]*?</\s*memory-context\s*>", re.I)
_EMBEDDED_IP = re.compile(r"(?:\d{1,3}[.-]){3}\d{1,3}")
# Wildcard DNS names that encode an address in the hostname. Classified locally.
_WILDCARD_DNS_SUFFIXES = (".nip.io", ".sslip.io", ".xip.io", ".localtest.me")
# search_files walks the root for symlinks. Past this many directory entries the
# walk stops and the call is refused, so a huge tree cannot stall the hook.
_SYMLINK_WALK_LIMIT = 20000
# Compaction handoff text. Locked to Hermes by tests; the auxiliary summary
# request itself never reaches this plugin.
_SUMMARY_PREFIXES = ("[CONTEXT COMPACTION", "[CONTEXT SUMMARY]")
_SUMMARY_MERGE = "[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]"
_PROVIDER_TAG = re.compile(
    r"<\s*memory-provider-context\s*>[\s\S]*?</\s*memory-provider-context\s*>",
    re.I,
)


class _Runtime:
    """Process-wide hook state. See the module docstring."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.by_session: dict = {}
        self.seen_messaging = False
        self.runner = None
        self.ctx = None
        self.policy_key = None
        self.policy = None
        self.memory_files: dict = {}
        self.blocked_key = None
        self.blocked = None


_runtime = _Runtime()


class _SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _home() -> Path:
    """Active profile home. Same resolver ``get_memory_dir()`` uses."""
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home())


def _policy_path() -> Path | None:
    home = _home()
    current = home / "household-tiers.json"
    return current if current.exists() else None


def _read_policy_file() -> dict:
    path = _policy_path()
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("policy root must be a JSON object")
    return data


def _policy_key(path: Path):
    """Identity of the policy file. ``None`` as the stat means the file is absent."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return (str(path), None)
    return (str(path), st.st_mtime_ns, st.st_size, st.st_ino)


def _policy_from_raw(raw: dict) -> dict:
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
    pol["timezone"] = _timezone(raw.get("timezone", "UTC"))
    pol["hours"] = _hours_with_timezone({**preset.get("hours", {}), **(hours or {})}, pol["timezone"])
    pol["notes"] = {**preset.get("notes", {}), **(notes or {})}
    pol["messages"] = {**DEFAULT_MESSAGES, **_string_map(messages)}
    pol["memory_tiers"] = _memory_tiers(raw.get("memory_tiers", ["owner"]))
    roots = raw.get("file_roots", [])
    if roots is None:
        roots = []
    pol["file_roots"] = _file_roots(roots)
    _audit_settings(pol)
    return pol


def _policy() -> dict:
    """Parsed policy. Reused while the file's inode, mtime, and size are unchanged."""
    path = _home() / "household-tiers.json"
    key = _policy_key(path)
    with _runtime.lock:
        if _runtime.policy_key == key and _runtime.policy is not None:
            return _runtime.policy
    if key[1] is None:
        raw: dict = {}
    else:
        text = path.read_text(encoding="utf-8")
        # A writer may have replaced the file between stat and read.
        key_after = _policy_key(path)
        if key_after != key:
            key = key_after
            text = path.read_text(encoding="utf-8") if key[1] is not None else ""
        raw = json.loads(text) if key[1] is not None else {}
        if not isinstance(raw, dict):
            raise ValueError("policy root must be a JSON object")
    pol = _policy_from_raw(raw)
    with _runtime.lock:
        _runtime.policy_key = key
        _runtime.policy = pol
    return pol


def _timezone(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timezone must be a time zone name")
    ZoneInfo(value.strip())
    return value.strip()


def _hours_with_timezone(hours: dict, timezone: str) -> dict:
    out = {}
    for tier, rule in hours.items():
        if not isinstance(rule, dict) or not isinstance(rule.get("allow"), str):
            raise ValueError("hours entries must be objects with an allow window")
        copied = dict(rule)
        if not copied.get("tz"):
            copied["tz"] = timezone
        else:
            _timezone(copied["tz"])
        out[tier] = copied
    return out


def _memory_tiers(value) -> list:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError("memory_tiers must be a list of tier names")
    return list(value)


def _file_roots(value) -> list[str]:
    """Absolute roots a non-owner file tool may use. Empty allows nothing."""
    if not isinstance(value, list):
        raise ValueError("file_roots must be a list of path strings")
    roots: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("file_roots must be a list of path strings")
        expanded = Path(os.path.expanduser(item.strip()))
        if not expanded.is_absolute():
            raise ValueError("file_roots entries must be absolute after ~ expansion")
        roots.append(str(expanded))
    return roots


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
    return [_home() / "household-tiers-audit.jsonl"]


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
        elif key == "tool":
            if not isinstance(value, str) or _TOOL_NAME_RE.fullmatch(value) is None:
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
    pol = pol or _policy()
    # The memory tool reads and writes the shared MEMORY.md / USER.md. A tier
    # that does not keep that memory does not get the tool, even from ["*"].
    if tool == "memory" and tier not in pol["memory_tiers"]:
        return False
    allowed = pol["tiers"].get(tier, [])
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


def _resolved_ips(host: str):
    """Addresses from the system resolver. None when lookup fails (fail closed)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return None
    found = []
    for info in infos:
        try:
            found.append(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
        except (ValueError, IndexError):
            return None
    return found or None


def _host_is_local(host: str | None) -> bool:
    """True for a local address, a local name, or a hostname that resolves to one.

    IP literals and encoded forms are classified locally. Other names are
    resolved once. HTTP redirects are not followed, and a later DNS change is
    not seen. A failed lookup is treated as local.
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
    resolved = _resolved_ips(name)
    if resolved is None:
        return True
    return any(_ip_is_local(item) for item in resolved)


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
    # Owner is the only tier that skips this check. A custom ["*"] list does not.
    if tool_name not in _URL_TOOLS or tier == "owner":
        return False
    if not _lan_opened():
        return False
    urls = _urls(args)
    if not urls:
        return False
    return any(_host_is_local(_url_host(url)) for url in urls)


def _session_base() -> Path:
    """Session directory for a relative file path.

    ``ctx.session_cwd()`` is the calling session's workspace, not the gateway
    process cwd. No registered context, or an empty or relative answer, fails
    closed (the relative path is refused).
    """
    ctx = _runtime.ctx
    reader = getattr(ctx, "session_cwd", None) if ctx is not None else None
    if not callable(reader):
        raise RuntimeError("session cwd is unavailable")
    text = str(reader() or "").strip()
    if not text:
        raise RuntimeError("session cwd is empty")
    path = Path(os.path.expanduser(text))
    if not path.is_absolute():
        raise RuntimeError("session cwd is not absolute")
    return path


def _host_realpath(path: Path) -> str:
    """realpath, including a missing tail: symlinks in the existing prefix are followed."""
    return os.path.normcase(os.path.realpath(os.path.expanduser(str(path))))


def _same_or_under(child: str, parent: str) -> bool:
    if child == parent:
        return True
    prefix = parent if parent.endswith(os.sep) else parent + os.sep
    return child.startswith(prefix)


def _touches(path: str, blocked: str) -> bool:
    """True when ``path`` is inside ``blocked`` or a walk of ``path`` would enter it."""
    return _same_or_under(path, blocked) or _same_or_under(blocked, path)


def _account_homes() -> list[Path]:
    homes = [Path.home()]
    try:
        import pwd

        homes.append(Path(pwd.getpwuid(os.getuid()).pw_dir))
    except Exception:
        pass
    return homes


def _secret_dirs() -> list[str]:
    found: list[str] = []
    for home in _account_homes():
        for parts in _SECRET_DIR_PARTS:
            found.append(_host_realpath(home.joinpath(*parts)))
    return found


def _blocked_paths() -> list[str]:
    """Profile home plus credential and browser paths.

    Recomputed when a parent of those paths changes (a directory's mtime moves
    when an entry is added or replaced). The user path itself is resolved on
    every call.
    """
    homes = _account_homes()
    hermes_home = _home()
    parents = [hermes_home]
    for home in homes:
        parents.extend((
            home,
            home / ".config",
            home / "Library",
            home / "Library" / "Application Support",
            home / "AppData",
            home / "AppData" / "Local",
            home / "AppData" / "Roaming",
        ))
    stamps = []
    for parent in parents:
        try:
            # Follow a symlink: a symlinked home must invalidate when the
            # target directory gains or replaces an entry.
            st = os.stat(parent)
            stamps.append((str(parent), st.st_mtime_ns, st.st_ino))
        except OSError:
            stamps.append((str(parent), None, None))
    key = tuple(stamps)
    cached = _runtime.blocked
    if cached is not None and _runtime.blocked_key == key:
        return cached
    blocked = [_host_realpath(hermes_home)]
    blocked.extend(_secret_dirs())
    found: list[str] = []
    for item in blocked:
        if item not in found:
            found.append(item)
    _runtime.blocked_key = key
    _runtime.blocked = found
    return found


def _tree_has_escaping_symlink(root: str, roots: list[str], blocked: list[str]) -> bool:
    """True when a directory entry is a symlink whose target leaves ``roots`` or touches ``blocked``.

    The walk does not follow symlinks to list them. A symlink whose target is a
    directory inside ``roots`` is scanned too. Past ``_SYMLINK_WALK_LIMIT``
    entries, or on any filesystem error, the call is refused.
    """
    seen = 0
    stack = [root]
    visited: set[str] = set()
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        try:
            iterator = os.scandir(current)
        except OSError:
            return True
        with iterator:
            for entry in iterator:
                seen += 1
                if seen > _SYMLINK_WALK_LIMIT:
                    return True
                try:
                    is_link = entry.is_symlink()
                except OSError:
                    return True
                if is_link:
                    try:
                        target = _host_realpath(Path(entry.path))
                    except OSError:
                        return True
                    if any(_touches(target, item) for item in blocked):
                        return True
                    if not any(_same_or_under(target, item) for item in roots):
                        return True
                    if os.path.isdir(target) and target not in visited:
                        stack.append(target)
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                except OSError:
                    return True
    return False


def _resolve_user_path(path_str: str) -> str:
    text = str(path_str if path_str is not None else "").strip() or "."
    candidate = Path(os.path.expanduser(text))
    if not candidate.is_absolute():
        candidate = _session_base() / candidate
    return _host_realpath(candidate)


def _path_args(args) -> list[str]:
    paths: list[str] = []
    data = args if isinstance(args, dict) else {}
    for key in ("path", "file", "file_path", "directory", "root", "target"):
        value = data.get(key)
        if isinstance(value, str):
            paths.append(value)
        elif isinstance(value, list):
            paths.extend(item for item in value if isinstance(item, str))
    return paths or ["."]


def _file_path_denied(args, pol: dict, tool_name: str = "") -> bool:
    """Non-owner file tools: allow only file_roots, never secrets or the profile home.

    Any resolution failure is a denial. An empty ``file_roots`` denies every path.
    ``search_files`` also refuses a directory that contains a symlink out of the
    roots or into a blocked path.
    """
    try:
        roots = [_host_realpath(Path(item)) for item in pol.get("file_roots") or []]
        blocked = _blocked_paths()
        for raw in _path_args(args):
            resolved = _resolve_user_path(raw)
            if not any(_same_or_under(resolved, root) for root in roots):
                return True
            if any(_touches(resolved, item) for item in blocked):
                return True
            if tool_name == "search_files" and os.path.isdir(resolved):
                if _tree_has_escaping_symlink(resolved, roots, blocked):
                    return True
        return False
    except Exception:
        return True


def _pre_tool_call(tool_name: str = "", args=None, session_id: str = "", **kwargs):
    try:
        tier, who = _identity(session_id)
        pol = _policy()
        if _allowed(tier, tool_name, pol):
            # The owner tier is not limited by file_roots. Every other tier is,
            # including one whose tool list is ["*"].
            if tier != "owner" and tool_name in _FILE_TOOLS and _file_path_denied(args, pol, tool_name):
                _audit(pol, decision="block", who=who, tier=tier, tool=tool_name, why="private_path")
                return {"action": "block", "message": _msg(pol, "path_blocked", who=who, tier=tier, tool=tool_name)}
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


def _memory_chunks(body: str) -> list[str]:
    """Distinctive pieces of a memory block: delimiter entries and their paragraphs."""
    try:
        from tools.memory_tool_store import ENTRY_DELIMITER

        parts = body.split(ENTRY_DELIMITER)
    except Exception:
        parts = [body]
    found: list[str] = []
    for entry in parts:
        entry = entry.strip()
        pieces = [entry] if entry else []
        pieces.extend(part.strip() for part in re.split(r"\n\s*\n", entry) if part.strip())
        for piece in pieces:
            if len(piece) >= 8 and piece not in found:
                found.append(piece)
    return found


def _split_memory_entries(raw: str) -> list[str]:
    """Non-empty entries split on the full delimiter. A bare section sign stays put.

    This does not call ``MemoryStore._parse_entries``. A core change to that
    helper cannot drop entry text from the path and memory checks here.
    """
    from tools.memory_tool_store import ENTRY_DELIMITER

    return [entry for entry in (part.strip() for part in str(raw).split(ENTRY_DELIMITER)) if entry]


def _memory_file(target: str) -> tuple[list[str], list[str]] | None:
    """Entries and scrub chunks for one memory file. Reused while the file is unchanged."""
    try:
        from tools.memory_tool import get_memory_dir

        name = "USER.md" if target == "user" else "MEMORY.md"
        path = get_memory_dir() / name
        if not path.is_file():
            return None
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size, st.st_ino)
        cached = _runtime.memory_files.get(str(path))
        if cached is not None and cached[0] == key:
            return cached[1], cached[2]
        raw = path.read_text(encoding="utf-8-sig")
        entries = _split_memory_entries(raw)
        chunks: list[str] = []
        for entry in entries:
            chunks.extend(_memory_chunks(entry))
        _runtime.memory_files[str(path)] = (key, entries, chunks)
        return entries, chunks
    except Exception:
        return None


def _disk_entries(target: str) -> list[str] | None:
    loaded = _memory_file(target)
    if loaded is None:
        return None
    return loaded[0]


def _memory_block_end(text: str, match: re.Match) -> int:
    """Exclusive end of one boxed block, including a following provider block.

    When the on-disk entry text matches, that body is the end of the built-in
    block. Hermes then joins ``system_prompt_block()`` before the next section
    anchor (plugin sections, profile, timestamp, runtime). That gap is removed
    with the box. A later boxed header is not consumed. An anchor inside the
    matched entry text does not cut the entry short.
    """
    start = match.end()
    header = text[match.start():match.end()]
    target = "user" if "USER PROFILE" in header else "memory"
    entries = _disk_entries(target)
    content_end = None
    if entries:
        from tools.memory_tool_store import ENTRY_DELIMITER

        body = ENTRY_DELIMITER.join(entries)
        if body and text.startswith(body, start):
            content_end = start + len(body)
    search_from = content_end if content_end is not None else start
    candidates = []
    nxt = _BLOCK_OPEN.search(text, search_from)
    if nxt:
        candidates.append(nxt.start())
    for anchor in _POST_MEMORY_ANCHORS:
        at = text.find("\n\n" + anchor, search_from)
        if at != -1:
            candidates.append(at)
    boundary = min(candidates) if candidates else len(text)
    if content_end is None:
        return boundary
    if boundary > content_end:
        return boundary
    return content_end


def _strip_memory_blocks(text: str, repl: str) -> tuple[str, int]:
    hidden = 0
    out: list[str] = []
    pos = 0
    while pos < len(text):
        match = _BLOCK_OPEN.search(text, pos)
        if not match:
            out.append(text[pos:])
            break
        out.append(text[pos:match.start()])
        out.append(repl)
        hidden += 1
        pos = _memory_block_end(text, match)
    return "".join(out), hidden


def _store_chunks() -> list[str]:
    """Entry text currently in the owner's memory files, when those files exist."""
    chunks: list[str] = []
    for target in ("memory", "user"):
        loaded = _memory_file(target)
        if loaded is None:
            continue
        chunks.extend(loaded[1])
    return chunks


def _scrub_owner_entries(text: str, repl: str) -> tuple[str, int]:
    """Redact on-disk entry text that survived the block strip. Does not refuse."""
    pieces = sorted(_store_chunks(), key=len, reverse=True)
    hits = 0
    for piece in pieces:
        if piece and piece in text:
            text = text.replace(piece, repl)
            hits += 1
    return text, hits


def _clean_injected(text: str, repl_mem: str, repl_recall: str) -> tuple[str, int]:
    text, memory_hits = _strip_memory_blocks(text, repl_mem)
    text, recall_hits = _replace(_RECALL, repl_recall, text)
    text, provider_hits = _replace(_PROVIDER_TAG, repl_mem, text)
    text, scrub_hits = _scrub_owner_entries(text, repl_mem)
    return text, memory_hits + recall_hits + provider_hits + scrub_hits


def _summary_span(text: str) -> tuple[int, int] | None:
    """Byte span of a compaction handoff inside one message, if the markers match."""
    stripped = text.lstrip()
    offset = len(text) - len(stripped)
    if stripped.startswith(_SUMMARY_PREFIXES):
        return offset, len(text)
    at = text.find(_SUMMARY_MERGE)
    if at == -1:
        return None
    return at, len(text)


def _scrub_summary_portion(text: str, repl_mem: str) -> tuple[str, int]:
    """Redact owner entries inside a compaction handoff. Leave the rest of the message."""
    span = _summary_span(text)
    if span is None:
        return text, 0
    start, end = span
    chunk = text[start:end]
    chunk, provider_hits = _replace(_PROVIDER_TAG, repl_mem, chunk)
    chunk, scrub_hits = _scrub_owner_entries(chunk, repl_mem)
    return text[:start] + chunk + text[end:], provider_hits + scrub_hits


def _clean_text(text: str, injected: bool, repl_mem: str, repl_recall: str) -> tuple[str, int]:
    if injected:
        return _clean_injected(text, repl_mem, repl_recall)
    # Recall tags are Hermes-injected even when they sit on a user message.
    # The kid's own words, including a pasted header or an entry guess, stay.
    # A compaction handoff is not the kid's words: entry text in that span goes.
    text, recall_hits = _replace(_RECALL, repl_recall, text)
    text, summary_hits = _scrub_summary_portion(text, repl_mem)
    return text, recall_hits + summary_hits


def _clean_parts(content, injected: bool, repl_mem: str, repl_recall: str):
    if isinstance(content, str):
        return _clean_text(content, injected, repl_mem, repl_recall)
    if isinstance(content, list):
        cleaned = []
        hidden = 0
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                new_item = dict(item)
                new_item["text"], count = _clean_text(item["text"], injected, repl_mem, repl_recall)
                hidden += count
                cleaned.append(new_item)
                continue
            new_item, count = _clean_parts(item, injected, repl_mem, repl_recall)
            hidden += count
            cleaned.append(new_item)
        return cleaned, hidden
    return content, 0


def _clean_message(message, repl_mem: str, repl_recall: str):
    if not isinstance(message, dict):
        return message, 0
    injected = message.get("role") in _INJECTED_ROLES
    new = dict(message)
    hidden = 0
    for key in ("content", "text"):
        if key not in message:
            continue
        cleaned, count = _clean_parts(message[key], injected, repl_mem, repl_recall)
        new[key] = cleaned
        hidden += count
    return new, hidden


def _clean_request(request: dict, repl_mem: str, repl_recall: str):
    req = copy.deepcopy(request)
    hidden = 0
    for key in ("system", "instructions"):
        if key not in req:
            continue
        cleaned, count = _clean_parts(req[key], True, repl_mem, repl_recall)
        req[key] = cleaned
        hidden += count
    for key in ("messages", "input"):
        items = req.get(key)
        if not isinstance(items, list):
            continue
        cleaned_items = []
        for item in items:
            new_item, count = _clean_message(item, repl_mem, repl_recall)
            hidden += count
            cleaned_items.append(new_item)
        req[key] = cleaned_items
    return req, hidden


def _injected_strings(req) -> list[str]:
    found: list[str] = []

    def take(content):
        if isinstance(content, str):
            found.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    found.append(item["text"])
                elif isinstance(item, str):
                    found.append(item)

    for key in ("system", "instructions"):
        if key in req:
            take(req[key])
    for key in ("messages", "input"):
        items = req.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("role") in _INJECTED_ROLES:
                take(item.get("content"))
                take(item.get("text"))
    return found


def _structural_block_remains(req) -> bool:
    """A boxed owner-memory header is still in an injected section.

    Kid and tool text is not inspected, so pasting the header cannot refuse the turn.
    """
    return any(_BLOCK_OPEN.search(text) for text in _injected_strings(req))


def _tool_name(tool) -> str:
    """OpenAI nested name, Anthropic/Codex top-level name, or Bedrock toolSpec name."""
    if not isinstance(tool, dict):
        return ""
    spec = tool.get("toolSpec")
    if isinstance(spec, dict) and isinstance(spec.get("name"), str) and spec["name"]:
        return spec["name"]
    function = tool.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str) and function["name"]:
        return function["name"]
    name = tool.get("name")
    return name if isinstance(name, str) else ""


def _is_tool_marker(tool) -> bool:
    """Bedrock cachePoint and similar markers are not callable tools."""
    return isinstance(tool, dict) and "cachePoint" in tool and "toolSpec" not in tool


def _append_note(req: dict, text: str) -> bool:
    if isinstance(req.get("instructions"), str):
        req["instructions"] += text
        return True
    system = req.get("system")
    if isinstance(system, str):
        req["system"] = system + text
        return True
    if isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                block["text"] += text
                return True
    messages = req.get("messages")
    if isinstance(messages, list):
        for index, message in enumerate(messages):
            if not isinstance(message, dict) or message.get("role") != "system":
                continue
            content = message.get("content")
            if isinstance(content, str):
                messages[index] = dict(message, content=content + text)
                return True
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        block["text"] += text
                        return True
    return False


def _refusal_request(req: dict, refusal: str) -> dict:
    """A same-family payload whose text is only the refusal and which has no tools."""
    out = {key: req[key] for key in ("model", "max_tokens", "max_output_tokens", "temperature", "store") if key in req}
    if "instructions" in req or "input" in req:
        out["instructions"] = refusal
        out["input"] = [{"type": "message", "role": "user", "content": refusal}]
        out.setdefault("store", False)
        return out
    if "system" in req:
        out["system"] = refusal
        out["messages"] = [{"role": "user", "content": refusal}]
        return out
    out["messages"] = [{"role": "user", "content": refusal}]
    return out


def _filter_tool_lists(req: dict, tier: str, pol: dict):
    """Drop disallowed tools from every provider list this payload uses.

    Returns how many tools remain, or None when the payload has no tool list.
    """
    visible = None
    lists = []
    tools = req.get("tools")
    if isinstance(tools, list):
        lists.append(("tools", tools))
    config = req.get("toolConfig")
    if isinstance(config, dict) and isinstance(config.get("tools"), list):
        lists.append(("toolConfig", config["tools"]))
    if not lists:
        return None
    for key, tools in lists:
        kept = [tool for tool in tools if _is_tool_marker(tool) or _allowed(tier, _tool_name(tool), pol)]
        callable_kept = [tool for tool in kept if not _is_tool_marker(tool)]
        visible = len(callable_kept)
        if key == "tools":
            if kept:
                req["tools"] = kept
            else:
                req.pop("tools", None)
                req.pop("tool_choice", None)
        elif callable_kept:
            config["tools"] = kept
        else:
            req.pop("toolConfig", None)
    return visible


def _llm_result(req: dict, reason: str) -> dict:
    return {"request": req, "source": PLUGIN_NAME, "reason": reason}


def _llm_request(**kwargs):
    request = kwargs.get("request")
    if not isinstance(request, dict):
        return None
    try:
        tier, who = _identity(kwargs.get("session_id") or "")
        pol = _policy()
        # Owner (and any other memory tier) with every tool has nothing to change.
        # A custom ["*"] list on a tier that does not keep memory still strips.
        if "*" in pol["tiers"].get(tier, []) and tier in pol["memory_tiers"]:
            return None
        repl_mem = _msg(pol, "memory_withheld")
        repl_recall = _msg(pol, "recall_withheld")
        if tier not in pol["memory_tiers"]:
            req, hidden = _clean_request(request, repl_mem, repl_recall)
            if _structural_block_remains(req):
                return _llm_result(
                    _refusal_request(req, DEFAULT_MESSAGES["policy_error_tool"]),
                    f"{who} ({tier}): owner memory block still present; refused",
                )
        else:
            req, hidden = copy.deepcopy(request), 0
        note = (pol.get("notes") or {}).get(tier)
        noted = False
        if isinstance(note, str) and note:
            noted = _append_note(req, "\n\n" + _msg(pol, "tier_note_prefix", tier=tier) + note)
        visible = _filter_tool_lists(req, tier, pol)
        if hidden or noted or visible is not None:
            reason = f"{who} ({tier}): {hidden} memory blocks withheld"
            if visible is not None:
                reason += f", {visible} tools visible"
            if noted:
                reason += ", note added"
            return _llm_result(req, reason)
        return None
    except Exception:
        try:
            return _llm_result(
                _refusal_request(dict(request), DEFAULT_MESSAGES["policy_error_tool"]),
                "error: failed closed",
            )
        except Exception:
            return _llm_result(
                {"messages": [{"role": "user", "content": DEFAULT_MESSAGES["policy_error_tool"]}]},
                "error: failed closed",
            )


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
    memory_tiers = pol["memory_tiers"]
    # No sender id is not a free pass: a shared session we cannot attribute
    # is refused for tiers that do not keep owner memory.
    if (
        tier not in memory_tiers
        and src.get("chat_type", "dm") != "dm"
        and shared
        and (not ids or not any(str(session_key).endswith(":" + item) for item in ids))
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
    # Same-timestamp replace still has to be visible on the next command.
    _runtime.policy_key = None
    _runtime.policy = None


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
    _runtime.ctx = ctx
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
    ctx.register_middleware("llm_request", _llm_request)
