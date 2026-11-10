"""Secret Santa: a small Flask app for gift exchanges where people join at different times.

Flow:
  1. The organizer creates an event (event name + their name + password) and gets a share link.
  2. Participants open the share link and join with their name and a PIN.
  3. The organizer draws names. Everyone is paired at once, so nobody can end up with themselves.
  4. Everyone returns to the same link, logs in with name + PIN (or password) and opens their gift.
"""

import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone

from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

MIN_PARTICIPANTS = 3
MAX_PARTICIPANTS = 100
MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)
PIN_RE = re.compile(r"^\d{4,6}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    budget        TEXT NOT NULL DEFAULT '',
    exchange_date TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'open',   -- 'open' or 'drawn'
    created_at    TEXT NOT NULL,
    drawn_at      TEXT
);
CREATE TABLE IF NOT EXISTS participants (
    id            INTEGER PRIMARY KEY,
    event_id      INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    name_key      TEXT NOT NULL,                  -- normalized name, for matching
    secret_hash   TEXT NOT NULL,                  -- hashed PIN, or organizer password
    is_organizer  INTEGER NOT NULL DEFAULT 0,
    wishlist      TEXT NOT NULL DEFAULT '',
    receiver_id   INTEGER REFERENCES participants(id),
    revealed_at   TEXT,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until  TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (event_id, name_key)
);
"""

bp = Blueprint("santa", __name__)


# --------------------------------------------------------------------------- helpers


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def clean(text):
    """Trim and collapse whitespace."""
    return " ".join((text or "").split())


def make_name_key(first, last):
    return f"{first} {last}".casefold()


def make_pairs(ids):
    """Return {giver_id: receiver_id}.

    Shuffles everyone into one big circle and each person gives to the next one.
    Nobody gets themselves and everybody gives and receives exactly once.
    """
    order = list(ids)
    secrets.SystemRandom().shuffle(order)
    return {order[i]: order[(i + 1) % len(order)] for i in range(len(order))}


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(current_app.config["DATABASE"])
    db.executescript(SCHEMA)
    db.close()


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def validate_person(first, last):
    errors = []
    if not first or not last:
        errors.append("Please enter both first and last name.")
    elif len(first) > 40 or len(last) > 40:
        errors.append("Names can be at most 40 characters each.")
    return errors


def get_event(code):
    row = get_db().execute("SELECT * FROM events WHERE code = ?", (code,)).fetchone()
    if row is None:
        abort(404)
    return dict(row)


def get_participants(event_id):
    rows = get_db().execute(
        "SELECT * FROM participants WHERE event_id = ? ORDER BY is_organizer DESC, created_at, id",
        (event_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_participant(event_id, pid):
    row = get_db().execute(
        "SELECT * FROM participants WHERE event_id = ? AND id = ?", (event_id, pid)
    ).fetchone()
    return dict(row) if row else None


def current_participant(event):
    pid = session.get("events", {}).get(event["code"])
    return get_participant(event["id"], pid) if pid else None


def log_in(code, pid):
    events = dict(session.get("events", {}))
    events[code] = pid
    session["events"] = events
    session.permanent = True


def add_participant(db, event_id, first, last, secret, wishlist="", is_organizer=False):
    cur = db.execute(
        """INSERT INTO participants
           (event_id, first_name, last_name, name_key, secret_hash, is_organizer, wishlist, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            first,
            last,
            make_name_key(first, last),
            generate_password_hash(secret),
            int(is_organizer),
            wishlist,
            iso(utcnow()),
        ),
    )
    return cur.lastrowid


def render_event(event, me=None, status=200, **extra):
    participants = get_participants(event["id"])
    organizer = next((p for p in participants if p["is_organizer"]), None)
    receiver = None
    if me and event["status"] == "drawn" and me["revealed_at"]:
        receiver = get_participant(event["id"], me["receiver_id"])
    extra.setdefault("form", {})
    extra.setdefault("view", request.args.get("view"))
    return (
        render_template(
            "event.html",
            event=event,
            me=me,
            organizer=organizer,
            participants=participants,
            receiver=receiver,
            share_url=url_for("santa.event", code=event["code"], _external=True),
            min_participants=MIN_PARTICIPANTS,
            **extra,
        ),
        status,
    )


def require_me(event):
    me = current_participant(event)
    if me is None:
        abort(403)
    return me


def require_organizer(event):
    me = require_me(event)
    if not me["is_organizer"]:
        abort(403)
    return me


# --------------------------------------------------------------------------- routes


@bp.before_app_request
def csrf_protect():
    if request.method == "POST":
        expected = session.get("csrf")
        sent = request.form.get("csrf", "")
        if not expected or not secrets.compare_digest(expected, sent):
            abort(400)


@bp.get("/")
def home():
    return render_template("home.html")


