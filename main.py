"""AnimeHUB | Dream: Bleach navigation INSIDE the channel (Bot API only).

The published hub and two section menus use URL inline buttons pointing to
messages IN THE SAME CHANNEL. They never use callbacks that edit a public
message for everyone and never send subscribers into a bot's private chat.
"""

import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes


ROOT = Path(__file__).resolve().parent
CATALOG_FILE = ROOT / "catalog.json"
STATE_FILE = Path(os.getenv("STATE_FILE", str(ROOT / "state.json")))
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан. Введи Bot Token в настройках BotHost.")

OWNER_RAW = os.getenv("OWNER_USER_ID", "").strip()
if OWNER_RAW and not OWNER_RAW.isdigit():
    raise RuntimeError("OWNER_USER_ID должен быть числом")
OWNER_ID = int(OWNER_RAW) if OWNER_RAW else None
CHANNEL_RAW = os.getenv("BLEACH_CHANNEL_ID", "").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("animehub.bleach")


def read_dict(path):
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# Built-in copy prevents startup failures if catalog.json was accidentally omitted.
# The catalog.json file takes precedence and remains the editable source.
DEFAULT_CATALOG = json.loads('{\n  "title": "⚔️ БЛИЧ — НАВИГАЦИЯ",\n  "invite_link": "https://t.me/+gnBXocT0HL40YTRi",\n  "sections": [\n    {\n      "id": "original",\n      "title": "⚔️ Блич — оригинальный сериал",\n      "button": "⚔️ Блич (1–366)",\n      "description": "Выберите сюжетную арку. Здесь представлены только арки, опубликованные в канале. Пропуски в нумерации не являются ошибкой кнопок.",\n      "items": [\n        {"id": "substitute", "name": "Временный синигами", "episodes": "1–20", "url": "https://t.me/c/3593534577/23"},\n        {"id": "soul_entry", "name": "Сообщество душ: Проникновение", "episodes": "21–41", "url": "https://t.me/c/3593534577/34"},\n        {"id": "soul_rescue", "name": "Сообщество душ: Спасение", "episodes": "42–63", "url": "https://t.me/c/3593534577/77"},\n        {"id": "arrancar", "name": "Арранкары", "episodes": "110–127", "url": "https://t.me/c/3593534577/96"},\n        {"id": "hueco", "name": "Уэко Мундо", "episodes": "138–167", "url": "https://t.me/c/3593534577/127"},\n        {"id": "arrancar_vs", "name": "Арранкары против синигами", "episodes": "190–203", "url": "https://t.me/c/3593534577/142"},\n        {"id": "past", "name": "Прошлое", "episodes": "206–212", "url": "https://t.me/c/3593534577/150"},\n        {"id": "karakura", "name": "Арранкары: Фальшивый город Каракура", "episodes": "215–228", "url": "https://t.me/c/3593534577/176"},\n        {"id": "downfall", "name": "Арранкары: Разрушение", "episodes": "266–310", "url": "https://t.me/c/3593534577/217"},\n        {"id": "lost_agent", "name": "Потерянное удостоверение синигами", "episodes": "343–366", "url": "https://t.me/c/3593534577/241"}\n      ]\n    },\n    {\n      "id": "tybw",\n      "title": "🔥 Блич: Тысячелетняя кровавая война",\n      "button": "🔥 Тысячелетняя кровавая война",\n      "description": "Выберите часть истории или вариант озвучки. Диапазоны серий сохранены в том виде, в котором ты указал их для постов канала.",\n      "items": [\n        {"id": "tybw_amber", "name": "Кровавая война — Amber", "episodes": "366–379", "url": "https://t.me/c/3593534577/255"},\n        {"id": "tybw_deep", "name": "Кровавая война — DEEP", "episodes": "366–379", "url": "https://t.me/c/3593534577/269"},\n        {"id": "tybw_separation", "name": "Прощание — DEEP", "episodes": "380–392", "url": "https://t.me/c/3593534577/284"},\n        {"id": "tybw_conflict", "name": "Конфликт", "episodes": "393–405", "url": "https://t.me/c/3593534577/299"}\n      ]\n    }\n  ]\n}\n')
CATALOG = read_dict(CATALOG_FILE) or DEFAULT_CATALOG
if not CATALOG.get("sections"):
    raise RuntimeError("Каталог не содержит разделов")
