"""The catalog draft loads in Hermes' catalog code and in the standalone validator."""

import json
from pathlib import Path

import hermes_yaml as yaml

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "catalog" / "household-tiers.yaml"


def _validator():
    import hermes_cli

    script = Path(hermes_cli.__file__).resolve().parents[1] / "scripts" / "validate_plugin_catalog.py"
    source = script.read_text(encoding="utf-8").split("\n", 1)[1]
    namespace = {"__file__": str(script), "__name__": "validate_plugin_catalog"}
    exec(compile(source, str(script), "exec"), namespace)
    return namespace["validate_entry"]


def test_catalog_and_plugin_descriptions_match():
    sentence = (
        "built-in owner memory (MEMORY.md/USER.md) withheld from non-owner prompts; "
        "an external provider block joined after those boxes is removed; "
        "a provider block with no built-in box, and compaction's auxiliary summary, are not filtered"
    )
    catalog = yaml.safe_load(ENTRY.read_text(encoding="utf-8"))
    plugin = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    pasted = (ROOT / "CATALOG_DESCRIPTION.txt").read_text(encoding="utf-8").strip()
    assert catalog["description"] == plugin["description"] == pasted
    assert sentence in pasted


def test_standalone_validator_accepts_the_entry():
    data = yaml.safe_load(ENTRY.read_text(encoding="utf-8"))
    errors, _warnings = _validator()(data)
    assert errors == []


def test_runtime_loader_accepts_the_entry():
    from hermes_cli.plugin_catalog import entry_from_mapping

    data = yaml.safe_load(ENTRY.read_text(encoding="utf-8"))
    entry = entry_from_mapping(data, "household-tiers.yaml")
    assert entry is not None
    assert entry.name == "household-tiers"
    assert entry.repo == "https://github.com/chevlume/household-tiers"
    assert entry.maintainer == "chevlume"
    assert entry.category == "platform"
    assert entry.requires_hermes == ">=0.22.7"
    assert entry.version == "0.1.1"
    assert len(entry.sha) == 40
    assert entry.capabilities.provides_middleware == ["llm_request"]
    assert "post_gateway_admission" in entry.capabilities.provides_hooks
    blob = json.dumps(entry.to_dict() if hasattr(entry, "to_dict") else data)
    assert "image" not in data
    assert "banner" not in blob.lower()
