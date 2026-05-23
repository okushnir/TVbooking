"""
Room Booking App
----------------
Calendar-based booking for a single shared room, with username/password login.

- Only users defined in secrets ([credentials]) can sign in and book.
- Each user can book up to a per-user daily cap (default 5 hours/day).
- The room can't be double-booked (overlaps blocked).
- Only admins can cancel other people's bookings; everyone can cancel their own.
- State lives in a SQL database (SQLite by default; any SQLAlchemy URL works).

Login: streamlit-authenticator (no OAuth, no consent screen).
Storage: SQL database (SQLite locally; point at free Postgres for online).
"""

import copy
import zlib
import uuid
from datetime import datetime, date

import pandas as pd
import streamlit as st
import streamlit_authenticator as stauth
from sqlalchemy import create_engine, text
from streamlit_calendar import calendar

# ---------------------------------------------------------------------------
# CONFIG  — edit these to taste
# ---------------------------------------------------------------------------
ROOM_NAME = "TV Room"

# Admins can cancel ANY booking. Everyone else can only cancel their own.
# Use the same emails you set under [credentials...] in secrets.
ADMINS = [
    "odedkushnir@gmail.com",
    "sharonkushnir@gmail.com",
]

DAILY_USER_HOURS = 5.0          # max hours each user may book per day
OPEN = "08:00"                  # earliest bookable time
CLOSE = "22:30"                 # latest bookable time
SLOT_MINUTES = 30               # booking granularity

# Time ranges that are locked every day and cannot be booked (e.g. reserved).
BLOCKED_SLOTS = [("14:30", "16:30")]

_oh, _om = map(int, OPEN.split(":")); OPEN_MIN = _oh * 60 + _om
_ch, _cm = map(int, CLOSE.split(":")); CLOSE_MIN = _ch * 60 + _cm

USER_COLORS = ["#0ea5a5", "#6366f1", "#f59e0b", "#ec4899", "#10b981", "#ef4444"]

st.set_page_config(page_title=f"{ROOM_NAME} Booking", page_icon="📅", layout="wide")


# ---------------------------------------------------------------------------
# AUTH (username/password via streamlit-authenticator)
# ---------------------------------------------------------------------------
def _to_plain(obj):
    """st.secrets returns read-only AttrDicts; convert to plain mutable types."""
    if hasattr(obj, "items"):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain(x) for x in obj]
    return obj


def login():
    try:
        cookie = st.secrets["cookie"]
        creds = _to_plain(st.secrets["credentials"])
    except KeyError:
        st.title(f"📅 {ROOM_NAME}")
        st.error(
            "Login isn't configured. Add `[cookie]` and `[credentials]` to your "
            "Streamlit secrets (see README.md)."
        )
        st.stop()

    authenticator = stauth.Authenticate(
        copy.deepcopy(creds),
        cookie["name"],
        cookie["key"],
        float(cookie.get("expiry_days", 30)),
    )
    authenticator.login(location="main")

    status = st.session_state.get("authentication_status")
    if status is False:
        st.error("Username or password is incorrect.")
        st.stop()
    if status is None:
        st.info("Please log in to continue.")
        st.stop()

    username = st.session_state["username"]
    name = st.session_state.get("name") or username
    email = creds["usernames"].get(username, {}).get("email", username).lower()
    return authenticator, email, name


authenticator, me_email, me_name = login()
is_admin = me_email in [a.lower() for a in ADMINS]

# ---------------------------------------------------------------------------
# DATABASE BACKEND (SQLAlchemy)
#   Default: local SQLite file (bookings.db). For persistent ONLINE storage,
#   set [database] url in secrets to a free Postgres URL (Neon / Supabase).
# ---------------------------------------------------------------------------
HEADERS = ["id", "date", "email", "name", "start", "end", "created_at"]


