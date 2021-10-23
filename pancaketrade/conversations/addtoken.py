from typing import NamedTuple
from decimal import Decimal

from pancaketrade.network import Network
from pancaketrade.persistence import Token, db
from pancaketrade.utils.config import Config
from pancaketrade.utils.db import token_exists
from pancaketrade.utils.generic import chat_message, check_chat_id, format_token_amount
from pancaketrade.watchers import TokenWatcher
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    Filters,
    MessageHandler,
)
from web3 import Web3
from web3.exceptions import ABIFunctionNotFound, ContractLogicError


class AddTokenResponses(NamedTuple):
    ADDRESS: int = 0
    EMOJI: int = 1
    SLIPPAGE: int = 2


class AddTokenConversation:
    def __init__(self, parent, config: Config):
        self.parent = parent
        self.net: Network = parent.net
        self.config = config
        self.next = AddTokenResponses()
        self.handler = ConversationHandler(
            entry_points=[CommandHandler('addtoken', self.command_addtoken)],
            states={
                self.next.ADDRESS: [MessageHandler(Filters.text & ~Filters.command, self.command_addtoken_address)],
                self.next.EMOJI: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_addtoken_emoji),
                    CallbackQueryHandler(self.command_addtoken_noemoji, pattern='^None$'),
                ],
                self.next.SLIPPAGE: [MessageHandler(Filters.text & ~Filters.command, self.command_addtoken_slippage)],
            },
            fallbacks=[CommandHandler('cancel', self.command_canceltoken)],
            name='addtoken_conversation',
        )

    @check_chat_id
    def command_addtoken(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        context.user_data['addtoken'] = {}
        chat_message(update, context, text='Пожалуйста отправьте мне адрес контракта токена.', edit=False)
        return self.next.ADDRESS

    @check_chat_id
    def command_addtoken_address(self, update: Update, context: CallbackContext):
        assert update.message and update.message.text and context.user_data is not None
        response = update.message.text.strip()
        if Web3.isAddress(response):
            token_address = Web3.toChecksumAddress(response)
        else:
            chat_message(
                update, context, text='⚠️ Указанный вами адрес не является действительным. Попробуйте снова:', edit=False
            )
            return self.next.ADDRESS
        add = context.user_data['addtoken']
        add['address'] = str(token_address)
        try:
            add['decimals'] = self.net.get_token_decimals(token_address)
            add['symbol'] = self.net.get_token_symbol(token_address)
        except (ABIFunctionNotFound, ContractLogicError):
            chat_message(
                update,
                context,
                text='⛔ Неверное ABI для этого адреса.\n'
                + 'Убедитесь что адрес является смартконтрактом '
                + f'<a href="https://bscscan.com/address/{token_address}">BscScan</a> и попробуйте снова.',
                edit=False,
            )
            del context.user_data['addtoken']
            return ConversationHandler.END

        if token_exists(address=token_address):
            chat_message(update, context, text=f'⚠️ Токен <b>{add["symbol"]}</b> уже добавлен.', edit=False)
            del context.user_data['addtoken']
            return ConversationHandler.END
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton('🙅‍♂️ No emoji', callback_data='None')]])
        chat_message(
            update,
            context,
            text=f'Спасибо, токен <b>{add["symbol"]}</b> использует '
            + f'{add["decimals"]} decimals. '
            + 'Пожалуйста отправьте мне EMOJI с которой у вас будет ассоциироваться токен для простоты использования, '
            + 'или кликните на кнопку ниже.',
            reply_markup=reply_markup,
            edit=False,
        )
        return self.next.EMOJI

    @check_chat_id
    def command_addtoken_emoji(self, update: Update, context: CallbackContext):
        assert update.message and update.message.text and context.user_data is not None
        add = context.user_data['addtoken']
        add['icon'] = update.message.text.strip()
        chat_message(
            update,
            context,
            text='Хорошо, токен будет показываться как '
            + f'<b>"{add["icon"]} {add["symbol"]}"</b>. '
            + 'Какое стандартное проскальзывание в % для торговли на PancakeSwap?',
            edit=False,
        )
        return self.next.SLIPPAGE

    @check_chat_id
    def command_addtoken_noemoji(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        add = context.user_data['addtoken']
        add['icon'] = None
        chat_message(
            update,
            context,
            text=f'Хорошо, токен будет показываться как <b>"{add["symbol"]}"</b>. '
            + 'Какое стандартное проскальзывание в % для торговли на PancakeSwap?',
            edit=self.config.update_messages,
        )
        return self.next.SLIPPAGE

    @check_chat_id
    def command_addtoken_slippage(self, update: Update, context: CallbackContext):
        assert update.message and update.message.text and context.user_data is not None
        try:
            slippage = Decimal(update.message.text.strip())
        except Exception:
            chat_message(
                update,
                context,
                text='⚠️ Это не является действительным значением проскальзывания. Пожалуйста введите значение в процентах. Попробуйте снова:',
                edit=False,
            )
            return self.next.SLIPPAGE
        if slippage < Decimal("0.01") or slippage > 100:
            chat_message(
                update,
                context,
                text='⚠️ Это не является действительным значением проскальзывания. Пожалуйста введите значение между 0.01 и 100 '
                + 'процентов. Попробуйте снова:',
                edit=False,
            )
            return self.next.SLIPPAGE
        add = context.user_data['addtoken']
        add['default_slippage'] = f'{slippage:.2f}'
        emoji = add['icon'] + ' ' if add['icon'] else ''

        chat_message(
            update,
            context,
            text=f'Хорошо, токен <b>{emoji}{add["symbol"]}</b> '
            + f'будет использовать <b>{add["default_slippage"]}%</b> проскальзывание как стандартное.',
            edit=False,
        )
        try:
            db.connect()
            with db.atomic():
                token_record = Token.create(**add)
        except Exception as e:
            chat_message(update, context, text=f'⛔ Failed to create database record: {e}', edit=False)
            del context.user_data['addtoken']
            return ConversationHandler.END
        finally:
            del context.user_data['addtoken']
            db.close()
        token = TokenWatcher(token_record=token_record, net=self.net, dispatcher=context.dispatcher, config=self.config)
        self.parent.watchers[token.address] = token
        balance = self.net.get_token_balance(token_address=token.address)
        balance_usd = self.net.get_token_balance_usd(token_address=token.address, balance=balance)
        buttons = [
            [
                InlineKeyboardButton('➕ Создать ордер', callback_data=f'addorder:{token.address}'),
                InlineKeyboardButton('💰 Купить/Продать сейчас', callback_data=f'buysell:{token.address}'),
            ]
        ]
        if not self.net.is_approved(token_address=token.address):
            buttons.append([InlineKeyboardButton('☑️ подтвердить для продажи', callback_data=f'approve:{token.address}')])
        reply_markup = InlineKeyboardMarkup(buttons)
        chat_message(
            update,
            context,
            text='✅ Токен добавлен успешно. '
            + f'Баланс  {format_token_amount(balance)} {token.symbol} (${balance_usd:.2f}).',
            reply_markup=reply_markup,
            edit=False,
        )
        return ConversationHandler.END

    @check_chat_id
    def command_canceltoken(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        del context.user_data['addtoken']
        chat_message(update, context, text='⚠️ OK, Я отменяю эту команду.', edit=False)
        return ConversationHandler.END
