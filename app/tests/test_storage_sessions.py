import json
import os

from energyoptimizer.sessions import Sessions, describe_agent
from energyoptimizer.storage import load_json, save_json


def test_save_keeps_backup_and_load_falls_back(tmp_path):
    p = str(tmp_path / "x.json")
    assert save_json(p, {"v": 1})
    assert save_json(p, {"v": 2})
    assert json.load(open(p + ".bak")) == {"v": 1}
    assert load_json(p, None) == {"v": 2}
    # Power cut while writing: the main file is damaged …
    open(p, "w").write('{"v": ')
    assert load_json(p, None) == {"v": 1}
    # … or gone between the two renames.
    os.remove(p)
    assert load_json(p, None) == {"v": 1}
    os.remove(p + ".bak")
    assert load_json(p, "dflt") == "dflt"
    assert not os.path.exists(p + ".tmp")


def test_sessions_create_validate_revoke(tmp_path):
    s = Sessions(str(tmp_path))
    a = s.create("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/604.1")
    b = s.create("curl/8.0")
    assert s.valid(a) and s.valid(b) and not s.valid("nope")
    assert a not in json.dumps(s.s)   # only hashes are stored
    lst = s.listing(a)
    assert {x["ua"] for x in lst} == {"Safari · iPhone", "curl"}
    assert [x["current"] for x in lst if x["ua"] == "Safari · iPhone"] == [True]
    s2 = Sessions(str(tmp_path))
    s2.load()
    assert s2.valid(a)
    assert s2.revoke_all(keep=a) == 1
    assert s2.valid(a) and not s2.valid(b)
    sid = s2.listing()[0]["id"]
    assert s2.revoke_id(sid) and not s2.valid(a)


def test_describe_agent():
    assert describe_agent("Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537") == "Chrome · Windows"
    assert describe_agent("") == "Browser"
