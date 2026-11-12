# 🎅 Secret Santa

A small Flask app for Secret Santa groups whose members join at different times.

## How it works

1. **The organizer** clicks *New Secret Santa* and enters an event name, their name and a password.
   They get a share link (for example `/e/k7Qx9mPz`). The organizer takes part in the draw too.
2. **Participants** open the link and join with their first name, last name and a 4–6 digit PIN.
   They can add a wishlist.
3. **The organizer** can remove mistakes, then clicks *Close sign-ups & draw names*. Everyone is
   paired at once in one random circle, so nobody gets themselves. At least 3 people are needed.
4. **Everyone** goes back to the same link, logs in with just their first name + PIN (the organizer
   uses their password) and taps the gift to see who they're buying for, plus that person's wishlist.
   If two people share a first name and PIN, the page also asks for the last name.

The organizer can see who has opened their gift, but not who got whom.

## Run it locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/flask --app app run --debug --port 5050
```

Then open http://127.0.0.1:5050.

Tip: to act as several people at once, use a normal window, a private window and another browser.
Each one keeps its own login.

## Tests

```bash
.venv/bin/pytest -q
```

## Data

Everything is stored in SQLite at `instance/secret_santa.db`. The session signing key is created
on first run at `instance/secret_key` (or set the `SECRET_KEY` environment variable).
The database path can be changed with the `DATABASE` environment variable.

PINs and passwords are stored hashed. After 5 wrong PINs, that name is locked for 15 minutes.

## Deploy to PythonAnywhere (free)

Replace `USERNAME` with your PythonAnywhere username.

1. **Get the code.** Open a *Bash console* (Consoles tab) and run:

   ```bash
   git clone https://github.com/aflores-msc/secret-santa.git
   python3.13 -m venv ~/secret-santa/.venv
   ~/secret-santa/.venv/bin/pip install -r ~/secret-santa/requirements.txt
   ```

2. **Create the web app.** Web tab → *Add a new web app* → *Manual configuration*
   (not "Flask") → *Python 3.13*.

3. **Point it at the code.** On the Web tab:
   - *Source code*: `/home/USERNAME/secret-santa`
   - *Virtualenv*: `/home/USERNAME/secret-santa/.venv`
   - *Static files*: URL `/static/` → directory `/home/USERNAME/secret-santa/static`
   - *Force HTTPS*: on

4. **Edit the WSGI file** (link on the Web tab). Replace everything in it with:

   ```python
   import os
   import sys

   os.environ["SECURE_COOKIES"] = "1"
   path = "/home/USERNAME/secret-santa"
   if path not in sys.path:
       sys.path.insert(0, path)

   from app import app as application
   ```

5. Click **Reload** and open `https://USERNAME.pythonanywhere.com`.

Free accounts must log in and click *Run until 1 month from today* on the Web tab every month,
or the site is switched off (your data is kept). PythonAnywhere emails a reminder a week before.

**Updating later:** push changes to GitHub, then in a PythonAnywhere Bash console run
`cd ~/secret-santa && git pull`, and click **Reload** on the Web tab.

## Forgotten password or PIN

Run this in a Bash console in the project folder (use your own event code, which is the part of
the share link after `/e/`). It asks for the new password twice and also clears any lockout:

```bash
.venv/bin/flask --app app reset-password --event=EVENTCODE --first=Armando --last=Flores
```
