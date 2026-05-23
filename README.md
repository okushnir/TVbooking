# Room Booking App

A calendar-based booking app for one shared room, with username/password login.
**No Google Cloud, no OAuth.**

- Only users defined in secrets can sign in and book
- **5 hours per user per day** (per-user cap, configurable)
- Room can't be double-booked (overlaps blocked)
- **Admins can cancel any booking; everyone else only their own**
- Bookings stored in a SQL database

---

## Configure

Edit the **CONFIG** block at the top of `app.py`:

| Setting | Meaning |
|---|---|
| `ADMINS` | emails that can cancel anyone's booking |
| `DAILY_USER_HOURS` | per-user hours/day (`5.0`) |
| `OPEN_HOUR` / `CLOSE_HOUR` | bookable window |
| `SLOT_MINUTES` | granularity |

The set of people who can log in is whoever you list under
`[credentials.usernames.*]` in secrets — that *is* the allow-list.

---

## Users & login

In `.streamlit/secrets.toml`:

- Set `[cookie] key` to a long random string
  (`python -c "import secrets; print(secrets.token_hex(32))"`).
- Add one `[credentials.usernames.<username>]` block per person (`email`,
  `name`, `password`). The `<username>` is what they type to log in.
- Passwords are plaintext and hashed automatically at runtime — no extra step.
- Make sure each admin's `email` matches an entry in `ADMINS` in `app.py`.

## Storage

The app uses SQLAlchemy, so the database is just a connection URL.

- **Local (default):** nothing to set up. Bookings go in a `bookings.db` SQLite
  file next to `app.py`. Great for development and testing.
- **Online / persistent:** Streamlit Community Cloud wipes local files on every
  reboot or redeploy, so for a live shared app use a free hosted Postgres (no
  GCP):
  1. Create a free database at **neon.tech** or **supabase.com**.
  2. Copy its connection string.
  3. Add to secrets:
     ```toml
     [database]
     url = "postgresql+psycopg2://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require"
     ```
  The table is created automatically on first run either way.

---

## Run locally

```bash
pip install -r requirements.txt
mkdir -p .streamlit
cp secrets.toml.example .streamlit/secrets.toml   # then fill it in
streamlit run app.py
```

## Deploy online — Streamlit Community Cloud (free)

1. Push `app.py` + `requirements.txt` to GitHub (never commit secrets; also add
   `bookings.db` and `.streamlit/secrets.toml` to `.gitignore`).
2. https://share.streamlit.io → *New app* → pick the repo and `app.py`.
3. *Advanced settings → Secrets*: paste your filled-in `secrets.toml`, including
   the `[database]` Postgres URL so bookings persist.
4. Deploy. The public `*.streamlit.app` URL is your live app.

---

## Notes

- **Concurrency.** Each booking is re-validated against a fresh read before
  writing, so the per-user cap and overlap checks hold under simultaneous use.
- **Change a password / add a user** = edit secrets (and redeploy on the cloud).
- `psycopg2-binary` in requirements is only used when you set a Postgres URL;
  SQLite needs nothing extra.