@bp.route("/new", methods=["GET", "POST"])
def new_event():
    errors = []
    form = request.form
    if request.method == "POST":
        name = clean(form.get("event_name"))
        budget = clean(form.get("budget"))
        exchange_date = (form.get("exchange_date") or "").strip()
        first = clean(form.get("first_name"))
        last = clean(form.get("last_name"))
        password = form.get("password", "")
        confirm = form.get("password_confirm", "")

        if not name:
            errors.append("Give your Secret Santa a name.")
        elif len(name) > 80:
            errors.append("The event name can be at most 80 characters.")
        if len(budget) > 40:
            errors.append("Keep the budget short (at most 40 characters).")
        if exchange_date:
            try:
                date.fromisoformat(exchange_date)
            except ValueError:
                errors.append("That exchange date doesn't look right.")
        errors += validate_person(first, last)
        if len(password) < 6:
            errors.append("Your password needs at least 6 characters.")
        elif password != confirm:
            errors.append("The two passwords don't match.")

        if not errors:
            db = get_db()
            code = secrets.token_urlsafe(6)
            while db.execute("SELECT 1 FROM events WHERE code = ?", (code,)).fetchone():
                code = secrets.token_urlsafe(6)
            cur = db.execute(
                "INSERT INTO events (code, name, budget, exchange_date, created_at) VALUES (?, ?, ?, ?, ?)",
                (code, name, budget, exchange_date, iso(utcnow())),
            )
            pid = add_participant(db, cur.lastrowid, first, last, password, is_organizer=True)
            db.commit()
            log_in(code, pid)
            return redirect(url_for("santa.event", code=code))

    return render_template("new.html", errors=errors, form=form), (400 if errors else 200)


@bp.get("/e/<code>")
def event(code):
    ev = get_event(code)
    return render_event(ev, current_participant(ev))


@bp.post("/e/<code>/join")
def join(code):
    ev = get_event(code)
    form = request.form
    first = clean(form.get("first_name"))
    last = clean(form.get("last_name"))
    pin = form.get("pin", "").strip()
    pin_confirm = form.get("pin_confirm", "").strip()
    wishlist = (form.get("wishlist") or "").strip()

    errors = validate_person(first, last)
    if not PIN_RE.match(pin):
        errors.append("Your PIN must be 4 to 6 digits.")
    elif pin != pin_confirm:
        errors.append("The two PINs don't match.")
    if len(wishlist) > 500:
        errors.append("Keep your wishlist under 500 characters.")

    if not errors:
        db = get_db()
        db.execute("BEGIN IMMEDIATE")  # so a join can't sneak in while names are being drawn
        status = db.execute("SELECT status FROM events WHERE id = ?", (ev["id"],)).fetchone()[0]
        count = db.execute(
            "SELECT COUNT(*) FROM participants WHERE event_id = ?", (ev["id"],)
        ).fetchone()[0]
        if status != "open":
            db.rollback()
            return redirect(url_for("santa.event", code=code))
        if count >= MAX_PARTICIPANTS:
            errors.append(f"This Secret Santa is full ({MAX_PARTICIPANTS} people).")
        else:
            try:
                pid = add_participant(db, ev["id"], first, last, pin, wishlist)
                db.commit()
                log_in(code, pid)
                return redirect(url_for("santa.event", code=code))
            except sqlite3.IntegrityError:
                errors.append(
                    f"Someone named {first} {last} has already joined. If that's you, log in instead."
                )
        db.rollback()

    return render_event(ev, None, status=400, join_errors=errors, form=form, view="join")


@bp.post("/e/<code>/login")
def login(code):
    ev = get_event(code)
    form = request.form
    first = clean(form.get("first_name"))
    last = clean(form.get("last_name"))
    secret = form.get("secret", "").strip()

    db = get_db()
    row = db.execute(
        "SELECT * FROM participants WHERE event_id = ? AND name_key = ?",
        (ev["id"], make_name_key(first, last)),
    ).fetchone()
    error = "That name and PIN don't match. Check the spelling of your name and try again."

    if row:
        locked_until = row["locked_until"]
        if locked_until and datetime.fromisoformat(locked_until) > utcnow():
            error = "Too many wrong tries. Please wait 15 minutes and try again."
        elif check_password_hash(row["secret_hash"], secret):
            db.execute(
                "UPDATE participants SET failed_logins = 0, locked_until = NULL WHERE id = ?",
                (row["id"],),
            )
            db.commit()
            log_in(code, row["id"])
            return redirect(url_for("santa.event", code=code))
        else:
            failed = row["failed_logins"] + 1
            if failed >= MAX_FAILED_LOGINS:
                db.execute(
                    "UPDATE participants SET failed_logins = 0, locked_until = ? WHERE id = ?",
                    (iso(utcnow() + LOCKOUT), row["id"]),
                )
                error = "Too many wrong tries. Please wait 15 minutes and try again."
            else:
                db.execute(
                    "UPDATE participants SET failed_logins = ? WHERE id = ?", (failed, row["id"])
                )
            db.commit()

    return render_event(ev, None, status=400, login_errors=[error], form=form, view="login")


