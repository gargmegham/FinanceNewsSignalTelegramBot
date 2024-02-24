import logging

from requests import Session
from requests.exceptions import ConnectionError, Timeout, TooManyRedirects
import json
import traceback, html
import requests
from io import BytesIO
from datetime import datetime
import pytz

from typing import Tuple, Optional
from telegram import (
    Update,
    Chat,
    ChatMember,
    ParseMode,
    ChatMemberUpdated,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    CallbackContext,
    ChatMemberHandler,
    Filters,
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger(__name__)

"""Run bot."""
# Create the Updater and pass it your bot's token.
secrets = json.load(open("secrets.json"))
secrets = secrets["secrets"]
bot_token = secrets["BOT_API_TOKEN"]
updater = Updater(bot_token)

# Get the dispatcher to register handlers
dispatcher = updater.dispatcher


def extract_status_change(
    chat_member_update: ChatMemberUpdated,
) -> Optional[Tuple[bool, bool]]:
    """Takes a ChatMemberUpdated instance and extracts whether the 'old_chat_member' was a member
    of the chat and whether the 'new_chat_member' is a member of the chat. Returns None, if
    the status didn't change.
    """
    status_change = chat_member_update.difference().get("status")
    old_is_member, new_is_member = chat_member_update.difference().get(
        "is_member", (None, None)
    )

    if status_change is None:
        return None

    old_status, new_status = status_change
    was_member = old_status in [
        ChatMember.MEMBER,
        ChatMember.CREATOR,
        ChatMember.ADMINISTRATOR,
    ] or (old_status == ChatMember.RESTRICTED and old_is_member is True)
    is_member = new_status in [
        ChatMember.MEMBER,
        ChatMember.CREATOR,
        ChatMember.ADMINISTRATOR,
    ] or (new_status == ChatMember.RESTRICTED and new_is_member is True)

    return was_member, is_member


def track_chats(update: Update, context: CallbackContext) -> None:
    """Tracks the chats the bot is in."""
    result = extract_status_change(update.my_chat_member)
    if result is None:
        return
    was_member, is_member = result

    # Let's check who is responsible for the change
    cause_name = update.effective_user.full_name

    # Handle chat types differently:
    chat = update.effective_chat
    if chat.type == Chat.PRIVATE:
        if not was_member and is_member:
            logger.info("%s started the bot", cause_name)
            context.bot_data.setdefault("user_ids", set()).add(chat.id)
        elif was_member and not is_member:
            logger.info("%s blocked the bot", cause_name)
            context.bot_data.setdefault("user_ids", set()).discard(chat.id)
    elif chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
        if not was_member and is_member:
            logger.info("%s added the bot to the group %s", cause_name, chat.title)
            context.bot_data.setdefault("group_ids", set()).add(chat.id)
        elif was_member and not is_member:
            logger.info("%s removed the bot from the group %s", cause_name, chat.title)
            context.bot_data.setdefault("group_ids", set()).discard(chat.id)
    else:
        if not was_member and is_member:
            logger.info("%s added the bot to the channel %s", cause_name, chat.title)
            context.bot_data.setdefault("channel_ids", set()).add(chat.id)
        elif was_member and not is_member:
            logger.info(
                "%s removed the bot from the channel %s", cause_name, chat.title
            )
            context.bot_data.setdefault("channel_ids", set()).discard(chat.id)


def show_chats(update: Update, context: CallbackContext) -> None:
    """Shows which chats the bot is in"""
    user_ids = ", ".join(
        str(uid) for uid in context.bot_data.setdefault("user_ids", set())
    )
    group_ids = ", ".join(
        str(gid) for gid in context.bot_data.setdefault("group_ids", set())
    )
    channel_ids = ", ".join(
        str(cid) for cid in context.bot_data.setdefault("channel_ids", set())
    )
    text = (
        f"@{context.bot.username} is currently in a conversation with the user IDs {user_ids}."
        f" Moreover it is a member of the groups with IDs {group_ids} "
        f"and administrator in the channels with IDs {channel_ids}."
    )
    update.effective_message.reply_text(text)


def greet_chat_members(update: Update, _: CallbackContext) -> None:
    """Greets new users in chats and announces when someone leaves"""
    result = extract_status_change(update.chat_member)
    if result is None:
        return

    was_member, is_member = result
    cause_name = update.chat_member.from_user.mention_html()
    member_name = update.chat_member.new_chat_member.user.mention_html()

    if not was_member and is_member:
        update.effective_chat.send_message(
            f"Hello {member_name} was added by {cause_name}.\nWelcome to the community!",
            parse_mode=ParseMode.HTML,
        )


def convert_ISO_EDT(from_time):
    from_time = datetime.strptime(
        from_time[: from_time.rindex(".")], "%Y-%m-%dT%H:%M:%S"
    )
    eastern = pytz.timezone("US/Eastern")
    edt_dt = from_time.astimezone(eastern)
    fmt = "%Y-%m-%d %H:%M:%S %Z%z"
    return edt_dt.strftime(fmt)


def round_value(val):
    if int(val) >= 100:
        return format(val, ".2f")
    if int(val) >= 10:
        return format(val, ".3f")
    if int(val) >= 1:
        return format(val, ".4f")
    if val >= 0.01:
        return format(val, ".5f")
    return format(val, ".8f")


def show_price(update: Update, context: CallbackContext):
    print(update.message.text)
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/ohlcv/latest"
    info_url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/info"
    quote_url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
    try:
        command, symbol = update.message.text.split()
        symbol = symbol.upper()
    except:
        update.message.reply_text("Please enter a valid command.\nExample: price BTC")
        return
    parameters = {"symbol": symbol}
    secrets = json.load(open("secrets.json"))
    secrets = secrets["secrets"]
    headers = {
        "Accepts": "application/json",
        "X-CMC_PRO_API_KEY": secrets["COINMARKETCAP_KEY"],
    }

    session = Session()
    session.headers.update(headers)

    try:
        response = session.get(url, params=parameters)
        response_meta = session.get(info_url, params=parameters)
        response_quote = session.get(quote_url, params=parameters)
        if response.status_code == 200:
            try:
                data = json.loads(response.text)
                if data["status"]["error_message"] is not None:
                    raise Exception(data["status"]["error_message"])
                data = data["data"][symbol]
            except:
                update.message.reply_text("Data not available for this one")
                return
            if response_meta.status_code != 200:
                logo = None
            else:
                meta_data = json.loads(response_meta.text)
                if meta_data["status"]["error_message"] is None:
                    token_website = meta_data["data"][symbol]["urls"]["website"][0]
                    logo = meta_data["data"][symbol]["logo"]
                    response_img = requests.get(logo)
                    if response_img.status_code == 200:
                        img = BytesIO(response_img.content)
                    else:
                        img = None
                else:
                    logo = None
            if logo is None:
                img = None
            price_change = None
            currency_name = list(data["quote"].keys())[0]
            if response_quote.status_code != 200:
                price_change = None
            else:
                quote_data = json.loads(response_quote.text)
                if quote_data["status"]["error_message"] is None:
                    price_change = quote_data["data"][symbol]["quote"][currency_name][
                        "percent_change_24h"
                    ]
                    price_change = (
                        f"<b>+{format(price_change, '.2f')}</b>"
                        if price_change > 0
                        else f"<i>{format(price_change, '.2f')}</i>"
                    )
                else:
                    price_change = None
            price = data["quote"][currency_name]
            # last_updated = convert_ISO_EDT(data['last_updated'])
            # time_open = convert_ISO_EDT(data['time_open'])
            last_updated = " ".join(
                price["last_updated"][: price["last_updated"].rindex(":")].split("T")
            )
            time_open = " ".join(
                data["time_open"][: data["time_open"].rindex(":")].split("T")
            )
            message = "<code>Symbol: {symbol} </code>{price_change}%<code>\nPrice: {price} {currency_name}\nName: {name}\n</code>website: {token_website}<code>\nTime Open: {time_open} UTC\nLast updated: {last_updated} UTC\nopen: {open} {currency_name}\nlow: {low} {currency_name}\nhigh: {high} {currency_name}\nclose: {close} {currency_name}\nvolume: {volume} {currency_name}</code>".format(
                symbol=data["symbol"],
                price=round_value(price["close"]),
                name=data["name"],
                token_website=token_website,
                last_updated=last_updated,
                open=round_value(price["open"]),
                low=round_value(price["low"]),
                high=round_value(price["high"]),
                close=round_value(price["close"]),
                volume=round_value(price["volume"]),
                currency_name=currency_name,
                price_change=price_change,
                time_open=time_open,
            )
            if img is not None:
                update.message.reply_photo(img, caption=message, parse_mode="HTML")
                return
            else:
                update.message.reply_text(message, parse_mode="HTML")
                return
        elif response.status_code == 400:
            update.message.reply_text("Please enter a valid symbol.")
            return
        else:
            logger.info("request failed due to {}".format(response.status_code))
    except (ConnectionError, Timeout, TooManyRedirects) as e:
        logger.error(
            "connectionerror = {}\ntimeout = {}\ntoo many redirects = {}".format(
                e[0], e[1], e[2]
            )
        )
    except Exception as ee:
        logger.error("error = {}".format(ee))


def error_handler(update: object, context: CallbackContext) -> None:
    """Log the error and send a telegram message to notify the developer."""
    # Log the error before we do anything else, so we can see it even if something breaks.
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    # traceback.format_exception returns the usual python message about an exception, but as a
    # list of strings rather than a single string, so we have to join them together.
    tb_list = traceback.format_exception(
        None, context.error, context.error.__traceback__
    )
    tb_string = "".join(tb_list)

    # Build the message with some markup and additional information about what happened.
    # You might need to add some logic to deal with messages longer than the 4096 character limit.
    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    message = (
        f"An exception was raised while handling an update\n"
        f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}"
        "</pre>\n\n"
        f"<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n"
        f"<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n"
        f"<pre>{html.escape(tb_string)}</pre>"
    )

    # Finally, send the message
    context.bot.send_message(
        chat_id=secrets["DEV_USER"], text=message, parse_mode=ParseMode.HTML
    )


