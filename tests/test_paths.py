"""File tools: the default user tier has none. Custom grants stay inside file_roots."""

import json
import os
from pathlib import Path


def _user(home, **extra):
    body = {"preset": "family", "users": {"telegram:333": "user", "telegram:111": "owner"}}
    body.update(extra)
    (home / "household-tiers.json").write_text(json.dumps(body), encoding="utf-8")


def _as_user(monkeypatch, uid="333"):
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", uid)


def test_default_user_has_no_file_tools(ht, home, monkeypatch):
    _user(home)
    _as_user(monkeypatch)
    assert "read_file" not in ht.DEFAULT_TIERS["user"]
    assert "search_files" not in ht.DEFAULT_TIERS["user"]
    for tool, args in (
        ("read_file", {"path": "/tmp/notes.txt"}),
        ("search_files", {"path": "/tmp"}),
        ("read_file", {"path": str(home / "household-tiers.json")}),
    ):
        blocked = ht._pre_tool_call(tool_name=tool, args=args, session_id="u")
        assert blocked["action"] == "block", tool
        assert "may not use" in blocked["message"]
    assert ht._pre_tool_call(tool_name="web_search", args={"query": "weather"}, session_id="u") is None


def test_empty_file_roots_allow_no_path(ht, home, monkeypatch):
    _user(home, tiers={"user": ["read_file", "search_files", "web_search"]})
    _as_user(monkeypatch)
    outside = ht._pre_tool_call(tool_name="read_file", args={"path": "/tmp/notes.txt"}, session_id="u")
    assert outside["action"] == "block"
    assert "may not read that path" in outside["message"]
    searched = ht._pre_tool_call(tool_name="search_files", args={"query": "secret"}, session_id="u")
    assert searched["action"] == "block"
    assert ht._pre_tool_call(tool_name="web_search", args={"query": "weather"}, session_id="u") is None


def test_file_roots_allow_only_that_tree(ht, home, tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "ok.txt").write_text("ok", encoding="utf-8")
    _user(
        home,
        tiers={"user": ["read_file", "search_files"]},
        file_roots=[str(shared)],
    )
    _as_user(monkeypatch)
    assert ht._pre_tool_call(tool_name="read_file", args={"path": str(shared / "ok.txt")}, session_id="u") is None
    for path in (home / "household-tiers.json", home, home.parent, Path("/tmp/notes.txt")):
        blocked = ht._pre_tool_call(tool_name="read_file", args={"path": str(path)}, session_id="u")
        assert blocked["action"] == "block", path
    parent = ht._pre_tool_call(tool_name="search_files", args={"path": str(home.parent)}, session_id="u")
    assert parent["action"] == "block"


