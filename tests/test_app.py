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
    page = fresh.get(f"/e/{code}").get_data(as_text=True)
    assert "The names are drawn" in page
    assert 'name="last_name"' not in page  # only first name + PIN after the draw
    res = post(fresh, f"/e/{code}/login", {"first_name": " ana ", "secret": "4321"})
    assert res.status_code == 302
    assert "Tap the gift" in fresh.get(f"/e/{code}").get_data(as_text=True)


def test_organizer_logs_in_with_first_name_after_draw(app):
    org, code = create_event(app, first="Armando", last="Flores", password="Navidad2026")
    join(app, code, "Ana", "Smith")
    join(app, code, "Ben", "Smith")
    post(org, f"/e/{code}/draw", {})
    post(org, f"/e/{code}/logout", {})
    res = post(org, f"/e/{code}/login", {"first_name": "armando", "secret": "Navidad2026"})
    assert res.status_code == 302
    assert "Organizer tools" in org.get(f"/e/{code}").get_data(as_text=True)


def drawn_event_with_two_anas(app, pin_1, pin_2):
    org, code = create_event(app)
    join(app, code, "Ana", "Smith", pin=pin_1)
    join(app, code, "Ana", "Lopez", pin=pin_2)
    post(org, f"/e/{code}/draw", {})
    return code


def test_same_first_name_different_pins(app):
    code = drawn_event_with_two_anas(app, "1111", "2222")
    client = app.test_client()
    assert post(client, f"/e/{code}/login", {"first_name": "Ana", "secret": "2222"}).status_code == 302
    res = post(client, f"/e/{code}/reveal", {}, headers={"Accept": "application/json"})
    assert res.status_code == 200 and res.get_json()["name"] != "Ana Lopez"


def test_same_first_name_same_pin_asks_for_last_name(app):
    code = drawn_event_with_two_anas(app, "1111", "1111")
    client = app.test_client()
    res = post(client, f"/e/{code}/login", {"first_name": "Ana", "secret": "1111"})
    assert res.status_code == 400
    page = res.get_data(as_text=True)
    assert "more than one Ana" in page and 'name="last_name"' in page
    res = post(client, f"/e/{code}/login", {"first_name": "Ana", "last_name": "Lopez", "secret": "1111"})
    assert res.status_code == 302


def test_first_name_only_wrong_pin_locks_out(app):
    code = drawn_event_with_two_anas(app, "1111", "2222")
    client = app.test_client()
    for _ in range(5):
        res = post(client, f"/e/{code}/login", {"first_name": "Ana", "secret": "0000"})
        assert res.status_code == 400
    res = post(client, f"/e/{code}/login", {"first_name": "Ana", "secret": "1111"})
    assert "Too many wrong tries" in res.get_data(as_text=True)


@pytest.mark.parametrize("password", ["Navidad2026", "with spaces in it", " leading1", "trailing1 "])
def test_organizer_can_log_back_in(app, password):
    org, code = create_event(app, first="Armando", last="Flores", password=password)
    post(org, f"/e/{code}/logout", {})
    assert "Organizer tools" not in org.get(f"/e/{code}").get_data(as_text=True)
    res = post(org, f"/e/{code}/login", {"first_name": "Armando", "last_name": "Flores", "secret": password})
    assert res.status_code == 302
    assert "Organizer tools" in org.get(f"/e/{code}").get_data(as_text=True)


def test_reset_password_command(app):
    org, code = create_event(app, first="Armando", last="Flores", password="forgotten1")
    client = app.test_client()
    for _ in range(5):  # lock the account
        post(client, f"/e/{code}/login", {"first_name": "Armando", "last_name": "Flores", "secret": "wrong!"})

    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["reset-password", f"--event={code}", "--first=armando", "--last=FLORES"],
        input="NewPass123\nNewPass123\n",
    )
    assert result.exit_code == 0, result.output
    res = post(client, f"/e/{code}/login", {"first_name": "Armando", "last_name": "Flores", "secret": "NewPass123"})
    assert res.status_code == 302

    bad = runner.invoke(args=["reset-password", f"--event={code}", "--first=No", "--last=One"], input="x12345\nx12345\n")
    assert bad.exit_code != 0 and "No one with that name" in bad.output


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
    assert "Log in with your first name and PIN" in late.get(f"/e/{code}").get_data(as_text=True)


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