@st.cache_resource(show_spinner=False)
def get_engine():
    try:
        url = st.secrets["database"]["url"]
    except (KeyError, FileNotFoundError):
        url = "sqlite:///bookings.db"

    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(url, pool_pre_ping=True)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bookings (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                email TEXT NOT NULL,
                name TEXT,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                created_at TEXT
            )
        """))
    return engine


def load_bookings() -> pd.DataFrame:
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT id, date, email, name, start_time, end_time, created_at FROM bookings"
        )).mappings().all()
    if not rows:
        return pd.DataFrame(columns=HEADERS)
    return pd.DataFrame(rows).rename(
        columns={"start_time": "start", "end_time": "end"}
    )[HEADERS]


def add_booking(b_date: date, email: str, name: str, start: str, end: str):
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO bookings (id, date, email, name, start_time, end_time, created_at)
            VALUES (:id, :date, :email, :name, :start, :end, :created_at)
        """), {
            "id": str(uuid.uuid4())[:8],
            "date": b_date.isoformat(),
            "email": email,
            "name": name,
            "start": start,
            "end": end,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })


def delete_booking(booking_id: str):
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM bookings WHERE id = :id"), {"id": booking_id})


# ---------------------------------------------------------------------------
# TIME HELPERS
# ---------------------------------------------------------------------------
def to_minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def slot_options():
    out, t = [], OPEN_MIN
    while t < CLOSE_MIN:
        out.append(fmt(t))
        t += SLOT_MINUTES
    return out


def blocked_windows():
    """Locked daily ranges as (start_min, end_min) tuples."""
    return [(to_minutes(s), to_minutes(e)) for s, e in BLOCKED_SLOTS]


def hits_blocked(start_m: int, end_m: int):
    """Return the first blocked window the range overlaps, else None."""
    for b_s, b_e in blocked_windows():
        if start_m < b_e and end_m > b_s:
            return (fmt(b_s), fmt(b_e))
    return None


def day_bookings(df: pd.DataFrame, b_date: date) -> pd.DataFrame:
    return df if df.empty else df[df["date"] == b_date.isoformat()].copy()


def minutes_for(df_day: pd.DataFrame, email: str | None = None) -> int:
    rows = df_day if email is None else df_day[df_day["email"].str.lower() == email.lower()]
    return int(sum(to_minutes(r["end"]) - to_minutes(r["start"]) for _, r in rows.iterrows()))


def overlaps(df_day: pd.DataFrame, start_m: int, end_m: int) -> bool:
    return any(
        start_m < to_minutes(r["end"]) and end_m > to_minutes(r["start"])
        for _, r in df_day.iterrows()
    )


def color_for(email: str) -> str:
    return USER_COLORS[zlib.crc32(email.lower().encode()) % len(USER_COLORS)]


def all_users():
    """All configured users as (email, name), for the colour legend."""
    try:
        u = _to_plain(st.secrets["credentials"]["usernames"])
    except Exception:  # noqa: BLE001
        return []
    return [(v.get("email", k).lower(), v.get("name", k)) for k, v in u.items()]


