"""AnimeHUB | Dream — Bleach navigation bot (DragonBall-style).

Uses Bot API only. No Telegram API ID / API HASH / Premium session needed.
For a channel post, buttons deep-link into a PRIVATE chat with the bot so
subscribers cannot change the navigation message for everybody else.
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


TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не найден. Укажи Bot Token при создании бота в BotHost.")

OWNER_RAW = os.getenv("OWNER_USER_ID", "").strip()
OWNER_USER_ID = int(OWNER_RAW) if OWNER_RAW else None

# After finding the channel ID, put BLEACH_CHANNEL_ID into BotHost variables.
DEFAULT_CHANNEL_ID = os.getenv("BLEACH_CHANNEL_ID", "").strip()
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
CATALOG_FILE = Path(__file__).resolve().with_name("catalog.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("animehub.bleach")


def load_json(path: Path, default: dict) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else default
    except (OSError, json.JSONDecodeError):
        return default


CATALOG = load_json(CATALOG_FILE, {})
if not CATALOG.get("sections"):
    raise RuntimeError("Не найден или пустой catalog.json")

SECTIONS = {s["id"]: s for s in CATALOG["sections"]}
ITEMS = {item["id"]: item for section in CATALOG["sections"] for item in section["items"]}
if len(ITEMS) != sum(len(s["items"]) for s in CATALOG["sections"]):
    raise RuntimeError("Повторяющиеся ID арок в catalog.json")

STATE = load_json(STATE_FILE, {})


def save_state() -> None:
    """Store bot preferences. Files may not survive a BotHost redeployment."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
        temp_file.write_text(json.dumps(STATE, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_file.replace(STATE_FILE)
    except OSError as exc:
        log.warning("Не удалось сохранить state.json: %s", exc)


def active_channel():
    """Saved channel ID wins over configured fallback. May be number or @username."""
    value = STATE.get("channel_id") or DEFAULT_CHANNEL_ID
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return value or None


def link_for(item: dict) -> str:
    return STATE.get("links", {}).get(item["id"], item.get("url", ""))


def telegram_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and parsed.hostname in {"t.me", "telegram.me"} and bool(parsed.path.strip("/"))


def is_owner(update: Update) -> bool:
    return bool(OWNER_USER_ID is not None and update.effective_user and update.effective_user.id == OWNER_USER_ID)


async def require_owner(update: Update) -> bool:
    if is_owner(update) and update.effective_chat and update.effective_chat.type == "private":
        return True
    if update.effective_message:
        message = (
            "⛔ Команда доступна только владельцу в личном чате с ботом."
            if OWNER_USER_ID is not None else
            "⛔ Сначала укажи OWNER_USER_ID в переменных BotHost. Узнать его можно командой /myid."
        )
        await update.effective_message.reply_text(message)
    return False


MAIN_TEXT = (
    "⚔️ БЛИЧ — НАВИГАЦИЯ\n\n"
    "Выберите часть истории.\n"
    "Каждая кнопка арки ведёт на соответствующий пост с сериями в нашем канале."
)


def private_main_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(section["button"], callback_data=f"section:{section['id']}")]
        for section in CATALOG["sections"]
    ]
    rows.append([InlineKeyboardButton("📢 Канал «Блич»", url=CATALOG["invite_link"])])
    return InlineKeyboardMarkup(rows)


def section_keyboard(section_id: str) -> InlineKeyboardMarkup:
    section = SECTIONS[section_id]
    rows = []
    for item in section["items"]:
        label = f"▫️ {item['name']} ({item['episodes']})"
        link = link_for(item)
        if telegram_url(link):
            rows.append([InlineKeyboardButton(label, url=link)])
        else:
            rows.append([InlineKeyboardButton(f"{label} — нет ссылки", callback_data="noop")])
    rows.append([InlineKeyboardButton("⬅️ Назад к частям", callback_data="back:main")])
    return InlineKeyboardMarkup(rows)


def section_text(section_id: str) -> str:
    section = SECTIONS[section_id]
    return f"{section['title']}\n\n{section['description']}"