def test_relative_paths_use_ctx_session_cwd_not_the_process_cwd(ht, tmp_path, monkeypatch):
    session = tmp_path / "session"
    hermes = session / ".hermes"
    hermes.mkdir(parents=True)
    (session / "projects").mkdir()
    (session / "projects" / "readme.md").write_text("ok", encoding="utf-8")
    other = tmp_path / "other"
    (other / "projects").mkdir(parents=True)
    (other / "projects" / "readme.md").write_text("other", encoding="utf-8")
    (hermes / "household-tiers.json").write_text(
        json.dumps(
            {
                "preset": "family",
                "users": {"telegram:333": "user"},
                "tiers": {"user": ["read_file", "search_files"]},
                "file_roots": [str(session)],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    monkeypatch.setenv("TERMINAL_CWD", str(other))
    _as_user(monkeypatch)
    monkeypatch.chdir(other)

    class Ctx:
        def session_cwd(self):
            return str(session)

        def register_command(self, *args, **kwargs):
            pass

        def register_hook(self, *args, **kwargs):
            pass

        def register_middleware(self, *args, **kwargs):
            pass

    import tools.file_tools_paths as paths

    def _private(*args, **kwargs):
        raise AssertionError("private _resolve_base_dir must not be used")

    monkeypatch.setattr(paths, "_resolve_base_dir", _private)
    ht.register(Ctx())
    blocked = ht._pre_tool_call(tool_name="read_file", args={"path": ".hermes/memories/MEMORY.md"}, session_id="u")
    assert blocked["action"] == "block"
    omitted = ht._pre_tool_call(tool_name="search_files", args={"query": "secret"}, session_id="u")
    assert omitted["action"] == "block"
    dotted = ht._pre_tool_call(tool_name="search_files", args={"path": ".", "query": "secret"}, session_id="u")
    assert dotted["action"] == "block"
    allowed = ht._pre_tool_call(tool_name="read_file", args={"path": "projects/readme.md"}, session_id="u")
    assert allowed is None


def test_relative_path_without_a_session_cwd_is_refused(ht, home, tmp_path, monkeypatch):
    session = tmp_path / "ws"
    session.mkdir()
    (session / "note.txt").write_text("ok", encoding="utf-8")
    _user(home, tiers={"user": ["read_file"]}, file_roots=[str(session)])
    _as_user(monkeypatch)
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    refused = ht._pre_tool_call(tool_name="read_file", args={"path": "note.txt"}, session_id="u")
    assert refused["action"] == "block"
    assert ht._pre_tool_call(tool_name="read_file", args={"path": str(session / "note.txt")}, session_id="u") is None


def test_path_compare_uses_normcase(ht, home, tmp_path, monkeypatch):
    notes = tmp_path / "Notes"
    notes.mkdir()
    (notes / "a.txt").write_text("ok", encoding="utf-8")
    _user(home, tiers={"user": ["read_file"]}, file_roots=[str(notes).swapcase()])
    _as_user(monkeypatch)
    monkeypatch.setattr(os.path, "normcase", lambda value: value.lower() if isinstance(value, str) else value)
    assert ht._pre_tool_call(tool_name="read_file", args={"path": str(notes / "a.txt")}, session_id="u") is None
    folded = str(home).swapcase()
    blocked = ht._pre_tool_call(
        tool_name="read_file",
        args={"path": folded + "/memories/MEMORY.md"},
        session_id="u",
    )
    assert blocked["action"] == "block"


def test_symlink_with_missing_component_cannot_escape_into_hermes_home(ht, tmp_path, monkeypatch):
    """Hermes realpath follows the link; a missing segment must not skip that."""
    session = tmp_path / "proj"
    session.mkdir()
    hermes = tmp_path / "dot-hermes"
    (hermes / "memories").mkdir(parents=True)
    secret = "SYMLINK_MEMORY_SECRET"
    (hermes / "memories" / "MEMORY.md").write_text(secret, encoding="utf-8")
    (hermes / "household-tiers.json").write_text(
        json.dumps(
            {
                "preset": "family",
                "users": {"telegram:333": "user"},
                "tiers": {"user": ["read_file"]},
                "file_roots": [str(tmp_path)],
            }
        ),
        encoding="utf-8",
    )
    (session / "h").symlink_to(hermes)
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    monkeypatch.setenv("TERMINAL_CWD", str(session))
    _as_user(monkeypatch)
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)

    class Ctx:
        def session_cwd(self):
            return str(session)

        def register_command(self, *args, **kwargs):
            pass

        def register_hook(self, *args, **kwargs):
            pass

        def register_middleware(self, *args, **kwargs):
            pass

    ht.register(Ctx())
    from tools.file_tools import read_file_tool

    opened = read_file_tool("h/x/../memories/MEMORY.md", task_id="symlink")
    assert secret in opened
    blocked = ht._pre_tool_call(
        tool_name="read_file",
        args={"path": "h/x/../memories/MEMORY.md"},
        session_id="symlink",
        task_id="symlink",
    )
    assert blocked["action"] == "block"


def test_credential_and_browser_dirs_are_blocked_inside_a_root(ht, home, tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "ok.txt").write_text("ok", encoding="utf-8")
    _user(home, tiers={"user": ["read_file", "search_files"]}, file_roots=[str(tmp_path)])
    _as_user(monkeypatch)
    assert ht._pre_tool_call(tool_name="read_file", args={"path": str(shared / "ok.txt")}, session_id="u") is None
    relatives = (
        ".ssh/id_rsa",
        ".aws/credentials",
        ".gnupg/pubring.kbx",
        ".kube/config",
        ".docker/config.json",
        ".azure/accessTokens.json",
        ".config/gh/hosts.yml",
        ".config/gcloud/credentials.db",
        ".mozilla/firefox/profile/cookies.sqlite",
        ".config/google-chrome/Default/Cookies",
        ".config/chromium/Default/Cookies",
        ".config/microsoft-edge/Default/Cookies",
        ".config/BraveSoftware/Brave-Browser/Default/Cookies",
        ".config/opera/Cookies",
        ".config/vivaldi/Default/Cookies",
        "Library/Application Support/Google/Chrome/Default/Cookies",
        "Library/Safari/History.db",
        "AppData/Local/Google/Chrome/User Data/Default/Cookies",
        "AppData/Roaming/Mozilla/Firefox/profiles.ini",
    )
    for rel in relatives:
        blocked = ht._pre_tool_call(tool_name="read_file", args={"path": str(tmp_path / rel)}, session_id="u")
        assert blocked["action"] == "block", rel
    searched = ht._pre_tool_call(tool_name="search_files", args={"path": str(tmp_path)}, session_id="u")
    assert searched["action"] == "block"
    link = shared / "key"
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_rsa").write_text("SECRETKEY", encoding="utf-8")
    link.symlink_to(tmp_path / ".ssh" / "id_rsa")
    linked = ht._pre_tool_call(tool_name="read_file", args={"path": str(link)}, session_id="u")
    assert linked["action"] == "block"


def test_account_home_credentials_are_blocked_when_home_differs(ht, home, tmp_path, monkeypatch):
    import pwd

    account = Path(pwd.getpwuid(os.getuid()).pw_dir)
    if account.resolve() == tmp_path.resolve():
        return
    _user(home, tiers={"user": ["read_file"]}, file_roots=[str(account)])
    _as_user(monkeypatch)
    blocked = ht._pre_tool_call(
        tool_name="read_file",
        args={"path": str(account / ".ssh" / "id_rsa")},
        session_id="u",
    )
    assert blocked["action"] == "block"


def test_non_owner_star_is_still_limited_to_file_roots(ht, home, tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "ok.txt").write_text("ok", encoding="utf-8")
    _user(home, tiers={"user": ["*"]}, file_roots=[str(shared)])
    _as_user(monkeypatch)
    assert ht._pre_tool_call(tool_name="read_file", args={"path": str(shared / "ok.txt")}, session_id="u") is None
    blocked = ht._pre_tool_call(tool_name="read_file", args={"path": "/tmp/notes.txt"}, session_id="u")
    assert blocked["action"] == "block"
    assert ht._pre_tool_call(tool_name="terminal", args={"command": "true"}, session_id="u") is None


def test_owner_is_not_limited_by_file_roots(ht, home, tmp_path, monkeypatch):
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_rsa").write_text("SECRETKEY", encoding="utf-8")
    _user(home, file_roots=[])
    _as_user(monkeypatch, uid="111")
    assert ht._pre_tool_call(tool_name="read_file", args={"path": str(tmp_path / ".ssh" / "id_rsa")}, session_id="o") is None
    assert ht._pre_tool_call(tool_name="read_file", args={"path": str(home / "household-tiers.json")}, session_id="o") is None


def test_home_follows_the_active_profile_not_a_hand_built_path(ht, home, tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    profile = tmp_path / "profiles" / "house"
    profile.mkdir(parents=True)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "ok.txt").write_text("ok", encoding="utf-8")
    (profile / "household-tiers.json").write_text(
        json.dumps(
            {
                "users": {"telegram:333": "user"},
                "tiers": {"user": ["read_file"]},
                "file_roots": [str(tmp_path)],
            }
        ),
        encoding="utf-8",
    )
    (home / "household-tiers.json").write_text(json.dumps({"default": "guest"}), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", f"  {home}  ")
    assert ht._home().resolve() == Path(home).resolve()
    assert ht._policy()["default"] == "guest"
    _as_user(monkeypatch)
    token = set_hermes_home_override(profile)
    try:
        assert ht._home().resolve() == profile.resolve()
        assert ht._policy()["users"]["telegram:333"] == "user"
        blocked = ht._pre_tool_call(tool_name="read_file", args={"path": str(profile / "memories" / "MEMORY.md")}, session_id="u")
        assert blocked["action"] == "block"
        # The env home is not the active profile, so it is not the read block.
        assert ht._pre_tool_call(tool_name="read_file", args={"path": str(home / "household-tiers.json")}, session_id="u") is None
        assert ht._pre_tool_call(tool_name="read_file", args={"path": str(shared / "ok.txt")}, session_id="u") is None
        ht._pre_tool_call(tool_name="terminal", args={}, session_id="u")
        assert (profile / "household-tiers-audit.jsonl").is_file()
        assert not (home / "household-tiers-audit.jsonl").exists()
    finally:
        reset_hermes_home_override(token)


def test_relative_file_root_fails_closed(ht, home, monkeypatch):
    _user(home, file_roots=["notes"])
    _as_user(monkeypatch)
    blocked = ht._pre_tool_call(tool_name="web_search", args={}, session_id="bad")
    assert blocked["action"] == "block"
    assert "policy error" in blocked["message"]


def test_memory_tiers_must_be_a_list(ht, home, monkeypatch):
    (home / "household-tiers.json").write_text(
        json.dumps({"users": {"telegram:333": "user"}, "memory_tiers": "owner"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "111")
    blocked = ht._pre_tool_call(tool_name="web_search", args={}, session_id="bad")
    assert blocked["action"] == "block"
    assert "policy error" in blocked["message"]


def test_family_hours_default_to_utc_and_timezone_is_configurable(ht, home):
    (home / "household-tiers.json").write_text(json.dumps({"preset": "family"}), encoding="utf-8")
    assert ht._policy()["hours"]["kid"]["tz"] == "UTC"
    assert ht._policy()["timezone"] == "UTC"
    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "timezone": "Europe/Madrid"}),
        encoding="utf-8",
    )
    assert ht._policy()["hours"]["kid"]["tz"] == "Europe/Madrid"
    assert ht._policy()["hours"]["guest"]["tz"] == "Europe/Madrid"
