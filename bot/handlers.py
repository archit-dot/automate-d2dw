from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from telegram.error import BadRequest, NetworkError

from config import settings
from database import repo
from database.db import log

PENDING = {}
DIALOG_CACHE = {}


def owner(update):
    return bool(update.effective_user and update.effective_user.id == settings.owner_id)


def kb(rows):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows]
    )


def dashboard_text():
    routes = repo.routes()
    active = sum(1 for r in routes if r["autopilot"])
    next_pub = repo.next_publish()
    lines = [
        "🤖 TELEPOST CONTROL CENTER",
        "",
        "🟢 SYSTEM ONLINE",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🔀 Active Routes     {active}",
        f"📡 Sources           {len(repo.sources())}",
        f"📢 Destinations      {len(repo.destinations())}",
        f"📥 Queued Posts      {repo.count_queue()}",
        f"⚙️ Processing         {repo.count_processing()}",
        f"✅ Published Today   {repo.published_today()}",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    if next_pub:
        lines += ["⏭️ NEXT PUBLISH", f"{next_pub['route_name']} → {next_pub['daily_time']}", ""]
    else:
        lines += ["⏭️ NEXT PUBLISH", "—", ""]
    if routes:
        lines.append("🤖 AUTO-PILOT")
        for r in routes[:8]:
            lines.append(
                f"{'🟢' if r['autopilot'] else '🔴'} {r['name']}  "
                f"{repo.route_queue_count(r['id'])}/40"
            )
    return "\n".join(lines)


def main_kb():
    return kb(
        [
            [("🔀 Routes", "routes"), ("📡 Sources", "sources")],
            [("📢 Destinations", "destinations"), ("📥 Queues", "queues")],
            [("📊 Dashboard", "dashboard"), ("⚙️ Settings", "settings")],
        ]
    )


async def safe_edit(q, text, markup=None):
    try:
        return await q.edit_message_text(text=text, reply_markup=markup)
    except BadRequest as exc:
        if "Message is not modified" in str(exc):
            return None
        raise


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner(update):
        return await update.message.reply_text("⛔ Not authorized.")
    PENDING.pop(update.effective_user.id, None)
    await update.message.reply_text(dashboard_text(), reply_markup=main_kb())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner(update):
        return await update.message.reply_text("⛔ Not authorized.")
    await update.message.reply_text(
        "Use the buttons in the Telepost dashboard. /start opens the Control Center."
    )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != settings.owner_id:
        try:
            await q.answer("Not authorized.", show_alert=True)
        except NetworkError:
            pass
        return
    try:
        await q.answer()
    except NetworkError:
        pass

    data = q.data or ""
    if data in {"dashboard", "routes", "sources", "destinations", "queues", "settings"} or data.startswith(("route:", "rmanage:", "queue:", "destpick:")):
        PENDING.pop(q.from_user.id, None)
    try:
        if data == "noop":
            return
        if data == "dashboard":
            return await safe_edit(q, dashboard_text(), main_kb())
        if data == "routes":
            return await show_routes(q)
        if data == "sources":
            return await show_sources(q)
        if data == "destinations":
            return await show_destinations(q)
        if data == "queues":
            return await show_queues(q)
        if data == "settings":
            return await safe_edit(
                q,
                f"⚙️ SETTINGS\n\nTimezone: {settings.timezone}\nDatabase: {settings.db_path}",
                kb([[('🔙 Dashboard', 'dashboard')]]),
            )

        if data == "refresh_entities":
            failures = await context.application.bot_data["telegram_service"].refresh_configured_entities()
            if failures:
                text = "⚠️ REFRESH COMPLETE\n\n" + "\n".join(f"❌ {kind} #{item_id} — {title}" for kind, item_id, title, _ in failures)
            else:
                text = "✅ Telegram access refreshed for all configured chats."
            return await safe_edit(q, text, kb([[('🔙 Sources', 'sources')], [('🔙 Dashboard', 'dashboard')]]))

        if data == "newsource":
            PENDING[q.from_user.id] = {"action": "new_source"}
            return await safe_edit(
                q,
                "📡 ADD SOURCE\n\nChoose a Telegram chat or enter its ID/username.",
                kb([
                    [("📋 Select from Telegram", "dialogs:source")],
                    [("⌨️ Enter manually", "source_manual")],
                    [("❌ Cancel", "sources")],
                ]),
            )
        if data == "newdest":
            PENDING[q.from_user.id] = {"action": "new_destination"}
            return await safe_edit(
                q,
                "📢 ADD DESTINATION\n\nChoose a Telegram chat or enter its ID/username.",
                kb([
                    [("📋 Select from Telegram", "dialogs:destination")],
                    [("⌨️ Enter manually", "destination_manual")],
                    [("❌ Cancel", "destinations")],
                ]),
            )
        if data == "source_manual":
            PENDING[q.from_user.id] = {"action": "new_source_manual"}
            return await safe_edit(q, "📡 SEND SOURCE\n\nUse:\n-1001234567890 | Optional title\n\nor\n@username | Optional title", kb([[('❌ Cancel', 'sources')]]))
        if data == "destination_manual":
            PENDING[q.from_user.id] = {"action": "new_destination_manual"}
            return await safe_edit(q, "📢 SEND DESTINATION\n\nUse:\n-1001234567890 | Optional title\n\nor\n@username | Optional title", kb([[('❌ Cancel', 'destinations')]]))
        if data == "newroute":
            if not repo.destinations():
                return await safe_edit(q, "📢 Add a destination first.", kb([[('➕ Add Destination', 'newdest')], [('🔙 Dashboard', 'dashboard')]]))
            PENDING[q.from_user.id] = {"action": "route_name"}
            return await safe_edit(q, "➕ CREATE ROUTE\n\nSend the route name.", kb([[('❌ Cancel', 'routes')]]))

        if data.startswith("route:"):
            return await route_panel(q, int(data.split(":")[1]))
        if data.startswith("rmanage:"):
            return await route_sources_panel(q, int(data.split(":")[1]))
        if data.startswith("radd:"):
            rid = int(data.split(":")[1])
            selected = {int(s["id"]) for s in repo.route_sources(rid)}
            PENDING[q.from_user.id] = {"action": "route_sources", "route": rid, "selected": selected}
            return await source_picker(q, rid)
        if data.startswith("rtoggle:"):
            _, rid, sid = data.split(":")
            rid, sid = int(rid), int(sid)
            state = PENDING.get(q.from_user.id)
            if not state or state.get("action") != "route_sources" or state.get("route") != rid:
                return await route_sources_panel(q, rid)
            selected = state["selected"]
            if sid in selected:
                selected.remove(sid)
            else:
                selected.add(sid)
            return await source_picker(q, rid)
        if data.startswith("radddone:"):
            rid = int(data.split(":")[1])
            state = PENDING.get(q.from_user.id)
            if state and state.get("action") == "route_sources" and state.get("route") == rid:
                selected = state["selected"]
                current = {int(s["id"]) for s in repo.route_sources(rid)}
                for sid in current - selected:
                    repo.remove_route_source(rid, sid)
                for sid in selected - current:
                    repo.add_route_source(rid, sid)
                PENDING.pop(q.from_user.id, None)
            return await route_sources_panel(q, rid)
        if data.startswith("rremove:"):
            _, rid, sid = data.split(":")
            repo.remove_route_source(int(rid), int(sid))
            return await route_sources_panel(q, int(rid))
        if data.startswith("destpick:"):
            return await destination_picker(q, int(data.split(":")[1]))
        if data.startswith("setdest:"):
            _, rid, did = data.split(":")
            rid, did = int(rid), int(did)
            if not repo.destination(did):
                raise ValueError("Destination not found.")
            with repo.connect() as c:
                c.execute("UPDATE routes SET destination_id=? WHERE id=?", (did, rid))
            return await route_panel(q, rid, "📢 Destination updated.")
        if data.startswith("newroute_dest:"):
            did = int(data.split(":")[1])
            state = PENDING.get(q.from_user.id)
            if not state or state.get("action") != "route_destination":
                return await show_routes(q)
            if not repo.destination(did):
                raise ValueError("Destination is no longer configured.")
            name = state.get("name", "").strip()
            if not name:
                raise ValueError("Route name is empty.")
            rid = repo.add_route(name, did)
            PENDING.pop(q.from_user.id, None)
            return await safe_edit(
                q,
                f"✅ Route created: {name}\n\n📢 Destination: {repo.destination(did)['title']}",
                kb([[('📡 Add Sources', f'radd:{rid}')], [('⚙️ Open Route', f'route:{rid}')], [('🔙 Routes', 'routes')]]),
            )
        if data.startswith("autopilot:"):
            rid = int(data.split(":")[1])
            r = repo.route(rid)
            if not r:
                return await show_routes(q)
            if not repo.route_sources(rid):
                return await route_panel(q, rid, "❌ Add at least one source before starting Auto-Pilot.")
            if not r["destination_enabled"] or r["destination_paused"]:
                return await route_panel(q, rid, "❌ Destination is disabled or paused.")
            enabling = not bool(r["autopilot"])
            repo.set_autopilot(rid, enabling)
            monitor = context.application.bot_data["source_monitor"]
            if enabling:
                # Start work in the background. Never await a full queue fill
                # from this callback, so the rest of the UI stays responsive.
                monitor.start_autopilot(rid)
                notice = "▶️ Auto-Pilot enabled. Queue will refill automatically in the background."
            else:
                await monitor.stop_autopilot(rid)
                notice = "⏸️ Auto-Pilot paused."
            return await route_panel(q, rid, notice)
        if data.startswith("sched:"):
            rid = int(data.split(":")[1])
            PENDING[q.from_user.id] = {"action": "schedule", "route": rid}
            return await safe_edit(q, "⏰ TIMETABLE\n\nSend times separated by spaces.\nExample:\n08:00 11:00 14:00 17:00 20:00 23:00", kb([[('🔙 Route', f'route:{rid}')]]))
        if data.startswith("window:"):
            rid = int(data.split(":")[1])
            PENDING[q.from_user.id] = {"action": "window", "route": rid}
            return await safe_edit(q, "⏱ ROUTE WINDOW\n\nSend start and end time.\nExample: 08:00 23:00", kb([[('🔙 Route', f'route:{rid}')]]))
        if data.startswith("header:") or data.startswith("footer:"):
            kind = "header" if data.startswith("header:") else "footer"
            rid = int(data.split(":")[1])
            PENDING[q.from_user.id] = {"action": kind, "route": rid}
            return await safe_edit(q, f"📝 Send the {kind} text.", kb([[('🔙 Route', f'route:{rid}')]]))
        if data.startswith("fetchold:"):
            rid = int(data.split(":")[1])
            PENDING[q.from_user.id] = {"action": "fetch_link", "route": rid}
            return await safe_edit(q, "⏪ FETCH OLD\n\nSend a Telegram source post link.\nExample:\nhttps://t.me/sourcechannel/500", kb([[('🔙 Route', f'route:{rid}')]]))
        if data.startswith("queue:"):
            return await show_route_queue(q, int(data.split(":")[1]))
        if data.startswith("delroute:"):
            rid = int(data.split(":")[1])
            repo.delete_route(rid)
            return await show_routes(q)
        if data.startswith("pause_dest:"):
            did = int(data.split(":")[1])
            with repo.connect() as c:
                c.execute("UPDATE destinations SET paused=1-paused WHERE id=?", (did,))
            return await show_destinations(q)
    except Exception as exc:
        log("ui_callback", "failed", f"{data}: {exc}")
        return await safe_edit(q, f"❌ {exc}", kb([[('🔙 Dashboard', 'dashboard')]]))


async def show_routes(q):
    routes = repo.routes()
    lines = ["🔀 ROUTES", ""]
    for r in routes:
        lines += [
            f"{'🟢' if r['autopilot'] else '🔴'} {r['name']}",
            f"   📡 {len(repo.route_sources(r['id']))} Sources → 📢 {r['destination_title']}",
            f"   📥 Queue: {repo.route_queue_count(r['id'])}/40",
            "",
        ]
    if not routes:
        lines.append("No routes configured.")
    rows = [[(f"🔀 {r['name']}", f"route:{r['id']}")] for r in routes]
    rows += [[('➕ Create Route', 'newroute')], [('🔙 Dashboard', 'dashboard')]]
    return await safe_edit(q, "\n".join(lines), kb(rows))


async def route_panel(q, rid, notice=None):
    r = repo.route(rid)
    if not r:
        return await show_routes(q)
    lines = [
        f"⚙️ {r['name']}",
        "",
        f"📡 Sources: {len(repo.route_sources(rid))}",
        f"📢 Destination: {r['destination_title']}",
        f"📥 Queue: {repo.route_queue_count(rid)}/40",
        "🔄 Rotation: Round-Robin",
        f"🤖 Auto-Pilot: {'ON' if r['autopilot'] else 'OFF'}",
        f"⏱ Window: {r['start_time']} → {r['end_time']}",
    ]
    times = repo.route_schedule(rid)
    lines += ["", "⏰ Timetable: " + (", ".join(x['daily_time'] for x in times) if times else "Not set")]
    if notice:
        lines += ["", notice]
    rows = [
        [('📡 Manage Sources', f'rmanage:{rid}'), ('📢 Destination', f'destpick:{rid}')],
        [('⏰ Timetable', f'sched:{rid}'), ('⏱ Start/End', f'window:{rid}')],
        [('📝 Header', f'header:{rid}'), ('📝 Footer', f'footer:{rid}')],
        [('⏪ Fetch Old', f'fetchold:{rid}'), ('📥 View Queue', f'queue:{rid}')],
        [('⏸ Pause Auto-Pilot' if r['autopilot'] else '▶️ Start Auto-Pilot', f'autopilot:{rid}')],
        [('🗑 Delete Route', f'delroute:{rid}')],
        [('🔙 Routes', 'routes')],
    ]
    return await safe_edit(q, "\n".join(lines), kb(rows))


async def route_sources_panel(q, rid):
    sources = repo.route_sources(rid)
    lines = ['📡 ROUTE SOURCES', '']
    if sources:
        lines += [f"{i}. {s['title']} ({s['chat_id']})" for i, s in enumerate(sources, 1)]
    else:
        lines.append('No sources configured.')
    rows = [[(f'➖ {s["title"]}', f'rremove:{rid}:{s["id"]}')] for s in sources]
    rows += [[('➕ Add / Select Sources', f'radd:{rid}')], [('🔙 Route', f'route:{rid}')]]
    return await safe_edit(q, '\n'.join(lines), kb(rows))


async def source_picker(q, rid):
    state = PENDING.get(q.from_user.id)
    selected = state.get('selected', set()) if state else {int(s['id']) for s in repo.route_sources(rid)}
    rows = []
    for s in repo.sources():
        mark = '✅' if int(s['id']) in selected else '⬜'
        rows.append([(f'{mark} {s["title"]}', f'rtoggle:{rid}:{s["id"]}')])
    if not rows:
        rows = [[('ℹ️ Add a source first', 'sources')]]
    rows.append([('✅ Done', f'radddone:{rid}')])
    rows.append([('🔙 Back', f'rmanage:{rid}')])
    return await safe_edit(q, '📡 SELECT ROUTE SOURCES\n\nToggle the sources that belong to this route.', kb(rows))


async def destination_picker(q, rid):
    rows = [[(f'📢 {d["title"]}', f'setdest:{rid}:{d["id"]}')] for d in repo.destinations()]
    rows.append([('🔙 Route', f'route:{rid}')])
    return await safe_edit(q, '📢 SELECT DESTINATION', kb(rows))


async def show_sources(q):
    rows = repo.sources()
    text = '📡 SOURCES\n\n' + (
        '\n'.join(
            f"{'🟢' if s['enabled'] else '🔴'} #{s['id']} — {s['title']} ({s['chat_id']})"
            for s in rows
        )
        if rows else 'No sources configured.'
    )
    return await safe_edit(q, text, kb([[('➕ Add Source', 'newsource')], [('🔄 Refresh Telegram Access', 'refresh_entities')], [('🔙 Dashboard', 'dashboard')]]))


async def show_destinations(q):
    rows = repo.destinations()
    text = '📢 DESTINATIONS\n\n' + (
        '\n'.join(
            f"{'⏸️' if d['paused'] else ('🟢' if d['enabled'] else '🔴')} #{d['id']} — {d['title']} ({d['chat_id']})"
            for d in rows
        )
        if rows else 'No destinations configured.'
    )
    buttons = [[(f"{'▶️ Resume' if d['paused'] else '⏸️ Pause'} {d['title']}", f'pause_dest:{d["id"]}')] for d in rows]
    buttons += [[('➕ Add Destination', 'newdest')], [('🔄 Refresh Telegram Access', 'refresh_entities')], [('🔙 Dashboard', 'dashboard')]]
    return await safe_edit(q, text, kb(buttons))


async def show_queues(q):
    rows = repo.routes()
    text = '📥 QUEUES\n\n' + ('\n'.join(f"{r['name']}: {repo.route_queue_count(r['id'])}/40" for r in rows) if rows else 'No routes configured.')
    buttons = [[(f'🔀 {r["name"]}', f'queue:{r["id"]}')] for r in rows] + [[('🔙 Dashboard', 'dashboard')]]
    return await safe_edit(q, text, kb(buttons))


async def show_route_queue(q, rid):
    posts = repo.queue_posts(rid, 20)
    lines = ['📥 ROUTE QUEUE', '']
    if posts:
        lines += [f"{i}. {p['source_title']} — #{p['source_message_id']}" for i, p in enumerate(posts, 1)]
    else:
        lines.append('Queue empty.')
    return await safe_edit(q, '\n'.join(lines), kb([[('🔄 Refresh', f'queue:{rid}')], [('🔙 Route', f'route:{rid}')]]))


async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner(update):
        return
    uid = update.effective_user.id
    state = PENDING.get(uid)
    if not state:
        return
    value = (update.message.text or '').strip()
    action = state['action']

    try:
        if action in ('new_source_manual', 'new_destination_manual'):
            parts = value.split('|', 1)
            ref = parts[0].strip()
            title = parts[1].strip() if len(parts) == 2 else ''
            if not ref:
                raise ValueError('Telegram ID, @username or t.me link is required.')
            telegram = context.application.bot_data['telegram_service']
            resolved = await telegram.resolve(ref)
            if resolved.peer_type not in {'channel', 'chat'}:
                raise ValueError('Use a Telegram group, supergroup or channel, not a user account.')
            title = title or resolved.title
            if action == 'new_source_manual':
                repo.add_source(resolved.marked_id, title, resolved.username, resolved.peer_type, resolved.entity_id, resolved.access_hash)
                msg = f'✅ Source saved.\n{resolved.title}\n{resolved.marked_id}'
                back = 'sources'
            else:
                repo.add_destination(resolved.marked_id, title, resolved.username, resolved.peer_type, resolved.entity_id, resolved.access_hash)
                msg = f'✅ Destination saved.\n{resolved.title}\n{resolved.marked_id}'
                back = 'destinations'
            await update.message.reply_text(msg, reply_markup=kb([[('🔙 Back', back)]]))

        elif action == 'route_name':
            if not value:
                raise ValueError('Route name cannot be empty.')
            PENDING[uid] = {'action': 'route_destination', 'name': value}
            buttons = [[(f'📢 {d["title"]}', f'newroute_dest:{d["id"]}')] for d in repo.destinations()]
            buttons.append([('❌ Cancel', 'routes')])
            await update.message.reply_text('📢 SELECT DESTINATION\n\nChoose the configured destination for this route.', reply_markup=kb(buttons))
            return

        elif action == 'schedule':
            rid = state['route']
            times = value.split()
            if not times:
                raise ValueError('Enter at least one time.')
            repo.clear_schedules(rid)
            for t in times:
                if not repo.add_schedule(rid, t):
                    raise ValueError(f'Invalid time: {t}')
            await update.message.reply_text('✅ Timetable saved.', reply_markup=kb([[('⚙️ Route', f'route:{rid}')]]))

        elif action == 'window':
            rid = state['route']
            parts = value.split()
            if len(parts) != 2:
                raise ValueError('Use: HH:MM HH:MM')
            repo.set_route_window(rid, parts[0], parts[1])
            await update.message.reply_text('✅ Route window saved.', reply_markup=kb([[('⚙️ Route', f'route:{rid}')]]))

        elif action in ('header', 'footer'):
            rid = state['route']
            repo.set_route_text(rid, action, value, True)
            await update.message.reply_text(f'✅ {action.title()} saved.', reply_markup=kb([[('⚙️ Route', f'route:{rid}')]]))

        elif action == 'fetch_link':
            PENDING[uid] = {'action': 'fetch_count', 'route': state['route'], 'link': value}
            await update.message.reply_text('🔢 Optional number of posts. Send a number, or send 0 for linked post → latest.')
            return

        elif action == 'fetch_count':
            count = int(value)
            monitor = context.application.bot_data['source_monitor']
            telegram = context.application.bot_data['telegram_service']
            from core.fetch_old import fetch_old_posts
            queued = await fetch_old_posts(uid, state['route'], state['link'], None if count <= 0 else count, telegram, monitor)
            await update.message.reply_text(f'✅ Fetch Old complete.\nQueued: {queued}', reply_markup=main_kb())

    except Exception as exc:
        log('ui_input', 'failed', str(exc))
        await update.message.reply_text(f'❌ {exc}')
    finally:
        if action not in ('route_name', 'fetch_link'):
            PENDING.pop(uid, None)


async def dialog_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != settings.owner_id:
        return
    try:
        await q.answer()
    except NetworkError:
        pass
    data = q.data or ''
    if data.startswith('dialogs:'):
        kind = data.split(':', 1)[1]
        try:
            dialogs = await context.application.bot_data['telegram_service'].dialogs(limit=60)
            DIALOG_CACHE[q.from_user.id] = [(kind, resolved) for _, resolved in dialogs]
            rows = []
            for index, (stored_kind, resolved) in enumerate(DIALOG_CACHE[q.from_user.id]):
                if stored_kind != kind or resolved.peer_type not in {'channel', 'chat'}:
                    continue
                rows.append([(f'📢 {resolved.title}', f'dialogpick:{kind}:{index}')])
            rows = rows[:45]
            rows.append([('🔙 Back', 'sources' if kind == 'source' else 'destinations')])
            return await safe_edit(q, '📋 SELECT TELEGRAM CHAT', kb(rows))
        except Exception as exc:
            return await safe_edit(q, f'❌ Could not load Telegram dialogs: {exc}', kb([[('🔙 Back', 'dashboard')]]))

    if data.startswith('dialogpick:'):
        _, kind, index = data.split(':')
        cache = DIALOG_CACHE.get(q.from_user.id, [])
        index = int(index)
        if index < 0 or index >= len(cache):
            return await safe_edit(q, '❌ Dialog selection expired. Open Select from Telegram again.', kb([[('🔙 Dashboard', 'dashboard')]]))
        stored_kind, resolved = cache[index]
        if stored_kind != kind:
            return await safe_edit(q, '❌ Invalid dialog selection.', kb([[('🔙 Dashboard', 'dashboard')]]))
        if kind == 'source':
            repo.add_source(resolved.marked_id, resolved.title, resolved.username, resolved.peer_type, resolved.entity_id, resolved.access_hash)
            PENDING.pop(q.from_user.id, None)
            return await safe_edit(q, f'✅ Source saved.\n{resolved.title}\n{resolved.marked_id}', kb([[('🔙 Sources', 'sources')]]))
        repo.add_destination(resolved.marked_id, resolved.title, resolved.username, resolved.peer_type, resolved.entity_id, resolved.access_hash)
        PENDING.pop(q.from_user.id, None)
        return await safe_edit(q, f'✅ Destination saved.\n{resolved.title}\n{resolved.marked_id}', kb([[('🔙 Destinations', 'destinations')]]))


def build_application(telegram, source_monitor):
    app = Application.builder().token(settings.bot_token).build()
    app.bot_data['telegram_service'] = telegram
    app.bot_data['source_monitor'] = source_monitor
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_cmd))
    app.add_handler(CallbackQueryHandler(dialog_callback, pattern=r'^(dialogs:|dialogpick:)'))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    return app