@bp.post("/e/<code>/logout")
def logout(code):
    events = dict(session.get("events", {}))
    events.pop(code, None)
    session["events"] = events
    return redirect(url_for("santa.event", code=code))


@bp.post("/e/<code>/wishlist")
def wishlist(code):
    ev = get_event(code)
    me = require_me(ev)
    text = (request.form.get("wishlist") or "").strip()
    if len(text) > 500:
        return render_event(ev, me, status=400, wishlist_error="Keep your wishlist under 500 characters.")
    db = get_db()
    db.execute("UPDATE participants SET wishlist = ? WHERE id = ?", (text, me["id"]))
    db.commit()
    return redirect(url_for("santa.event", code=code, saved=1) + "#wishlist")


@bp.post("/e/<code>/remove/<int:pid>")
def remove(code, pid):
    ev = get_event(code)
    require_organizer(ev)
    if ev["status"] == "open":
        db = get_db()
        db.execute(
            "DELETE FROM participants WHERE id = ? AND event_id = ? AND is_organizer = 0",
            (pid, ev["id"]),
        )
        db.commit()
    return redirect(url_for("santa.event", code=code))


@bp.post("/e/<code>/draw")
def draw(code):
    ev = get_event(code)
    me = require_organizer(ev)
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    status = db.execute("SELECT status FROM events WHERE id = ?", (ev["id"],)).fetchone()[0]
    if status != "open":
        db.rollback()
        return redirect(url_for("santa.event", code=code))
    ids = [r[0] for r in db.execute("SELECT id FROM participants WHERE event_id = ?", (ev["id"],))]
    if len(ids) < MIN_PARTICIPANTS:
        db.rollback()
        return render_event(
            ev, me, status=400,
            draw_error=f"You need at least {MIN_PARTICIPANTS} people to draw names.",
        )
    pairs = make_pairs(ids)
    db.executemany(
        "UPDATE participants SET receiver_id = ? WHERE id = ?",
        [(receiver, giver) for giver, receiver in pairs.items()],
    )
    db.execute(
        "UPDATE events SET status = 'drawn', drawn_at = ? WHERE id = ?", (iso(utcnow()), ev["id"])
    )
    db.commit()
    return redirect(url_for("santa.event", code=code))


@bp.post("/e/<code>/reveal")
def reveal(code):
    ev = get_event(code)
    me = require_me(ev)
    if ev["status"] != "drawn":
        abort(409)
    db = get_db()
    if not me["revealed_at"]:
        db.execute("UPDATE participants SET revealed_at = ? WHERE id = ?", (iso(utcnow()), me["id"]))
        db.commit()
    receiver = get_participant(ev["id"], me["receiver_id"])
    if request.accept_mimetypes.best_match(["text/html", "application/json"]) == "application/json":
        return jsonify(
            name=f"{receiver['first_name']} {receiver['last_name']}",
            wishlist=receiver["wishlist"],
        )
    return redirect(url_for("santa.event", code=code))


# --------------------------------------------------------------------------- app factory


def _secret_key(instance_path):
    """Use SECRET_KEY from the environment, or create one and keep it in instance/."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(instance_path, "secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


def nice_date(value):
    try:
        d = date.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    return f"{d:%a, %b} {d.day}, {d.year}"


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.update(
        DATABASE=os.environ.get("DATABASE", os.path.join(app.instance_path, "secret_santa.db")),
        PERMANENT_SESSION_LIFETIME=timedelta(days=60),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        # Set SECURE_COOKIES=1 when the site is served over HTTPS (it is on PythonAnywhere).
        SESSION_COOKIE_SECURE=os.environ.get("SECURE_COOKIES") == "1",
    )
    # Hosting providers sit behind a proxy; this makes share links use https:// and the real host.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _secret_key(app.instance_path)

    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.filters["nice_date"] = nice_date
    app.teardown_appcontext(close_db)
    app.register_blueprint(bp)

    for code, title, message in [
        (400, "Something went wrong", "That request didn't look right. Go back, reload the page and try again."),
        (403, "Not so fast!", "You need to log in to this Secret Santa first."),
        (404, "Lost in the snow", "We couldn't find that Secret Santa. Check the link and try again."),
        (409, "Not yet!", "The names haven't been drawn yet."),
    ]:
        app.register_error_handler(
            code,
            lambda _e, c=code, t=title, m=message: (
                render_template("error.html", title=t, message=m),
                c,
            ),
        )

    with app.app_context():
        init_db()
    return app


app = create_app()
