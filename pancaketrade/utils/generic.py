"""Generic utilities."""
import functools
import logging
import threading
from decimal import Decimal
from typing import Any, Callable, Iterable, List, Mapping, Optional

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import CallbackContext


class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def start_in_thread(func: Callable, args: Iterable[Any] = []) -> None:
    t = threading.Thread(target=func, args=args)
    t.daemon = True
    t.start()


def check_chat_id(func: Callable) -> Callable:
    """Compare chat ID with admin's chat ID and refuse access if unauthorized."""

    @functools.wraps(func)
    def wrapper_check_chat_id(this, update: Update, context: CallbackContext, *args, **kwargs):
        if update.callback_query:
            update.callback_query.answer()
        if update.effective_chat is None:
            logger.debug('No chat ID')
            return
        if context.user_data is None:
            logger.debug('No user data')
            return
        if update.message is None and update.callback_query is None:
            logger.debug('No message')
            return
        if update.message and update.message.text is None and update.callback_query is None:
            logger.debug('No text in message')
            return
        chat_id = update.effective_chat.id
        if chat_id == this.config.secrets.admin_chat_id:
            return func(this, update, context, *args, **kwargs)
        logger.warning(f'Prevented user {chat_id} to interact.')
        context.bot.send_message(
            chat_id=this.config.secrets.admin_chat_id, text=f'Prevented user {chat_id} to interact.'
        )
        context.bot.send_message(chat_id=chat_id, text='This bot is not public, you are not allowed to use it.')

    return wrapper_check_chat_id


def chat_message(
    update: Update,
    context: CallbackContext,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    edit: bool = False,
) -> Optional[Message]:
    assert update.effective_chat
    if update.callback_query is not None and edit:
        query = update.callback_query
        try:
            query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
            )
        except Exception as e:  # if the message did not change, we can get an exception, we ignore it
            if not str(e).startswith('Message is not modified'):
                logger.error(f'Exception during message update: {e}')
                context.bot.send_message(chat_id=update.effective_chat.id, text=f'Exception during message update: {e}')
        return None
    return context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)


def get_tokens_keyboard_layout(
    watchers: Mapping, callback_prefix: Optional[str] = None, per_row: int = 3
) -> List[List[InlineKeyboardButton]]:
    buttons: List[InlineKeyboardButton] = []
    for token in sorted(watchers.values(), key=lambda token: token.symbol.lower()):
        callback = f'{callback_prefix}:{token.address}' if callback_prefix else token.address
        buttons.append(InlineKeyboardButton(token.name, callback_data=callback))
    buttons.append(InlineKeyboardButton('❌ Cancel', callback_data='canceltokenchoice'))
    buttons_layout = [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]  # noqa: E203
    return buttons_layout


def format_token_amount(amount: Decimal) -> str:
    if amount >= 100:
        return f'{amount:,.1f}'
    return f'{amount:.4g}'


def format_price_fixed(price: Decimal) -> str:
    price_fixed = f'{price:.{-price.adjusted()+2}f}' if price < 100 else f'{price:.1f}'
    return price_fixed
