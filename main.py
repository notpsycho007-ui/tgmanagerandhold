import csv
import io
import logging
import math
import re
import sqlite3
from datetime import datetime

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from config import ADMIN_IDS, BOT_TOKEN, MAX_NAME_LEN, PAGE_SIZE, SEARCH_LIMIT
from database import (
    add_account,
    count_accounts,
    count_today,
    delete_account,
    get_account,
    get_accounts_page,
    get_all_accounts,
    init_db,
    search_accounts,
    update_account,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- constants

(
    NAME, PHONE,
    DELETE_ID,
    EDIT_ID, EDIT_NAME, EDIT_PHONE,
    SEARCH,
) = range(7)

BTN_ADD = "➕ Add Account"
BTN_LIST = "📋 Account List"
BTN_SEARCH = "🔍 Search"
BTN_EDIT = "✏️ Edit Account"
BTN_DELETE = "🗑 Delete Account"
BTN_EXPORT = "📤 Export CSV"
BTN_DASH = "📊 Dashboard"
BTN_HELP = "ℹ️ Help"

KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_ADD, BTN_LIST],
        [BTN_SEARCH, BTN_EDIT],
        [BTN_DELETE, BTN_EXPORT],
        [BTN_DASH, BTN_HELP],
    ],
    resize_keyboard=True,
)

MENU_FILTER = filters.Text(
    [BTN_ADD, BTN_LIST, BTN_SEARCH, BTN_EDIT, BTN_DELETE, BTN_EXPORT, BTN_DASH, BTN_HELP]
)
TEXT_INPUT = filters.TEXT & ~filters.COMMAND & ~MENU_FILTER

PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
CANCEL_HINT = "(/cancel দিয়ে বাতিল করুন)"


# ------------------------------------------------------------------ helpers

def normalize_phone(raw: str) -> str:
    phone = re.sub(r"[\s\-().]", "", raw)
    if phone.startswith("00"):
        phone = "+" + phone[2:]
    return phone


def valid_name(name: str) -> bool:
    return 0 < len(name) <= MAX_NAME_LEN


def csv_safe(value: str) -> str:
    """Excel/Sheets formula injection ঠেকায়।"""
    if value and value[0] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def fmt_account(account_id, name, phone) -> str:
    return f"ID: {account_id}\nName: {name}\nPhone: {phone}\n──────────────\n"


def render_page(page: int):
    total = count_accounts()
    if total == 0:
        return "No accounts added yet.", None

    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    rows = get_accounts_page(PAGE_SIZE, page * PAGE_SIZE)

    text = f"📋 ACCOUNT LIST — Page {page + 1}/{pages} (Total: {total})\n\n"
    text += "".join(fmt_account(*row) for row in rows)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"pg:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"pg:{page + 1}"))

    return text, (InlineKeyboardMarkup([nav]) if nav else None)


# -------------------------------------------------------------------- guard