def chip(color: str, label: str, dot: bool = True) -> str:
    dot_html = (
        f"<span style='width:11px;height:11px;border-radius:3px;background:{color};"
        f"display:inline-block;flex:0 0 auto;'></span>" if dot else ""
    )
    return (
        "<span style='display:inline-flex;align-items:center;gap:7px;"
        "margin:0 14px 8px 0;font-size:0.95rem;'>"
        f"{dot_html}<span>{label}</span></span>"
    )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
      .block-container { padding-top: 2.2rem; }
      .room-banner {
        background: linear-gradient(135deg, #0f2a3f 0%, #0e7c7b 100%);
        border-radius: 14px; padding: 18px 24px; margin-bottom: 8px;
        color: #f8fafc;
      }
      .room-banner h1 { margin: 0; font-size: 1.7rem; }
      .room-banner p { margin: 4px 0 0; opacity: .85; font-size: .95rem; }
      .legend-box {
        display: flex; flex-wrap: wrap; align-items: center;
        background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);
        border-radius: 10px; padding: 10px 14px; margin: 6px 0 4px;
      }
      .bk-card {
        border-left: 6px solid var(--c,#0e7c7b);
        background: rgba(255,255,255,0.035);
        border-radius: 8px; padding: 9px 14px; margin-bottom: 2px;
      }
      .bk-time { font-size: 1.05rem; font-weight: 600; }
      .bk-meta { opacity: .75; }
    </style>
    """,
    unsafe_allow_html=True,
)
st.markdown(
    f"""
    <div class="room-banner">
      <h1>📅 {ROOM_NAME}</h1>
      <p>Open {OPEN}–{CLOSE} · max {DAILY_USER_HOURS:g} h/day per user · {SLOT_MINUTES}-min slots · locked daily {", ".join(f"{s}–{e}" for s, e in BLOCKED_SLOTS)}</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown(f"**{me_name}**")
    st.caption(me_email + ("  ·  admin" if is_admin else ""))
    authenticator.logout(button_name="Log out", location="sidebar")
    st.markdown("---")
    sel_date = st.date_input("Date", value=date.today(), min_value=date.today())

try:
    df = load_bookings()
except Exception as e:  # noqa: BLE001
    st.error(f"Could not reach the bookings database: {e}")
    st.stop()

# ---- Who's who legend (everyone can see who books) ----
users = all_users()
if users:
    legend = "".join(
        chip(color_for(e), f"<b>{n}</b>" + (" (you)" if e == me_email else ""))
        for e, n in users
    )
    st.markdown(f"<div class='legend-box'>{legend}</div>", unsafe_allow_html=True)

# ---- Calendar (all bookings, visible to everyone) ----
events = [
    {
        "title": f"{r['name'] or r['email']}"
                 + ("  (you)" if r["email"].lower() == me_email.lower() else ""),
        "start": f"{r['date']}T{r['start']}:00",
        "end": f"{r['date']}T{r['end']}:00",
        "backgroundColor": color_for(r["email"]),
        "borderColor": color_for(r["email"]),
    }
    for _, r in df.iterrows()
]
# Locked windows shown as a grey band every day
for b_s, b_e in BLOCKED_SLOTS:
    events.append({
        "title": "Locked",
        "daysOfWeek": [0, 1, 2, 3, 4, 5, 6],
        "startTime": f"{b_s}:00",
        "endTime": f"{b_e}:00",
        "display": "background",
        "color": "#64748b",
    })
calendar(
    events=events,
    options={
        "initialView": "timeGridWeek",
        "initialDate": sel_date.isoformat(),
        "slotMinTime": f"{OPEN}:00",
        "slotMaxTime": f"{CLOSE}:00",
        "slotDuration": f"00:{SLOT_MINUTES:02d}:00",
        "allDaySlot": False,
        "nowIndicator": True,
        "expandRows": True,
        "eventTimeFormat": {"hour": "2-digit", "minute": "2-digit", "hour12": False},
        "slotLabelFormat": {"hour": "2-digit", "minute": "2-digit", "hour12": False},
        "headerToolbar": {
            "left": "prev,next today",
            "center": "title",
            "right": "timeGridWeek,timeGridDay",
        },
        "height": 600,
    },
    key="calendar",
)

# ---- This day's status (for me) ----
df_day = day_bookings(df, sel_date)
my_used = minutes_for(df_day, me_email)
my_remaining = int(DAILY_USER_HOURS * 60) - my_used

c1, c2, c3 = st.columns(3)
c1.metric("Your hours today", f"{my_used/60:g} h")
c2.metric("Your remaining today", f"{my_remaining/60:g} h")
c3.metric("Total bookings today", len(df_day))

st.progress(
    min(my_used / (DAILY_USER_HOURS * 60), 1.0),
    text=f"Your usage: {my_used/60:g} / {DAILY_USER_HOURS:g} h",
)

# ---- Booking form ----
st.subheader(f"Book {ROOM_NAME} — {sel_date:%A %d %b %Y}")

if my_remaining <= 0:
    st.warning(f"You've reached your {DAILY_USER_HOURS:g} h limit for this day.")
else:
    fc1, fc2 = st.columns(2)
    # Don't offer start times that fall inside a locked window
    starts = [s for s in slot_options() if not hits_blocked(to_minutes(s), to_minutes(s) + SLOT_MINUTES)]
    start_choice = fc1.selectbox("Start time", starts)
    start_m0 = to_minutes(start_choice)
    # A booking can't run past close, past the user's remaining hours, or into
    # the next locked window that starts after this start time.
    next_block = min(
        [b_s for b_s, _ in blocked_windows() if b_s > start_m0] + [CLOSE_MIN]
    )
    max_dur = min(
        DAILY_USER_HOURS,
        my_remaining / 60,
        (next_block - start_m0) / 60,
    )
    dur_steps = [d / 60 for d in range(SLOT_MINUTES, int(max_dur * 60) + 1, SLOT_MINUTES)]
    if not dur_steps:
        st.info("No room left after this start time — pick an earlier slot.")
    else:
        duration = fc2.selectbox("Duration (hours)", dur_steps, format_func=lambda d: f"{d:g} h")
        if st.button("Book it", type="primary"):
            start_m = to_minutes(start_choice)
            end_m = start_m + int(duration * 60)

            # Re-read fresh right before writing to avoid race conditions
            fresh_day = day_bookings(load_bookings(), sel_date)
            fresh_mine = minutes_for(fresh_day, me_email)

            blocked = hits_blocked(start_m, end_m)
            if overlaps(fresh_day, start_m, end_m):
                st.error("That overlaps an existing booking. Pick another slot.")
            elif blocked:
                st.error(f"That overlaps the locked period ({blocked[0]}–{blocked[1]}).")
            elif end_m > CLOSE_MIN:
                st.error(f"Booking would end after closing ({CLOSE}).")
            elif fresh_mine + (end_m - start_m) > DAILY_USER_HOURS * 60:
                st.error(
                    f"That exceeds your {DAILY_USER_HOURS:g} h daily limit "
                    f"({fresh_mine/60:g} h already booked)."
                )
            else:
                add_booking(sel_date, me_email, me_name, fmt(start_m), fmt(end_m))
                st.success(f"Booked {start_choice}–{fmt(end_m)}.")
                st.rerun()

# ---- Day's bookings (graphical, visible to everyone) ----
st.subheader(f"Bookings on {sel_date:%A %d %b %Y}")
if df_day.empty:
    st.info("No bookings yet for this day.")
else:
    # quick "booked today by" summary row
    booked_emails = list(dict.fromkeys(df_day["email"].str.lower()))
    summary = "".join(
        chip(
            color_for(e),
            (df_day[df_day["email"].str.lower() == e]["name"].iloc[0] or e)
            + (" (you)" if e == me_email else ""),
        )
        for e in booked_emails
    )
    st.markdown(
        f"<div class='legend-box'><span style='opacity:.7;margin-right:10px;'>"
        f"Booked today:</span>{summary}</div>",
        unsafe_allow_html=True,
    )

    for _, r in df_day.sort_values("start").iterrows():
        col_a, col_b = st.columns([6, 1])
        mine = r["email"].lower() == me_email.lower()
        who = f"{r['name'] or r['email']}" + (" (you)" if mine else "")
        c = color_for(r["email"])
        hrs = (to_minutes(r["end"]) - to_minutes(r["start"])) / 60
        col_a.markdown(
            f"<div class='bk-card' style='--c:{c};'>"
            f"<span class='bk-time'>{r['start']} – {r['end']}</span>"
            f"<span class='bk-meta'> · {hrs:g} h</span><br>"
            f"<span style='display:inline-flex;align-items:center;gap:7px;'>"
            f"<span style='width:10px;height:10px;border-radius:50%;background:{c};"
            f"display:inline-block;'></span>{who}</span></div>",
            unsafe_allow_html=True,
        )
        if mine or is_admin:
            col_b.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
            if col_b.button("Cancel", key=f"del_{r['id']}"):
                delete_booking(r["id"])
                st.rerun()