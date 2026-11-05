import json
_MAP = {}
def pytest_collection_modifyitems(items):
    for item in items:
        ids = [a for m in item.iter_markers("rule") for a in m.args]
        if ids:
            _MAP[item.nodeid] = sorted(set(ids))
def pytest_collection_finish(session):
    print("RULEMAP " + json.dumps(_MAP))