def channel_nav_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    """Channel buttons are URL deep-links, not callbacks that edit shared channel post."""
    rows = [
        [InlineKeyboardButton(s["button"], url=f"https://t.me/{bot_username}?start={s['id']}")]
        for s in CATALOG["sections"]
    ]
    rows.append([InlineKeyboardButton("📢 Канал «Блич»", url=CATALOG["invite_link"])])
    return InlineKeyboardMarkup(rows)


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message and update.effective_user:
        await update.effective_message.reply_text(
            f"Твой Telegram ID: <code>{update.effective_user.id}</code>\n"
            "Добавь его в BotHost как OWNER_USER_ID.",
            parse_mode="HTML",
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("Открой меню в личном чате с ботом.")
        return
    target = context.args[0] if context.args else ""
    if target in SECTIONS:
        await update.message.reply_text(section_text(target), reply_markup=section_keyboard(target))
    else:
        await update.message.reply_text(MAIN_TEXT, reply_markup=private_main_keyboard())


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    if not query.message or query.message.chat.type != "private":
        await query.answer("Перейди в личный чат с ботом для навигации.", show_alert=True)
        return
    await query.answer()
    data = query.data or ""
    if data == "noop":
        return
    if data == "back:main":
        text, keyboard = MAIN_TEXT, private_main_keyboard()
    elif data.startswith("section:") and data[8:] in SECTIONS:
        target = data[8:]
        text, keyboard = section_text(target), section_keyboard(target)
    else:
        text, keyboard = MAIN_TEXT, private_main_keyboard()
    try:
        await query.edit_message_text(text=text, reply_markup=keyboard)
    except BadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise


async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    message = update.message
    if not message:
        return

    candidate = None
    if context.args:
        raw = context.args[0].strip()
        if re.fullmatch(r"-\d+", raw):
            candidate = int(raw)
        elif raw.startswith("@"):
            candidate = raw
        else:
            await message.reply_text("Используй ID вида -1003593534577 или публичный @username.")
            return
    elif message.reply_to_message:
        origin = message.reply_to_message.forward_origin
        if origin and getattr(origin, "type", "") == "channel":
            candidate = origin.chat.id

    if candidate is None:
        await message.reply_text(
            "Укажи ID канала. Для ссылок t.me/c/3593534577/... предположительный ID:\n"
            "<code>/set_channel -1003593534577</code>\n\n"
            "Перед публикацией бот покажет настоящее название найденного канала.",
            parse_mode="HTML",
        )
        return
    try:
        chat = await context.bot.get_chat(candidate)
        me = await context.bot.get_me()
        member = await context.bot.get_chat_member(chat.id, me.id)
        if chat.type != "channel" or member.status != "administrator":
            await message.reply_text("Бот должен быть администратором именно канала, а не группы.")
            return
        if not getattr(member, "can_post_messages", False):
            await message.reply_text("Выдай боту право «Публикация сообщений» в этом канале.")
            return
    except TelegramError as exc:
        await message.reply_text(f"❌ Канал недоступен: {exc}\nПроверь права администратора и ID.")
        return

    if STATE.get("channel_id") != chat.id:
        STATE.pop("last_nav_message_id", None)
    STATE["channel_id"] = chat.id
    STATE["channel_title"] = chat.title
    save_state()
    await message.reply_text(
        f"✅ Привязан канал: {chat.title}\nID: <code>{chat.id}</code>\n\n"
        "Теперь используй /post_nav для предварительного просмотра.\n"
        "Сохрани этот ID также в BotHost как BLEACH_CHANNEL_ID: настройки файла state.json могут сброситься при пересборке.",
        parse_mode="HTML",
    )


async def channel_info(context: ContextTypes.DEFAULT_TYPE):
    """Validate destination and ability to post. Returns Chat or raises TelegramError."""
    destination = active_channel()
    if not destination:
        return None
    chat = await context.bot.get_chat(destination)
    me = await context.bot.get_me()
    member = await context.bot.get_chat_member(chat.id, me.id)
    if chat.type != "channel" or member.status != "administrator" or not getattr(member, "can_post_messages", False):
        raise ValueError("Бот должен быть администратором канала с правом публикации.")
    return chat


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    destination = active_channel()
    text = (
        f"⚙️ AnimeHUB Bleach\n"
        f"OWNER_USER_ID: {OWNER_USER_ID}\n"
        f"Канал: {destination or 'не выбран'}\n"
        f"Последний пост навигации: {STATE.get('last_nav_message_id', 'нет')}\n"
        f"Арки: {len(ITEMS)}\n"
        f"Изменённые ссылки: {len(STATE.get('links', {}))}"
    )
    if destination:
        try:
            chat = await channel_info(context)
            text += f"\n✅ Доступ проверен: {chat.title}"
        except (TelegramError, ValueError) as exc:
            text += f"\n❌ Не удалось проверить права: {exc}"
    await update.message.reply_text(text)


async def preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    await update.message.reply_text(MAIN_TEXT, reply_markup=private_main_keyboard())
    await update.message.reply_text("Это личный предпросмотр. В канале будут кнопки, открывающие меню в личном чате с ботом.")


async def post_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    try:
        chat = await channel_info(context)
    except (TelegramError, ValueError) as exc:
        await update.message.reply_text(f"❌ Не удалось проверить канал: {exc}")
        return
    if chat is None:
        await update.message.reply_text("Сначала привяжи канал через /set_channel или переменную BLEACH_CHANNEL_ID.")
        return
    context.user_data["pending_channel"] = chat.id
    await update.message.reply_text(
        f"📋 Подготовлена навигация для канала:\n"
        f"{chat.title} (ID {chat.id})\n\n"
        "Для публикации напиши /confirm_nav.\n"
        "Для отмены — /cancel_nav.\n\n"
        "Проверить меню без отправки: /preview"
    )


async def cancel_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    context.user_data.pop("pending_channel", None)
    await update.message.reply_text("Публикация отменена.")


async def confirm_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    pending = context.user_data.get("pending_channel")
    if pending is None:
        await update.message.reply_text("Сначала отправь /post_nav и проверь канал назначения.")
        return
    try:
        chat = await channel_info(context)
        if chat is None or chat.id != pending:
            await update.message.reply_text("Канал изменился. Повтори /post_nav.")
            return
        me = await context.bot.get_me()
        sent = await context.bot.send_message(
            chat_id=chat.id,
            text=MAIN_TEXT,
            reply_markup=channel_nav_keyboard(me.username),
            disable_web_page_preview=True,
        )
    except (TelegramError, ValueError) as exc:
        await update.message.reply_text(f"❌ Публикация не удалась: {exc}")
        return
    context.user_data.pop("pending_channel", None)
    STATE["last_nav_message_id"] = sent.message_id
    STATE["channel_id"] = chat.id
    STATE["channel_title"] = chat.title
    save_state()
    await update.message.reply_text(
        f"✅ Навигация опубликована в «{chat.title}».\n"
        f"ID сообщения: {sent.message_id}\n\n"
        "Закрепить: /pin_nav"
    )


async def set_nav_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restore the ID of a nav post previously published by THIS bot."""
    if not await require_owner(update):
        return
    if not active_channel():
        await update.message.reply_text("Сначала настрой канал через /set_channel.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit() or int(context.args[0]) < 1:
        await update.message.reply_text("Пример: /set_nav 123 — ID существующего поста навигации, отправленного этим ботом.")
        return
    message_id = int(context.args[0])
    STATE["last_nav_message_id"] = message_id
    save_state()
    await update.message.reply_text(
        f"✅ Сохранён ID сообщения {message_id}. Теперь можно /update_nav или /pin_nav.\n"
        "Важно: бот сможет редактировать только свой собственный пост."
    )


async def update_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    channel_id = active_channel()
    message_id = STATE.get("last_nav_message_id")
    if not channel_id or not message_id:
        await update.message.reply_text("Нет сохранённого сообщения навигации. Сначала /post_nav → /confirm_nav.")
        return
    try:
        me = await context.bot.get_me()
        await context.bot.edit_message_text(
            chat_id=channel_id,
            message_id=message_id,
            text=MAIN_TEXT,
            reply_markup=channel_nav_keyboard(me.username),
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            await update.message.reply_text("✅ Меню уже актуально.")
            return
        await update.message.reply_text(f"❌ Не удалось изменить сообщение: {exc}")
        return
    except TelegramError as exc:
        await update.message.reply_text(f"❌ Не удалось изменить сообщение: {exc}")
        return
    await update.message.reply_text("✅ Навигация в канале обновлена без новой публикации.")


async def pin_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    channel_id = active_channel()
    message_id = STATE.get("last_nav_message_id")
    if not channel_id or not message_id:
        await update.message.reply_text("Нет поста для закрепления. Сначала /post_nav → /confirm_nav.")
        return
    try:
        await context.bot.pin_chat_message(chat_id=channel_id, message_id=message_id, disable_notification=True)
    except TelegramError as exc:
        await update.message.reply_text(f"❌ Не удалось закрепить: {exc}\nПроверь права бота.")
        return
    await update.message.reply_text("📌 Навигация закреплена!")


async def links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    lines = ["🔗 ID арок и текущие ссылки:"]
    for section in CATALOG["sections"]:
        lines.append(f"\n{section['title']}")
        for item in section["items"]:
            lines.append(f"• {item['id']}: {link_for(item)}")
    await update.message.reply_text("\n".join(lines), disable_web_page_preview=True)


async def setlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    if len(context.args) != 2:
        await update.message.reply_text("Пример: /setlink substitute https://t.me/c/3593534577/23")
        return
    item_id, link = context.args
    if item_id not in ITEMS:
        await update.message.reply_text("❌ Неизвестный ID арки. Список: /links")
        return
    if not telegram_url(link):
        await update.message.reply_text("❌ Нужна настоящая ссылка https://t.me/...")
        return
    STATE.setdefault("links", {})[item_id] = link
    save_state()
    await update.message.reply_text(
        f"✅ Ссылка для «{ITEMS[item_id]['name']}» изменена.\n"
        "Новые ссылки показываются в меню сразу после открытия.\n"
        "Для сохранения после пересборки BotHost пропиши её также в catalog.json на GitHub."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "⚔️ БЛИЧ — КОМАНДЫ\n\n"
        "/start или /bleach — меню арок\n"
        "/myid — узнать свой Telegram ID\n\n"
        "🔐 Для владельца (только личный чат):\n"
        "/status — проверить канал\n"
        "/set_channel -100... — привязать канал\n"
        "/preview — личный предпросмотр\n"
        "/post_nav — подготовить публикацию\n"
        "/confirm_nav — подтвердить публикацию\n"
        "/cancel_nav — отменить\n"
        "/update_nav — обновить уже опубликованное меню\n"
        "/set_nav ID — восстановить ID своего поста навигации\n"
        "/pin_nav — закрепить меню\n"
        "/links — ссылки и ID арок\n"
        "/setlink ID URL — заменить ссылку арки"
    )


def run() -> None:
    app = Application.builder().token(TOKEN).build()
    for command in ("start", "bleach", "menu"):
        app.add_handler(CommandHandler(command, start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("set_channel", set_channel))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("preview", preview))
    app.add_handler(CommandHandler("post_nav", post_nav))
    app.add_handler(CommandHandler("confirm_nav", confirm_nav))
    app.add_handler(CommandHandler("cancel_nav", cancel_nav))
    app.add_handler(CommandHandler("update_nav", update_nav))
    app.add_handler(CommandHandler("set_nav", set_nav_message))
    app.add_handler(CommandHandler("pin_nav", pin_nav))
    app.add_handler(CommandHandler("links", links))
    app.add_handler(CommandHandler("setlink", setlink))
    app.add_handler(CallbackQueryHandler(buttons))
    log.info("Bleach bot starting; %d arcs loaded", len(ITEMS))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run()
