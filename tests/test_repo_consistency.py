"""Offline consistency checks of this repository (no Infrahub, no Docker).

They catch the mistakes a render would only show later: a definition in .infrahub.yml that points to a missing file or
query, an object or schema file that is not valid YAML, a baseline that carries a real secret instead of the placeholder.
"""
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _infrahub_yml():
    return yaml.safe_load((ROOT / ".infrahub.yml").read_text())


def test_infrahub_yml_files_exist():
    cfg = _infrahub_yml()
    missing = []
    for q in cfg.get("queries", []):
        if not (ROOT / q["file_path"]).is_file():
            missing.append(q["file_path"])
    for t in cfg.get("jinja2_transforms", []):
        if not (ROOT / t["template_path"]).is_file():
            missing.append(t["template_path"])
    for key in ("python_transforms", "generator_definitions", "check_definitions"):
        for d in cfg.get(key, []):
            if not (ROOT / d["file_path"]).is_file():
                missing.append(d["file_path"])
    for s in cfg.get("schemas", []):
        if not (ROOT / s).exists():
            missing.append(s)
    assert not missing, f"files named in .infrahub.yml that do not exist: {missing}"


def test_infrahub_yml_references_resolve():
    cfg = _infrahub_yml()
    queries = {q["name"] for q in cfg.get("queries", [])}
    transforms = {t["name"] for t in cfg.get("jinja2_transforms", [])} | {t["name"] for t in cfg.get("python_transforms", [])}
    bad = []
    for t in cfg.get("jinja2_transforms", []):
        if t["query"] not in queries:
            bad.append(f"jinja2 transform {t['name']} -> query {t['query']}")
    for g in cfg.get("generator_definitions", []):
        if g["query"] not in queries:
            bad.append(f"generator {g['name']} -> query {g['query']}")
    for a in cfg.get("artifact_definitions", []):
        if a["transformation"] not in transforms:
            bad.append(f"artifact definition {a['name']} -> transformation {a['transformation']}")
    assert not bad, f"references in .infrahub.yml that do not resolve: {bad}"


def test_infrahub_yml_names_unique():
    cfg = _infrahub_yml()
    for key in ("queries", "jinja2_transforms", "python_transforms", "artifact_definitions", "generator_definitions", "check_definitions"):
        names = [x["name"] for x in cfg.get(key, [])]
        assert len(names) == len(set(names)), f"duplicate names in {key}: {sorted(n for n in names if names.count(n) > 1)}"


def test_objects_and_schemas_are_yaml():
    files = sorted((ROOT / "objects").glob("*.yml")) + sorted((ROOT / "schemas").rglob("*.yml"))
    assert files, "no object or schema files found"
    for f in files:
        docs = list(yaml.safe_load_all(f.read_text()))
        assert docs, f"{f.relative_to(ROOT)} holds no YAML document"


def test_objects_load_in_numbered_order():
    names = sorted(p.name for p in (ROOT / "objects").glob("*.yml"))
    assert all(re.match(r"^\d\d-", n) for n in names), f"object files must start with a two-digit load order: {names}"


def test_baselines_exist_and_are_named_by_device():
    cfgs = sorted((ROOT / "tests" / "expected").glob("*.cfg"))
    assert cfgs, "tests/expected/ holds no baseline"
    pattern = re.compile(r"^DC[12]-[A-Z0-9-]+\.cfg$")
    bad = [c.name for c in cfgs if not pattern.match(c.name)]
    assert not bad, f"baselines not named <site>-<device>.cfg: {bad}"
    lab = sorted((ROOT / "tests" / "expected-lab").iterdir())
    assert lab, "tests/expected-lab/ holds no lab baseline"


def test_no_secret_in_baselines():
    """The ciscoadmin line carries a placeholder in every render; a real hash or password must never be committed."""
    leaks = []
    for c in sorted((ROOT / "tests" / "expected").glob("*.cfg")):
        text = c.read_text()
        if "username ciscoadmin" in text and "__CISCOADMIN_HASH__" not in text:
            leaks.append(c.name)
        if re.search(r"password 5 \$5\$|secret sha512 \$6\$", text):
            leaks.append(c.name)
    assert not leaks, f"baselines with a real secret: {leaks}"


def test_templates_referenced_once():
    cfg = _infrahub_yml()
    used = {t["template_path"] for t in cfg.get("jinja2_transforms", [])}
    present = {str(p.relative_to(ROOT)) for p in (ROOT / "templates").glob("*.j2")}
    assert present <= used, f"templates not used by any transform: {sorted(present - used)}"