def getNews(context) -> None:
    """check for new tweet"""
    try:
        job = context.job
        secrets = json.load(open("secrets.json", "r"))
        last_news_id = secrets["secrets"]["last_news_id"]
        params = {
            "api_key": secrets["secrets"]["CRYPTOCOMPARE_API"],
            "feeds": secrets["secrets"]["FEED_SOURCES"],
        }
        news_api_endpoint = f"https://min-api.cryptocompare.com/data/v2/news/?lang=EN"
        response = requests.request("GET", news_api_endpoint, params=params, timeout=10)
        if response.status_code != 200:
            raise Exception(
                "Request returned an error: {} {}".format(
                    response.status_code, response.text
                )
            )
        response = json.loads(response.text)
        news_no = 0
        if not len(response["Data"]) or int(response["Data"][0]["id"]) <= int(
            last_news_id
        ):
            return

        secrets["secrets"]["last_news_id"] = response["Data"][0]["id"]
        json.dump(secrets, open("secrets.json", "w"), indent=4)

        while int(response["Data"][news_no]["id"]) > int(last_news_id):
            news_no += 1
        news_no -= 1
        while news_no >= 0:
            news_text = response["Data"][news_no]
            title = news_text["title"]
            body = news_text["body"]
            news_url = news_text["url"]
            news = f"{title}\n\n{body}\n\n{news_url}"
            context.bot.send_message(job.context, text=news, parse_mode=ParseMode.HTML)
            news_no -= 1
    except Exception as ee:
        logger.info(ee, "while fetching news")


dispatcher.job_queue.run_repeating(
    getNews, 60, context=secrets["CHAT_HANDLE"], name=secrets["CHAT_HANDLE"] + " news"
)

# on different commands - answer in Telegram
dispatcher.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
dispatcher.add_handler(CommandHandler("show_chats", show_chats))
dispatcher.add_handler(MessageHandler(Filters.regex("^price\s\w+$"), show_price))
dispatcher.add_handler(
    ChatMemberHandler(greet_chat_members, ChatMemberHandler.CHAT_MEMBER)
)
dispatcher.add_error_handler(error_handler)

# Start the Bot
updater.start_polling(
    allowed_updates=[Update.MESSAGE, Update.CHAT_MEMBER, Update.MY_CHAT_MEMBER]
)

# Block until you press Ctrl-C or the process receives SIGINT, SIGTERM or
# SIGABRT. This should be used most of the time, since start_polling() is
# non-blocking and will stop the bot gracefully.
updater.idle()
