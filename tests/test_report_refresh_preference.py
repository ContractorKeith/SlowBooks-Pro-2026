"""The "Refresh reports automatically" preference (#482).

report_auto_refresh is an ordinary per-user preference ({"enabled": bool})
through the preferences store as it is: unset reads as None (the page then
defaults to on), and a written value round-trips. The route only checks the
value is an object, so a bare boolean is refused.
"""

KEY = "/api/preferences/report_auto_refresh"


def test_unset_reads_as_none(client):
    r = client.get(KEY)
    assert r.status_code == 200
    assert r.json() == {"key": "report_auto_refresh", "value": None}


def test_enabled_false_round_trips(client):
    r = client.put(KEY, json={"value": {"enabled": False}})
    assert r.status_code == 200
    assert r.json()["value"] == {"enabled": False}
    assert client.get(KEY).json()["value"] == {"enabled": False}


def test_enabled_true_round_trips(client):
    assert client.put(KEY, json={"value": {"enabled": True}}).status_code == 200
    assert client.get(KEY).json()["value"] == {"enabled": True}


def test_a_bare_value_is_refused(client):
    # the store takes an object, not a scalar
    assert client.put(KEY, json={"value": True}).status_code == 422
