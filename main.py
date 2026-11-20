import os
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)
from yt_dlp import YoutubeDL

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_USER_ID", "0") or 0)
CHANNEL = os.getenv("CHANNEL_USERNAME", "@zayyasearchmusic")
PAYMENT_NAME = os.getenv("TELEBIRR_NAME", "Amir Ali")
PAYMENT_PHONE = os.getenv("TELEBIRR_PHONE", "0967757546")
MONTHLY_PRICE = int(os.getenv("MONTHLY_PRICE", "200"))
FREE_LIMIT = int(os.getenv("DAILY_FREE_SEARCHES", "10"))
DB_PATH = os.getenv("DATABASE_PATH", "zayya_music.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("zayya-music")


def db():
    # Ensure the configured database folder exists. For /var/data, Render
    # must have a Persistent Disk mounted at /var/data.
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    try:
        os.makedirs(db_dir, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot access database folder '{db_dir}'. "
            "In Render, add a Persistent Disk and set its Mount Path to /var/data, "
            "then set DATABASE_PATH=/var/data/zayya_music.db."
        ) from exc
    try:
        con = sqlite3.connect(DB_PATH)
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            f"Cannot open SQLite database at '{DB_PATH}'. "
            "Check that Render Persistent Disk is mounted at /var/data "
            "and DATABASE_PATH is /var/data/zayya_music.db."
        ) from exc
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            joined_at TEXT NOT NULL, referrer INTEGER, credits INTEGER NOT NULL DEFAULT 0,
            premium_until TEXT, last_search_day TEXT, searches_today INTEGER NOT NULL DEFAULT 0,
            invited_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS coupons(
            code TEXT PRIMARY KEY, credits INTEGER NOT NULL, uses_left INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS coupon_uses(
            code TEXT NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY(code,user_id)
        );
        """)


def now_utc():
    return datetime.now(timezone.utc)


def premium_active(row):
    if not row or not row["premium_until"]:
        return False
    try:
        return datetime.fromisoformat(row["premium_until"]) > now_utc()
    except ValueError:
        return False


def get_user(uid):
    with db() as con:
        return con.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()


def ensure_user(tg_user, referrer=None):
    uid = tg_user.id
    with db() as con:
        old = con.execute("SELECT user_id FROM users WHERE user_id=?", (uid,)).fetchone()
        if old:
            con.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?",
                        (tg_user.username or "", tg_user.first_name or "", uid))
            return False
        if referrer == uid:
            referrer = None
        con.execute("""INSERT INTO users(user_id,username,first_name,joined_at,referrer)
                       VALUES(?,?,?,?,?)""",
                    (uid, tg_user.username or "", tg_user.first_name or "",
                     now_utc().isoformat(), referrer))
        if referrer:
            con.execute("UPDATE users SET credits=credits+1, invited_count=invited_count+1 WHERE user_id=?",
                        (referrer,))
        return True


async def channel_ok(context, uid):
    try:
        member = await context.bot.get_chat_member(CHANNEL, uid)
        return member.status in ("member", "administrator", "creator")
    except Exception as exc:
        log.warning("Channel membership check failed: %s", exc)
        return False


async def require_channel(update, context):
    uid = update.effective_user.id
    if await channel_ok(context, uid):
        return True
    keyboard = [
        [InlineKeyboardButton("📢 Join Telegram Channel", url=f"https://t.me/{CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton("✅ Verify Membership", callback_data="verify_channel")]
    ]
    message = "To use ZAYYA SEARCH MUSIC, first join our Telegram channel, then tap Verify Membership."
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.effective_message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ref = None
    if context.args:
        try:
            ref = int(context.args[0].replace("ref_", ""))
        except ValueError:
            ref = None
    ensure_user(update.effective_user, ref)
    if not await require_channel(update, context):
        return
    await show_home(update, context)


async def show_home(update, context):
    keyboard = [
        [InlineKeyboardButton("🔎 Search Music", callback_data="search_help"),
         InlineKeyboardButton("👤 My Account", callback_data="account")],
        [InlineKeyboardButton("💎 Upgrade", callback_data="upgrade_info"),
         InlineKeyboardButton("🎁 Invite Friends", callback_data="invite")],
        [InlineKeyboardButton("🎟 Redeem Coupon", callback_data="coupon_help"),
         InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ]
    text = "🎵 Welcome to ZAYYA SEARCH MUSIC!\n\nSearch songs by title or artist. We show listening links and details."
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def menu_callback(update, context):
    q = update.callback_query
    await q.answer()
    if q.data == "verify_channel":
        if await channel_ok(context, q.from_user.id):
            await q.edit_message_text("✅ Channel verified! You can now use the bot.")
            await show_home(update, context)
        else:
            await q.answer("Please join the channel first.", show_alert=True)
        return
    if not await require_channel(update, context):
        return
    if q.data == "home":
        await show_home(update, context)
    elif q.data == "account":
        row = get_user(q.from_user.id)
        premium = "Active until " + row["premium_until"][:10] if premium_active(row) else "Free"
        day = now_utc().date().isoformat()
        used = row["searches_today"] if row["last_search_day"] == day else 0
        left = "Unlimited" if premium_active(row) else str(max(0, FREE_LIMIT-used))
        await q.edit_message_text(
            f"👤 My Account\nName: {q.from_user.first_name}\nUser ID: {q.from_user.id}\n"
            f"Plan: {premium}\nToday's free searches left: {left}\nCredits: {row['credits']}\n"
            f"Invited friends: {row['invited_count']}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="home")]])
        )
    elif q.data == "invite":
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start={q.from_user.id}"
        await q.edit_message_text(
            f"🎁 Invite friends\nShare this link:\n{link}\n\nYou earn 1 credit for each new user who starts through your link.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="home")]])
        )
    elif q.data == "upgrade_info":
        await q.edit_message_text(
            f"💎 Premium: {MONTHLY_PRICE} ETB for 30 days.\nTelebirr name: {PAYMENT_NAME}\n"
            f"Telebirr number: {PAYMENT_PHONE}\n\nAfter payment, send the payment screenshot to the bot. "
            "An admin will review it and activate Premium.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="home")]])
        )
    elif q.data == "search_help":
        await q.edit_message_text("🔎 Send a song name or artist name here to search. Example: Kida 4k",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="home")]]))
    elif q.data == "coupon_help":
        await q.edit_message_text("Use /redeem YOUR_CODE to redeem a coupon.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="home")]]))
    elif q.data == "help":
        await q.edit_message_text("Send a song or artist name to search. Free accounts get 10 searches per day. Premium accounts get unlimited searches for 30 days.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="home")]]))


def consume_search(uid):
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row:
            return False, "Please send /start first."
        if premium_active(row):
            return True, ""
        today = now_utc().date().isoformat()
        used = row["searches_today"] if row["last_search_day"] == today else 0
        if used < FREE_LIMIT:
            con.execute("UPDATE users SET searches_today=?,last_search_day=? WHERE user_id=?",
                        (used+1, today, uid))
            return True, ""
        if row["credits"] > 0:
            con.execute("UPDATE users SET credits=credits-1 WHERE user_id=?", (uid,))
            return True, "One referral credit was used."
        return False, f"Daily limit reached ({FREE_LIMIT}). Invite friends to earn credits or upgrade to Premium."


async def search_music(update, context):
    """Search YouTube metadata and let the user choose a numbered result."""
    if not await require_channel(update, context):
        return
    query = update.effective_message.text.strip()
    if not query or query.startswith("/"):
        return
    ok, note = consume_search(update.effective_user.id)
    if not ok:
        await update.effective_message.reply_text(note)
        return
    status = await update.effective_message.reply_text("🔎 Searching music...")
    try:
        opts = {
            "quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": True, "default_search": "ytsearch10"
        }
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
        entries = (info or {}).get("entries") or []
        results = []
        for item in entries[:10]:
            video_id = item.get("id")
            url = item.get("webpage_url") or item.get("url")
            if url and not str(url).startswith("http"):
                url = "https://www.youtube.com/watch?v=" + str(url)
            if not url and video_id:
                url = "https://www.youtube.com/watch?v=" + str(video_id)
            if not url:
                continue
            results.append({
                "title": item.get("title") or "Unknown title",
                "artist": item.get("uploader") or item.get("channel") or "Unknown artist",
                "duration": item.get("duration"),
                "url": url
            })
        if not results:
            await status.edit_text("No results found. Try another song or artist.")
            return
        context.user_data["music_results"] = results
        context.user_data["music_query"] = query
        keyboard = []
        row_buttons = []
        for i, item in enumerate(results):
            duration = item["duration"]
            dtext = f"{int(duration)//60}:{int(duration)%60:02d}" if duration else "--:--"
            label = f"{i+1}. {item['title'][:17]} · {dtext}"
            row_buttons.append(InlineKeyboardButton(label, callback_data=f"pick:{i}"))
            if len(row_buttons) == 2:
                keyboard.append(row_buttons)
                row_buttons = []
        if row_buttons:
            keyboard.append(row_buttons)
        keyboard.append([InlineKeyboardButton("🔎 Search another song", callback_data="search_help")])
        heading = f"🎵 Results for: {query}\n\nChoose the song you want:"
        if note:
            heading += f"\n\n{note}"
        await status.edit_text(heading, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        log.exception("Music search failed")
        await status.edit_text("Search is temporarily unavailable. Please try again later.")


async def pick_music(update, context):
    q = update.callback_query
    await q.answer()
    if not await channel_ok(context, q.from_user.id):
        return await q.edit_message_text(
            "Please join our channel first: https://t.me/zayyasearchmusic"
        )
    try:
        index = int(q.data.split(":", 1)[1])
        results = context.user_data.get("music_results", [])
        item = results[index]
    except (ValueError, IndexError, TypeError):
        return await q.edit_message_text("This search has expired. Send the song name again.")
    duration = item["duration"]
    dtext = f"{int(duration)//60}:{int(duration)%60:02d}" if duration else "Unknown"
    text = (
        f"🎶 Selected music\n\n"
        f"🎵 {item['title']}\n"
        f"👤 {item['artist']}\n"
        f"⏱ Duration: {dtext}\n\n"
        f"▶️ Listen here: {item['url']}\n\n"
        "This is an official platform listening link; the bot does not download or send copyrighted audio."
    )
    keyboard = [
        [InlineKeyboardButton("▶️ Open music", url=item["url"])],
        [InlineKeyboardButton("⬅️ Back to results", callback_data="back_results")],
        [InlineKeyboardButton("🔎 Search another song", callback_data="search_help")]
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def back_results(update, context):
    q = update.callback_query
    await q.answer()
    if not await channel_ok(context, q.from_user.id):
        return await q.edit_message_text("Please join our channel first: https://t.me/zayyasearchmusic")
    results = context.user_data.get("music_results", [])
    query = context.user_data.get("music_query", "")
    if not results:
        return await q.edit_message_text("This search has expired. Send the song name again.")
    keyboard = []
    row_buttons = []
    for i, item in enumerate(results):
        duration = item["duration"]
        dtext = f"{int(duration)//60}:{int(duration)%60:02d}" if duration else "--:--"
        row_buttons.append(InlineKeyboardButton(f"{i+1}. {item['title'][:17]} · {dtext}", callback_data=f"pick:{i}"))
        if len(row_buttons) == 2:
            keyboard.append(row_buttons)
            row_buttons = []
    if row_buttons:
        keyboard.append(row_buttons)
    keyboard.append([InlineKeyboardButton("🔎 Search another song", callback_data="search_help")])
    await q.edit_message_text(f"🎵 Results for: {query}\n\nChoose the song you want:", reply_markup=InlineKeyboardMarkup(keyboard))


async def photo_payment(update, context):
    if not await require_channel(update, context):
        return
    if update.message.photo:
        uid = update.effective_user.id
        await update.message.reply_text("📨 Payment proof received. Admin will review it. Premium is not active until approved.")
        if ADMIN_ID:
            caption = f"Premium payment proof\nUser: {update.effective_user.full_name}\nUser ID: {uid}\nUsername: @{update.effective_user.username or 'none'}"
            await context.bot.send_photo(ADMIN_ID, update.message.photo[-1].file_id, caption=caption,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Approve 30 days", callback_data=f"approve:{uid}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject:{uid}")
                ]]))


async def admin_callback(update, context):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("Admin only.", show_alert=True)
        return
    await q.answer()
    action, raw_uid = q.data.split(":", 1)
    uid = int(raw_uid)
    if action == "approve":
        until = max(now_utc(), datetime.fromisoformat(get_user(uid)["premium_until"]) if get_user(uid)["premium_until"] else now_utc()) + timedelta(days=30)
        with db() as con:
            con.execute("UPDATE users SET premium_until=? WHERE user_id=?", (until.isoformat(), uid))
        await q.edit_message_caption((q.message.caption or "") + "\n\nAPPROVED")
        await context.bot.send_message(uid, f"🎉 Premium activated until {until.date().isoformat()}!")
    elif action == "reject":
        await q.edit_message_caption((q.message.caption or "") + "\n\nREJECTED")
        await context.bot.send_message(uid, "Your payment proof was not approved. Please contact the admin if you think this is a mistake.")


async def admin(update, context):
    if update.effective_user.id != ADMIN_ID:
        return await update.effective_message.reply_text("Admin only.")
    with db() as con:
        total = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        premium = con.execute("SELECT COUNT(*) n FROM users WHERE premium_until > ?", (now_utc().isoformat(),)).fetchone()["n"]
        pending = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    keyboard = [[InlineKeyboardButton("👥 Users", callback_data="admin_users")],
                [InlineKeyboardButton("⬆️ Upgrade selected user", callback_data="admin_upgrade")]]
    await update.effective_message.reply_text(
        f"🛠 Admin Dashboard\nTotal users: {total}\nPremium users: {premium}\n"
        f"Monthly price: {MONTHLY_PRICE} ETB\n\nCommands:\n/coupon CODE CREDITS [USES]\n"
        "/upgrade USER_ID [DAYS]\n/users",
        reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_menu(update, context):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        return await q.answer("Admin only.", show_alert=True)
    await q.answer()
    if q.data == "admin_users":
        with db() as con:
            rows = con.execute("SELECT user_id,first_name,username,premium_until FROM users ORDER BY joined_at DESC LIMIT 30").fetchall()
        if not rows:
            return await q.edit_message_text("No users yet.")
        buttons = []
        for row in rows:
            label = f"{row['first_name'] or 'User'} · {row['user_id']}"
            buttons.append([InlineKeyboardButton("⬆️ " + label[:55], callback_data=f"quickup:{row['user_id']}")])
        await q.edit_message_text("Select a user to grant 30 days Premium:", reply_markup=InlineKeyboardMarkup(buttons))
    elif q.data == "admin_upgrade":
        await q.edit_message_text("Use /users, then select the user. Or send /upgrade USER_ID [DAYS].")


async def quick_upgrade(update, context):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        return await q.answer("Admin only.", show_alert=True)
    uid = int(q.data.split(":")[1])
    row = get_user(uid)
    if not row:
        return await q.answer("User not found.", show_alert=True)
    base = datetime.fromisoformat(row["premium_until"]) if row["premium_until"] and datetime.fromisoformat(row["premium_until"]) > now_utc() else now_utc()
    until = base + timedelta(days=30)
    with db() as con:
        con.execute("UPDATE users SET premium_until=? WHERE user_id=?", (until.isoformat(), uid))
    await q.answer("Premium activated.")
    await q.edit_message_text(f"✅ User {uid} upgraded until {until.date().isoformat()}.")
    await context.bot.send_message(uid, f"🎉 Admin activated your Premium until {until.date().isoformat()}.")


async def users_cmd(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    with db() as con:
        rows = con.execute("SELECT user_id,first_name,username,premium_until FROM users ORDER BY joined_at DESC LIMIT 30").fetchall()
    if not rows:
        return await update.effective_message.reply_text("No users yet.")
    keyboard = [[InlineKeyboardButton(f"⬆️ {r['first_name'] or 'User'} · {r['user_id']}", callback_data=f"quickup:{r['user_id']}")] for r in rows]
    await update.effective_message.reply_text("Select a user to upgrade for 30 days:", reply_markup=InlineKeyboardMarkup(keyboard))


async def upgrade_cmd(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        return await users_cmd(update, context)
    try:
        uid = int(context.args[0])
        days = int(context.args[1]) if len(context.args) > 1 else 30
        days = max(1, min(days, 3650))
    except ValueError:
        return await update.effective_message.reply_text("Usage: /upgrade USER_ID [DAYS]")
    row = get_user(uid)
    if not row:
        return await update.effective_message.reply_text("User not found.")
    base = datetime.fromisoformat(row["premium_until"]) if row["premium_until"] and datetime.fromisoformat(row["premium_until"]) > now_utc() else now_utc()
    until = base + timedelta(days=days)
    with db() as con:
        con.execute("UPDATE users SET premium_until=? WHERE user_id=?", (until.isoformat(), uid))
    await update.effective_message.reply_text(f"✅ User {uid} upgraded until {until.date().isoformat()}.")
    await context.bot.send_message(uid, f"🎉 Premium activated until {until.date().isoformat()}.")


async def coupon_cmd(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    if len(context.args) < 2:
        return await update.effective_message.reply_text("Usage: /coupon CODE CREDITS [USES]")
    code = context.args[0].upper()
    try:
        credits, uses = int(context.args[1]), int(context.args[2]) if len(context.args)>2 else 1
        if credits < 1 or uses < 1: raise ValueError()
    except ValueError:
        return await update.effective_message.reply_text("Credits and uses must be positive numbers.")
    with db() as con:
        con.execute("INSERT OR REPLACE INTO coupons(code,credits,uses_left) VALUES(?,?,?)", (code,credits,uses))
    await update.effective_message.reply_text(f"🎟 Coupon {code} created: {credits} credits each, {uses} use(s).")


async def redeem_cmd(update, context):
    if not context.args:
        return await update.effective_message.reply_text("Usage: /redeem CODE")
    code = context.args[0].upper()
    uid = update.effective_user.id
    with db() as con:
        coupon = con.execute("SELECT * FROM coupons WHERE code=?", (code,)).fetchone()
        used = con.execute("SELECT 1 FROM coupon_uses WHERE code=? AND user_id=?", (code,uid)).fetchone()
        if not coupon or coupon["uses_left"] < 1:
            return await update.effective_message.reply_text("Coupon invalid or fully used.")
        if used:
            return await update.effective_message.reply_text("You already used this coupon.")
        con.execute("INSERT INTO coupon_uses(code,user_id) VALUES(?,?)", (code,uid))
        con.execute("UPDATE coupons SET uses_left=uses_left-1 WHERE code=?", (code,))
        con.execute("UPDATE users SET credits=credits+? WHERE user_id=?", (coupon["credits"],uid))
    await update.effective_message.reply_text(f"✅ Coupon redeemed. {coupon['credits']} credits added.")


async def stats_cmd(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    with db() as con:
        total = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        premium = con.execute("SELECT COUNT(*) n FROM users WHERE premium_until > ?", (now_utc().isoformat(),)).fetchone()["n"]
        credits = con.execute("SELECT COALESCE(SUM(credits),0) n FROM users").fetchone()["n"]
    await update.effective_message.reply_text(f"📊 Stats\nUsers: {total}\nPremium: {premium}\nOutstanding credits: {credits}")


async def post_init(app):
    await app.bot.set_my_commands([
        ("start","Start the bot"),("admin","Admin dashboard"),("users","Select a user to upgrade"),
        ("upgrade","Upgrade a user"),("coupon","Create a coupon"),("redeem","Redeem a coupon"),("stats","Bot statistics")
    ])


def main():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required. Add it in Render environment variables.")
    if ADMIN_ID <= 0:
        raise RuntimeError("ADMIN_USER_ID is required. Add your numeric Telegram user ID in Render.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("upgrade", upgrade_cmd))
    app.add_handler(CommandHandler("coupon", coupon_cmd))
    app.add_handler(CommandHandler("redeem", redeem_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^(verify_channel|account|invite|upgrade_info|search_help|coupon_help|help|home)$"))
    app.add_handler(CallbackQueryHandler(pick_music, pattern="^pick:[0-9]+$"))
    app.add_handler(CallbackQueryHandler(back_results, pattern="^back_results$"))
    app.add_handler(CallbackQueryHandler(admin_menu, pattern="^admin_(users|upgrade)$"))
    app.add_handler(CallbackQueryHandler(quick_upgrade, pattern="^quickup:"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(approve|reject):"))
    app.add_handler(MessageHandler(filters.PHOTO, photo_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_music))
    log.info("Starting ZAYYA SEARCH MUSIC bot")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