SECTIONS = {section["id"]: section for section in CATALOG["sections"]}
ITEMS = {item["id"]: item for section in CATALOG["sections"] for item in section["items"]}
if len(SECTIONS) != 2 or set(SECTIONS) != {"original", "tybw"}:
    raise RuntimeError("В catalog.json должны быть разделы original и tybw")
if len(ITEMS) != sum(len(s["items"]) for s in CATALOG["sections"]):
    raise RuntimeError("Найдены повторяющиеся ID арок")
STATE = read_dict(STATE_FILE)


def save_state():
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(STATE, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError as exc:
        logger.warning("Не удалось сохранить state.json: %s", exc)


def active_channel():
    val = STATE.get("channel_id") or CHANNEL_RAW
    if isinstance(val, str) and re.fullmatch(r"-?\d+", val):
        return int(val)
    return val or None


def nav_ids():
    return STATE.get("nav", {})


def telegram_url(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in {"t.me", "telegram.me"} and bool(parsed.path.strip("/"))


def item_link(item):
    return STATE.get("links", {}).get(item["id"], item.get("url", ""))


def channel_post_url(channel_id, message_id):
    """Return a direct Telegram URL into this channel; works for members of private channels."""
    if not isinstance(channel_id, int) or channel_id >= 0:
        raise ValueError("Нужен числовой ID Telegram-канала, например -1003593534577")
    prefix = str(channel_id)
    if not prefix.startswith("-100"):
        raise ValueError("Неверный формат ID канала: нужен ID с префиксом -100")
    if int(message_id) < 1:
        raise ValueError("ID сообщения должен быть положительным")
    return f"https://t.me/c/{prefix[4:]}/{int(message_id)}"


HUB_TEXT = (
    "⚔️ БЛИЧ — НАВИГАЦИЯ\n\n"
    "Выберите нужный раздел. Все кнопки открывают сообщения прямо в этом канале — "
    "переходить в чат с ботом не нужно.\n\n"
    "📌 В меню указаны только опубликованные арки."
)


def hub_keyboard(channel_id, section_ids):
    rows = [
        [InlineKeyboardButton(SECTIONS[sid]["button"], url=channel_post_url(channel_id, section_ids[sid]))]
        for sid in ("original", "tybw")
    ]
    return InlineKeyboardMarkup(rows)


def section_text(section_id):
    section = SECTIONS[section_id]
    return f"{section['title']}\n\n{section['description']}\n\n⬇️ Выберите арку:"


def section_keyboard(section_id, channel_id=None, hub_id=None):
    section = SECTIONS[section_id]
    rows = []
    for item in section["items"]:
        text = f"▫️ {item['name']} ({item['episodes']})"
        url = item_link(item)
        if telegram_url(url):
            rows.append([InlineKeyboardButton(text, url=url)])
        else:
            # Never silently send visitors into a private bot chat.
            rows.append([InlineKeyboardButton(f"{text} — нет ссылки", callback_data="missing")])
    if channel_id and hub_id:
        rows.append([InlineKeyboardButton("⬅️ К основному меню", url=channel_post_url(channel_id, hub_id))])
    return InlineKeyboardMarkup(rows)


async def require_owner(update):
    if OWNER_ID is not None and update.effective_user and update.effective_user.id == OWNER_ID and update.effective_chat and update.effective_chat.type == "private":
        return True
    if update.effective_message:
        await update.effective_message.reply_text(
            "⛔ Только владелец в личном чате может управлять меню. "
            "Проверь OWNER_USER_ID в BotHost."
        )
    return False


async def get_channel(context):
    dest = active_channel()
    if not dest:
        return None
    chat = await context.bot.get_chat(dest)
    me = await context.bot.get_me()
    member = await context.bot.get_chat_member(chat.id, me.id)
    if chat.type != "channel" or member.status not in ("administrator", "creator"):
        raise ValueError("Бот не администратор канала.")
    if member.status == "administrator" and not getattr(member, "can_post_messages", False):
        raise ValueError("Боту требуется право «Публикация сообщений».")
    if not str(chat.id).startswith("-100"):
        raise ValueError("Не удалось сформировать ссылки на сообщения канала.")
    return chat


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != "private":
        return
    await update.message.reply_text(
        "⚔️ БЛИЧ — НАВИГАЦИЯ\n\n"
        "Кнопки для зрителей размещаются прямо в канале «Блич». "
        "Открой закреплённое сообщение навигации в канале — личный чат с ботом не нужен.\n\n"
        "Владелец: /help для управляющих команд."
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.effective_user:
        await update.message.reply_text(f"Твой Telegram ID: {update.effective_user.id}")


async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    candidate = None
    if context.args:
        value = context.args[0]
        if re.fullmatch(r"-100\d+", value):
            candidate = int(value)
        elif value.startswith("@"):
            candidate = value
    elif update.message.reply_to_message:
        origin = update.message.reply_to_message.forward_origin
        if origin and getattr(origin, "type", None) == "channel":
            candidate = origin.chat.id
    if candidate is None:
        await update.message.reply_text("Пример: /set_channel -1003593534577")
        return
    try:
        chat = await context.bot.get_chat(candidate)
        me = await context.bot.get_me()
        member = await context.bot.get_chat_member(chat.id, me.id)
        if chat.type != "channel" or member.status not in ("administrator", "creator") or (member.status == "administrator" and not member.can_post_messages):
            raise ValueError("Назначь бота администратором канала с правом публикации")
    except (TelegramError, ValueError) as exc:
        await update.message.reply_text(f"❌ Канал недоступен: {exc}")
        return
    if STATE.get("channel_id") != chat.id:
        STATE.pop("nav", None)
    STATE["channel_id"] = chat.id
    STATE["channel_title"] = chat.title
    save_state()
    await update.message.reply_text(f"✅ Канал «{chat.title}» привязан. ID: {chat.id}. Проверка: /status")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    try:
        chat = await get_channel(context)
        channel_status = f"✅ {chat.title} ({chat.id})" if chat else "❌ Канал не выбран"
    except (TelegramError, ValueError) as exc:
        channel_status = f"❌ {exc}"
    ids = nav_ids()
    await update.message.reply_text(
        f"⚙️ BLEACH NAVIGATION — ВНУТРИ КАНАЛА\n"
        f"Канал: {channel_status}\n"
        f"Владелец: {'задан' if OWNER_ID else 'НЕ ЗАДАН'}\n"
        f"Арки: {len(ITEMS)}\n"
        f"ID: главное меню {ids.get('hub', 'нет')}, "
        f"оригинал {ids.get('original', 'нет')}, TYBW {ids.get('tybw', 'нет')}\n\n"
        "Навигация работает по URL: зрители остаются в Telegram-канале."
    )


async def preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    await update.message.reply_text("👁 ПРЕДПРОСМОТР ДВУХ РАЗДЕЛОВ (только тебе):")
    for sid in ("original", "tybw"):
        await update.message.reply_text(section_text(sid), reply_markup=section_keyboard(sid), disable_web_page_preview=True)
    await update.message.reply_text(
        "Главный пост будет содержать 2 кнопки, ведущие на эти разделы уже в канале. "
        "Чтобы подготовить публикацию: /post_nav"
    )


async def post_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    try:
        chat = await get_channel(context)
    except (TelegramError, ValueError) as exc:
        await update.message.reply_text(f"❌ Не удалось проверить канал: {exc}")
        return
    if chat is None:
        await update.message.reply_text("Сначала настрой BLEACH_CHANNEL_ID или используй /set_channel -100...")
        return
    context.user_data["pending"] = chat.id
    await update.message.reply_text(
        f"📣 Готово к публикации в «{chat.title}» (ID {chat.id}).\n"
        "Будут опубликованы 3 сообщения: 2 раздела с арками и 1 главное меню.\n"
        "Все кнопки — ссылки на сообщения внутри канала.\n\n"
        "Подтвердить: /confirm_nav\nОтменить: /cancel_nav\nПредпросмотр: /preview"
    )


async def cancel_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    context.user_data.pop("pending", None)
    await update.message.reply_text("Отменено. Ничего не опубликовано.")


async def confirm_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    pending = context.user_data.get("pending")
    if pending is None:
        await update.message.reply_text("Сначала /post_nav, потом /confirm_nav")
        return
    try:
        chat = await get_channel(context)
        if chat is None or chat.id != pending:
            await update.message.reply_text("Канал изменился. Повтори /post_nav.")
            return
    except (TelegramError, ValueError) as exc:
        await update.message.reply_text(f"❌ {exc}")
        return

    new_messages = []
    section_ids = {}
    try:
        # Create BOTH section posts first, then the hub: all links are known.
        for sid in ("original", "tybw"):
            msg = await context.bot.send_message(
                chat_id=chat.id,
                text=section_text(sid),
                reply_markup=section_keyboard(sid),
                disable_web_page_preview=True,
            )
            section_ids[sid] = msg.message_id
            new_messages.append(msg.message_id)

        hub = await context.bot.send_message(
            chat_id=chat.id,
            text=HUB_TEXT,
            reply_markup=hub_keyboard(chat.id, section_ids),
            disable_web_page_preview=True,
        )
        new_messages.append(hub.message_id)
    except TelegramError as exc:
        # Cleanup partial publish so users don't see half-completed navigation.
        failed = []
        for mid in new_messages:
            try:
                await context.bot.delete_message(chat_id=chat.id, message_id=mid)
            except TelegramError:
                failed.append(mid)
        extra = f"\nНе удалось удалить сообщения: {failed}" if failed else ""
        await update.message.reply_text(f"❌ Ошибка публикации: {exc}{extra}")
        return

    # At this point navigation is usable even if the optional 'back' links fail.
    STATE["channel_id"] = chat.id
    STATE["channel_title"] = chat.title
    STATE["nav"] = {"hub": hub.message_id, **section_ids}
    save_state()
    context.user_data.pop("pending", None)

    failed_back = []
    for sid, msg_id in section_ids.items():
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=chat.id,
                message_id=msg_id,
                reply_markup=section_keyboard(sid, chat.id, hub.message_id),
            )
        except TelegramError as exc:
            logger.warning("Не удалось добавить кнопку возврата: %s", exc)
            failed_back.append(sid)

    await update.message.reply_text(
        f"✅ Опубликованы 3 сообщения в «{chat.title}».\n"
        f"Главное меню: {channel_post_url(chat.id, hub.message_id)}\n"
        f"Блич (1–366): {channel_post_url(chat.id, section_ids['original'])}\n"
        f"Тысячелетняя война: {channel_post_url(chat.id, section_ids['tybw'])}\n\n"
        f"Закрепить главное меню: /pin_nav\n"
        f"{('⚠️ Обнови ссылки возврата через /update_nav: '+', '.join(failed_back)) if failed_back else ''}\n"
        "Сохрани эти три ID: BotHost может сбросить state.json после пересборки."
    )


async def set_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restore menu IDs if BotHost wiped state.json after rebuilding."""
    if not await require_owner(update):
        return
    if len(context.args) != 3 or not all(x.isdigit() and int(x) > 0 for x in context.args):
        await update.message.reply_text("Формат: /set_nav ID_главного ID_оригинала ID_TYBW\nПример: /set_nav 320 318 319")
        return
    hub_id, orig_id, tybw_id = map(int, context.args)
    STATE["nav"] = {"hub": hub_id, "original": orig_id, "tybw": tybw_id}
    save_state()
    await update.message.reply_text("✅ ID всех трёх постов восстановлены. Теперь можно /update_nav и /pin_nav.")


async def update_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    ids = nav_ids()
    if not all(ids.get(key) for key in ("hub", "original", "tybw")):
        await update.message.reply_text("Нет всех трёх ID. Используй /set_nav или /post_nav → /confirm_nav.")
        return
    try:
        chat = await get_channel(context)
        if chat is None:
            await update.message.reply_text("Канал не привязан.")
            return
    except (TelegramError, ValueError) as exc:
        await update.message.reply_text(f"❌ {exc}")
        return
    errors = []
    for sid in ("original", "tybw"):
        try:
            await context.bot.edit_message_text(
                chat_id=chat.id,
                message_id=ids[sid],
                text=section_text(sid),
                reply_markup=section_keyboard(sid, chat.id, ids["hub"]),
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                errors.append(f"{sid}: {exc}")
        except TelegramError as exc:
            errors.append(f"{sid}: {exc}")
    try:
        await context.bot.edit_message_text(
            chat_id=chat.id,
            message_id=ids["hub"],
            text=HUB_TEXT,
            reply_markup=hub_keyboard(chat.id, ids),
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            errors.append(f"главное меню: {exc}")
    except TelegramError as exc:
        errors.append(f"главное меню: {exc}")

    if errors:
        await update.message.reply_text("⚠️ Часть меню не обновилась:\n" + "\n".join(errors))
    else:
        await update.message.reply_text("✅ Все 3 сообщения навигации обновлены прямо в канале.")


async def pin_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    ids = nav_ids()
    if not ids.get("hub"):
        await update.message.reply_text("Сначала /post_nav → /confirm_nav или /set_nav ...")
        return
    try:
        chat = await get_channel(context)
        if chat is None:
            raise ValueError("Канал не выбран")
        await context.bot.pin_chat_message(
            chat_id=chat.id,
            message_id=ids["hub"],
            disable_notification=True,
        )
    except (TelegramError, ValueError) as exc:
        await update.message.reply_text(f"❌ Не удалось закрепить главное меню: {exc}")
        return
    await update.message.reply_text("📌 Главное меню закреплено!")


async def links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    parts = ["🔗 Ссылки на арки:"]
    for s in CATALOG["sections"]:
        parts.append(f"\n{s['title']}")
        for item in s["items"]:
            parts.append(f"• {item['id']}: {item_link(item)}")
    await update.message.reply_text("\n".join(parts), disable_web_page_preview=True)


async def setlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    if len(context.args) != 2:
        await update.message.reply_text("Формат: /setlink ID https://t.me/c/3593534577/23\nСписок ID: /links")
        return
    item_id, url = context.args
    if item_id not in ITEMS or not telegram_url(url):
        await update.message.reply_text("❌ Неверный ID арки или URL. Смотри /links")
        return
    STATE.setdefault("links", {})[item_id] = url
    save_state()
    await update.message.reply_text(
        f"✅ Ссылка для «{ITEMS[item_id]['name']}» сохранена.\n"
        "Чтобы она обновилась В КАНАЛЕ, отправь /update_nav.\n"
        "Чтобы не потерять изменение после пересборки, исправь эту ссылку также в catalog.json на GitHub."
    )


async def missing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer("Ссылка на арку пока не добавлена.", show_alert=True)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(
        "⚔️ БЛИЧ — КНОПКИ ВНУТРИ КАНАЛА\n\n"
        "/start — информация\n/myid — Telegram ID\n\n"
        "🔐 Команды владельца:\n"
        "/status — проверить канал и ID трёх меню\n"
        "/set_channel -1003593534577 — привязать канал\n"
        "/preview — увидеть кнопки арок\n"
        "/post_nav — подготовить три поста меню\n"
        "/confirm_nav — подтвердить публикацию\n"
        "/cancel_nav — отменить\n"
        "/pin_nav — закрепить главное меню\n"
        "/update_nav — обновить все три поста\n"
        "/set_nav ГЛАВНОЕ ОРИГИНАЛ TYBW — восстановить ID после пересборки\n"
        "/links — список ссылок\n"
        "/setlink ID URL — изменить ссылку арки\n\n"
        "Зрители видят меню прямо в канале и не переходят к боту."
    )


def run():
    app = Application.builder().token(BOT_TOKEN).build()
    for cmd in ("start", "bleach", "menu"):
        app.add_handler(CommandHandler(cmd, start))
    for name, handler in (
        ("myid", myid), ("help", help_command),
        ("set_channel", set_channel), ("status", status),
        ("preview", preview), ("post_nav", post_nav),
        ("confirm_nav", confirm_nav), ("cancel_nav", cancel_nav),
        ("set_nav", set_nav), ("update_nav", update_nav),
        ("pin_nav", pin_nav), ("links", links), ("setlink", setlink),
    ):
        app.add_handler(CommandHandler(name, handler))
    app.add_handler(CallbackQueryHandler(missing_callback))
    logger.info("Bleach bot starting: %d arcs; URL navigation stays in channel", len(ITEMS))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run()