async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin ছাড়া কেউ কোনো handler-এ পৌঁছাতে পারবে না।"""
    user = update.effective_user
    if user and user.id in ADMIN_IDS:
        return

    if update.callback_query:
        await update.callback_query.answer("Access denied.", show_alert=True)
    elif update.message and update.message.text and update.message.text.startswith("/start"):
        await update.message.reply_text("⛔ Access denied.")

    raise ApplicationHandlerStop


# ----------------------------------------------------------------- commands

async def start(update, context):
    context.user_data.clear()
    await update.message.reply_text(
        "🚀 TELEMANAGER PRO\n\nWelcome Admin!", reply_markup=KEYBOARD
    )
    return ConversationHandler.END


async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Operation cancelled.", reply_markup=KEYBOARD)
    return ConversationHandler.END


async def help_command(update, context):
    await update.message.reply_text(
        "TELEMANAGER PRO HELP\n\n"
        "➕ Add Account — নতুন account যোগ\n"
        "📋 Account List — সব account (page করে)\n"
        "🔍 Search — নাম বা phone দিয়ে খোঁজা\n"
        "✏️ Edit Account — ID দিয়ে edit\n"
        "🗑 Delete Account — ID দিয়ে delete (confirm সহ)\n"
        "📤 Export CSV — সব data CSV হিসেবে\n"
        "📊 Dashboard — statistics\n\n"
        "/start - Main menu\n"
        "/cancel - Cancel current operation\n\n"
        "This version manages account records "
        "and does not log into Telegram accounts."
    )
    return ConversationHandler.END


async def dashboard(update, context):
    await update.message.reply_text(
        "📊 TELEMANAGER PRO DASHBOARD\n\n"
        f"Total Accounts: {count_accounts()}\n"
        f"Added Today: {count_today()}\n"
        "System: Online"
    )
    return ConversationHandler.END


# --------------------------------------------------------------------- list

async def show_list(update, context):
    text, markup = render_page(0)
    await update.message.reply_text(text, reply_markup=markup)
    return ConversationHandler.END


async def list_page_callback(update, context):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    text, markup = render_page(page)
    try:
        await query.edit_message_text(text, reply_markup=markup)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


# ---------------------------------------------------------------------- add

async def add_start(update, context):
    context.user_data.clear()
    await update.message.reply_text(f"Enter an account name:\n{CANCEL_HINT}")
    return NAME


async def receive_name(update, context):
    name = update.message.text.strip()
    if not valid_name(name):
        await update.message.reply_text(
            f"❌ Name 1-{MAX_NAME_LEN} অক্ষরের মধ্যে হতে হবে। আবার দিন:"
        )
        return NAME

    context.user_data["account_name"] = name
    await update.message.reply_text(
        "Enter the phone number with country code:\nExample: +8801712345678"
    )
    return PHONE


async def receive_phone(update, context):
    phone = normalize_phone(update.message.text)
    if not PHONE_RE.match(phone):
        await update.message.reply_text(
            "❌ Invalid number. + এবং country code সহ দিন।\nExample: +8801712345678"
        )
        return PHONE

    name = context.user_data.get("account_name")
    if not name:
        await update.message.reply_text(
            "Session expired. আবার শুরু করুন।", reply_markup=KEYBOARD
        )
        return ConversationHandler.END

    try:
        add_account(name, phone)
    except sqlite3.IntegrityError:
        await update.message.reply_text(
            "❌ এই phone number আগে থেকেই আছে। অন্য নম্বর দিন:"
        )
        return PHONE
    except Exception:
        logger.exception("Failed to add account")
        await update.message.reply_text(
            "❌ Database error. পরে আবার চেষ্টা করুন।", reply_markup=KEYBOARD
        )
    else:
        await update.message.reply_text(
            f"✅ Account saved!\n\nName: {name}\nPhone: {phone}",
            reply_markup=KEYBOARD,
        )

    context.user_data.clear()
    return ConversationHandler.END


# --------------------------------------------------------------------- edit

async def edit_start(update, context):
    context.user_data.clear()
    await update.message.reply_text(f"Enter Account ID to edit:\n{CANCEL_HINT}")
    return EDIT_ID


async def edit_receive_id(update, context):
    try:
        account_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Please enter a valid numeric ID:")
        return EDIT_ID

    account = get_account(account_id)
    if not account:
        await update.message.reply_text("❌ Account ID not found. আবার দিন:")
        return EDIT_ID

    context.user_data["edit_account"] = account
    await update.message.reply_text(
        f"Current name: {account[1]}\n"
        "Enter new name (বদলাতে না চাইলে - পাঠান):"
    )
    return EDIT_NAME


async def edit_receive_name(update, context):
    account_id, old_name, _ = context.user_data["edit_account"]
    text = update.message.text.strip()

    if text == "-":
        new_name = old_name
    elif valid_name(text):
        new_name = text
    else:
        await update.message.reply_text(
            f"❌ Name 1-{MAX_NAME_LEN} অক্ষরের মধ্যে হতে হবে। আবার দিন:"
        )
        return EDIT_NAME

    context.user_data["new_name"] = new_name
    old_phone = context.user_data["edit_account"][2]
    await update.message.reply_text(
        f"Current phone: {old_phone}\n"
        "Enter new phone (বদলাতে না চাইলে - পাঠান):"
    )
    return EDIT_PHONE


async def edit_receive_phone(update, context):
    account_id, _, old_phone = context.user_data["edit_account"]
    text = update.message.text.strip()

    if text == "-":
        new_phone = old_phone
    else:
        new_phone = normalize_phone(text)
        if not PHONE_RE.match(new_phone):
            await update.message.reply_text(
                "❌ Invalid number. + এবং country code সহ দিন।\n"
                "Example: +8801712345678"
            )
            return EDIT_PHONE

    new_name = context.user_data["new_name"]

    try:
        updated = update_account(account_id, new_name, new_phone)
    except sqlite3.IntegrityError:
        await update.message.reply_text(
            "❌ এই phone number অন্য account-এ আছে। অন্য নম্বর দিন:"
        )
        return EDIT_PHONE
    except Exception:
        logger.exception("Failed to update account")
        await update.message.reply_text(
            "❌ Database error. পরে আবার চেষ্টা করুন।", reply_markup=KEYBOARD
        )
    else:
        if updated:
            await update.message.reply_text(
                f"✅ Account updated!\n\nID: {account_id}\n"
                f"Name: {new_name}\nPhone: {new_phone}",
                reply_markup=KEYBOARD,
            )
        else:
            await update.message.reply_text(
                "❌ Account আর পাওয়া যাচ্ছে না।", reply_markup=KEYBOARD
            )

    context.user_data.clear()
    return ConversationHandler.END


# ------------------------------------------------------------------- delete

async def delete_start(update, context):
    context.user_data.clear()
    await update.message.reply_text(f"Enter Account ID to delete:\n{CANCEL_HINT}")
    return DELETE_ID


async def delete_receive(update, context):
    try:
        account_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Please enter a valid numeric ID:")
        return DELETE_ID

    account = get_account(account_id)
    if not account:
        await update.message.reply_text("❌ Account ID not found. আবার দিন:")
        return DELETE_ID

    markup = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Yes, delete", callback_data=f"dy:{account_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data="dn"),
        ]]
    )
    await update.message.reply_text(
        f"⚠️ এই account delete করবেন?\n\n"
        f"ID: {account[0]}\nName: {account[1]}\nPhone: {account[2]}",
        reply_markup=markup,
    )
    return ConversationHandler.END


async def delete_confirm_callback(update, context):
    query = update.callback_query
    await query.answer()
    account_id = int(query.data.split(":")[1])

    if delete_account(account_id):
        await query.edit_message_text(f"✅ Account #{account_id} deleted.")
    else:
        await query.edit_message_text("❌ Account ID not found.")


async def delete_cancel_callback(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Delete cancelled.")


# ------------------------------------------------------------------- search

async def search_start(update, context):
    context.user_data.clear()
    await update.message.reply_text(
        f"নাম বা phone-এর অংশ লিখুন:\n{CANCEL_HINT}"
    )
    return SEARCH


async def search_receive(update, context):
    query = update.message.text.strip()
    if not query:
        await update.message.reply_text("কিছু লিখুন:")
        return SEARCH

    rows = search_accounts(query, SEARCH_LIMIT)
    if not rows:
        await update.message.reply_text(
            "No match found.", reply_markup=KEYBOARD
        )
        return ConversationHandler.END

    text = f"🔍 SEARCH RESULTS ({len(rows)})\n\n"
    text += "".join(fmt_account(*row) for row in rows)
    if len(rows) == SEARCH_LIMIT:
        text += f"\nপ্রথম {SEARCH_LIMIT}টা দেখানো হয়েছে। আরও নির্দিষ্ট করে খুঁজুন।"

    await update.message.reply_text(text, reply_markup=KEYBOARD)
    return ConversationHandler.END


# ------------------------------------------------------------------- export

async def export_csv(update, context):
    rows = get_all_accounts()
    if not rows:
        await update.message.reply_text("No accounts to export.")
        return ConversationHandler.END

    text_buf = io.StringIO()
    writer = csv.writer(text_buf)
    writer.writerow(["id", "name", "phone", "created_at"])
    for account_id, name, phone, created_at in rows:
        writer.writerow([account_id, csv_safe(name), phone, created_at])

    # utf-8-sig: Excel-এ বাংলা নাম ঠিকমতো দেখাবে
    data = io.BytesIO(text_buf.getvalue().encode("utf-8-sig"))
    filename = f"accounts_{datetime.now():%Y%m%d_%H%M%S}.csv"

    await update.message.reply_document(
        document=data, filename=filename, caption=f"📤 {len(rows)} accounts exported."
    )
    return ConversationHandler.END


# ------------------------------------------------------------- menu routing

ACTIONS = {
    BTN_ADD: add_start,
    BTN_LIST: show_list,
    BTN_SEARCH: search_start,
    BTN_EDIT: edit_start,
    BTN_DELETE: delete_start,
    BTN_EXPORT: export_csv,
    BTN_DASH: dashboard,
    BTN_HELP: help_command,
}


async def menu_router(update, context):
    """Menu button সবসময় কাজ করবে, conversation-এর মাঝখানে চাপলেও।"""
    context.user_data.clear()
    result = await ACTIONS[update.message.text](update, context)
    return result if result is not None else ConversationHandler.END


# ------------------------------------------------------------------- errors

async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong. Please try again."
            )
        except Exception:
            logger.exception("Failed to send error message")


async def post_init(app: Application):
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Main menu"),
            BotCommand("cancel", "Cancel current operation"),
            BotCommand("help", "Help"),
        ]
    )


# --------------------------------------------------------------------- main

def main():
    if not BOT_TOKEN or not ADMIN_IDS:
        raise ValueError("BOT_TOKEN and ADMIN_ID (or ADMIN_IDS) are required.")

    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # group -1: সবার আগে admin check
    app.add_handler(TypeHandler(Update, guard), group=-1)

    conversation = ConversationHandler(
        entry_points=[MessageHandler(MENU_FILTER, menu_router)],
        states={
            NAME: [MessageHandler(TEXT_INPUT, receive_name)],
            PHONE: [MessageHandler(TEXT_INPUT, receive_phone)],
            DELETE_ID: [MessageHandler(TEXT_INPUT, delete_receive)],
            EDIT_ID: [MessageHandler(TEXT_INPUT, edit_receive_id)],
            EDIT_NAME: [MessageHandler(TEXT_INPUT, edit_receive_name)],
            EDIT_PHONE: [MessageHandler(TEXT_INPUT, edit_receive_phone)],
            SEARCH: [MessageHandler(TEXT_INPUT, search_receive)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
            CommandHandler("help", help_command),
            MessageHandler(MENU_FILTER, menu_router),
        ],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(conversation)

    app.add_handler(CallbackQueryHandler(list_page_callback, pattern=r"^pg:\d+$"))
    app.add_handler(CallbackQueryHandler(delete_confirm_callback, pattern=r"^dy:\d+$"))
    app.add_handler(CallbackQueryHandler(delete_cancel_callback, pattern=r"^dn$"))

    app.add_error_handler(error_handler)

    logger.info("TELEMANAGER PRO started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
