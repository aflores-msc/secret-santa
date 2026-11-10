import re

import pytest

from app import create_app, make_pairs


@pytest.fixture
def app(tmp_path):
    return create_app({"DATABASE": str(tmp_path / "test.db"), "SECRET_KEY": "test", "TESTING": True})


def csrf(client):
    client.get("/new")  # any page with a form creates the token
    with client.session_transaction() as s:
        return s["csrf"]


def post(client, url, data, **kw):
    return client.post(url, data={**data, "csrf": csrf(client)}, **kw)


def create_event(app, first="Olga", last="Organizer", password="secret123"):
    client = app.test_client()
    res = post(client, "/new", {
        "event_name": "Family Xmas", "budget": "$25", "exchange_date": "2026-12-24",
        "first_name": first, "last_name": last,
        "password": password, "password_confirm": password,
    })
    assert res.status_code == 302
    code = re.search(r"/e/([\w-]+)$", res.headers["Location"]).group(1)
    return client, code


def join(app, code, first, last, pin="1234", wishlist=""):
    client = app.test_client()
    res = post(client, f"/e/{code}/join", {
        "first_name": first, "last_name": last, "pin": pin, "pin_confirm": pin, "wishlist": wishlist,
    })
    return client, res


def test_make_pairs_is_a_valid_derangement():
    for n in range(2, 30):
        ids = list(range(100, 100 + n))
        pairs = make_pairs(ids)
        assert sorted(pairs) == ids
        assert sorted(pairs.values()) == ids
        assert all(giver != receiver for giver, receiver in pairs.items())


def test_full_flow(app):
    org, code = create_event(app)
    people = {"Olga Organizer": org}
    for first, last in [("Ana", "Pérez"), ("Ben", "Ng"), ("Cleo", "Diaz")]:
        client, res = join(app, code, first, last, wishlist=f"{first} likes socks")
        assert res.status_code == 302
        people[f"{first} {last}"] = client

    page = org.get(f"/e/{code}").get_data(as_text=True)
    assert "Organizer tools" in page and "Ana Pérez" in page

    assert post(org, f"/e/{code}/draw", {}).status_code == 302

    receivers = {}
    for name, client in people.items():
        res = post(client, f"/e/{code}/reveal", {}, headers={"Accept": "application/json"})
        assert res.status_code == 200
        receivers[name] = res.get_json()["name"]

    assert sorted(receivers.values()) == sorted(people)
    assert all(giver != receiver for giver, receiver in receivers.items())

    # Organizer sees everyone has opened, but no matches.
    page = org.get(f"/e/{code}").get_data(as_text=True)
    assert "4 of 4 people have opened" in page


def test_login_after_draw_with_pin(app):
    org, code = create_event(app)
    for first in ["Ana", "Ben"]:
        join(app, code, first, "Smith", pin="4321")
    post(org, f"/e/{code}/draw", {})

    fresh = app.test_client()
    assert "The names are drawn" in fresh.get(f"/e/{code}").get_data(as_text=True)
    res = post(fresh, f"/e/{code}/login", {"first_name": " ana ", "last_name": "SMITH", "secret": "4321"})
    assert res.status_code == 302
    assert "Tap the gift" in fresh.get(f"/e/{code}").get_data(as_text=True)


def test_draw_needs_three_people(app):
    org, code = create_event(app)
    join(app, code, "Ana", "Smith")
    res = post(org, f"/e/{code}/draw", {})
    assert res.status_code == 400
    assert "at least 3" in res.get_data(as_text=True)


def test_only_organizer_can_draw_or_remove(app):
    _, code = create_event(app)
    ana, _ = join(app, code, "Ana", "Smith")
    join(app, code, "Ben", "Smith")
    assert post(ana, f"/e/{code}/draw", {}).status_code == 403
    assert post(ana, f"/e/{code}/remove/1", {}).status_code == 403
    assert post(app.test_client(), f"/e/{code}/draw", {}).status_code == 403


def test_duplicate_name_rejected(app):
    _, code = create_event(app)
    join(app, code, "Ana", "Smith")
    _, res = join(app, code, "ANA", "smith")
    assert res.status_code == 400
    assert "already joined" in res.get_data(as_text=True)


def test_cannot_join_after_draw(app):
    org, code = create_event(app)
    join(app, code, "Ana", "Smith")
    join(app, code, "Ben", "Smith")
    post(org, f"/e/{code}/draw", {})
    late, res = join(app, code, "Late", "Larry")
    assert res.status_code == 302
    assert "Organizer tools" not in late.get(f"/e/{code}").get_data(as_text=True)
    assert "Log in with your name and PIN" in late.get(f"/e/{code}").get_data(as_text=True)


def test_lockout_after_wrong_pins(app):
    _, code = create_event(app)
    join(app, code, "Ana", "Smith", pin="1111")
    client = app.test_client()
    for _ in range(5):
        post(client, f"/e/{code}/login", {"first_name": "Ana", "last_name": "Smith", "secret": "0000"})
    res = post(client, f"/e/{code}/login", {"first_name": "Ana", "last_name": "Smith", "secret": "1111"})
    assert res.status_code == 400
    assert "Too many wrong tries" in res.get_data(as_text=True)


def test_post_without_csrf_is_rejected(app):
    _, code = create_event(app)
    res = app.test_client().post(f"/e/{code}/join", data={"first_name": "A", "last_name": "B"})
    assert res.status_code == 400


def test_unknown_event_is_404(app):
    assert app.test_client().get("/e/nope").status_code == 404
