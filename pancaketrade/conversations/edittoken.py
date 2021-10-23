from decimal import Decimal
from typing import NamedTuple, Optional

from pancaketrade.network import Network
from pancaketrade.persistence import db
from pancaketrade.utils.config import Config
from pancaketrade.utils.generic import chat_message, check_chat_id, format_price_fixed
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


class EditTokenResponses(NamedTuple):
    ACTION_CHOICE: int = 0
    EMOJI: int = 1
    SLIPPAGE: int = 2
    BUYPRICE: int = 3


class EditTokenConversation:
    def __init__(self, parent, config: Config):
        self.parent = parent
        self.net: Network = parent.net
        self.config = config
        self.next = EditTokenResponses()
        self.handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.command_edittoken, pattern='^edittoken:0x[a-fA-F0-9]{40}$')],
            states={
                self.next.ACTION_CHOICE: [
                    CallbackQueryHandler(
                        self.command_edittoken_action, pattern='^emoji$|^slippage$|^buyprice$|^cancel$'
                    )
                ],
                self.next.EMOJI: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_edittoken_emoji),
                    CallbackQueryHandler(self.command_edittoken_emoji, pattern='^[^:]*$'),
                ],
                self.next.SLIPPAGE: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_edittoken_slippage),
                    CallbackQueryHandler(self.command_edittoken_slippage, pattern='^[^:]*$'),
                ],
                self.next.BUYPRICE: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_edittoken_buyprice),
                    CallbackQueryHandler(self.command_edittoken_buyprice, pattern='^[^:]*$'),
                ],
            },
            fallbacks=[CommandHandler('cancel', self.command_canceltoken)],
            name='edittoken_conversation',
        )

    @check_chat_id
    def command_edittoken(self, update: Update, context: CallbackContext):
        assert update.callback_query and context.user_data is not None
        query = update.callback_query
        assert query.data
        token_address = query.data.split(':')[1]
        if not Web3.isChecksumAddress(token_address):
            self.command_error(update, context, text='Не верный адрес токена.')
            return ConversationHandler.END
        token: TokenWatcher = self.parent.watchers[token_address]
        context.user_data['edittoken'] = {'token_address': token_address}
        buttons = [
            [
                InlineKeyboardButton(f'{token.emoji}изменить emoji', callback_data='emoji'),
                InlineKeyboardButton('Изменить стандартное проскальзывание', callback_data='slippage'),
            ],
            [
                InlineKeyboardButton('Изменить цену покупки', callback_data='buyprice'),
                InlineKeyboardButton('❌ Отмена', callback_data='cancel'),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(buttons)
        chat_message(
            update,
            context,
            text=f'Что вы хотите изменить для токена {token.name}?',
            reply_markup=reply_markup,
            edit=self.config.update_messages,
        )
        return self.next.ACTION_CHOICE

    @check_chat_id
    def command_edittoken_action(self, update: Update, context: CallbackContext):
        assert update.callback_query and context.user_data is not None
        query = update.callback_query
        assert query.data
        edit = context.user_data['edittoken']
        token: TokenWatcher = self.parent.watchers[edit['token_address']]
        if query.data == 'cancel':
            return self.command_canceltoken(update, context)
        elif query.data == 'emoji':
            buttons = [
                InlineKeyboardButton('🙅‍♂️ Без emoji', callback_data='None'),
                InlineKeyboardButton('❌ Отмена', callback_data='cancel'),
            ]
            reply_markup = InlineKeyboardMarkup([buttons])
            chat_message(
                update,
                context,
                text=f'Пожалуйста отправьте мне EMOJI с которой у вас будет ассоциироваться токен {token.symbol} для простоты использования, '
                + 'или кликните на кнопку ниже.',
                reply_markup=reply_markup,
                edit=self.config.update_messages,
            )
            return self.next.EMOJI
        elif query.data == 'slippage':
            buttons = [
                InlineKeyboardButton(f'Keep {token.default_slippage}%', callback_data=str(token.default_slippage)),
                InlineKeyboardButton('❌ Отмена', callback_data='cancel'),
            ]
            reply_markup = InlineKeyboardMarkup([buttons])
            chat_message(
                update,
                context,
                text=f'Какое стандартное проскальзывание в % для торговли {token.name} на PancakeSwap?',
                reply_markup=reply_markup,
                edit=self.config.update_messages,
            )
            return self.next.SLIPPAGE
        elif query.data == 'buyprice':
            current_price, _ = self.net.get_token_price(token_address=token.address)
            current_price_fixed = format_price_fixed(current_price)
            buttons2 = [
                [InlineKeyboardButton('No price (disable profit calc)', callback_data='None')],
                [InlineKeyboardButton('❌ Отмена', callback_data='cancel')],
            ]
            reply_markup = InlineKeyboardMarkup(buttons2)
            chat_message(
                update,
                context,
                text=f'Какова была эффективная цена для покупки (после комиссии) для {token.name} которую вы инвестиировали? '
                + 'У вас есть 3 опции для этого:\n'
                + f' ・ Стандартная запись например "<code>{current_price_fixed}</code>"\n'
                + f' ・ Научная запись например "<code>{current_price:.1e}</code>"\n'
                + ' ・ Сумма вашей покупки в BNB например "<code>0.5BNB</code>" (включая "BNB" в конце)\n',
                reply_markup=reply_markup,
                edit=self.config.update_messages,
            )
            return self.next.BUYPRICE
        else:
            self.command_error(update, context, text='Invalid callback')
            return ConversationHandler.END

    @check_chat_id
    def command_edittoken_emoji(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        edit = context.user_data['edittoken']
        token: TokenWatcher = self.parent.watchers[edit['token_address']]
        if update.message is not None:
            assert update.message.text
            edit['icon'] = update.message.text.strip()
        else:
            assert update.callback_query
            query = update.callback_query
            assert query.data
            if query.data == 'cancel':
                return self.command_canceltoken(update, context)
            elif query.data == 'None':
                edit['icon'] = None
            else:
                edit['icon'] = query.data

        token_record = token.token_record
        try:
            db.connect()
            with db.atomic():
                token_record.icon = edit['icon']
                token_record.save()
        except Exception as e:
            self.command_error(update, context, text=f'Failed to update database record: {e}')
            return ConversationHandler.END
        finally:
            del context.user_data['edittoken']
            db.close()
        token.emoji = token_record.icon + ' ' if token_record.icon else ''
        token.name = token.emoji + token.symbol
        chat_message(
            update,
            context,
            text=f'✅ Хорошо, токен будет показываться как <b>"{token.name}"</b>. ',
            edit=self.config.update_messages,
        )
        return ConversationHandler.END

    @check_chat_id
    def command_edittoken_slippage(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        edit = context.user_data['edittoken']
        token: TokenWatcher = self.parent.watchers[edit['token_address']]
        if update.message is not None:
            assert update.message.text
            try:
                slippage = Decimal(update.message.text.strip())
            except Exception:
                chat_message(
                    update,
                    context,
                    text='⚠️ Это не является действительным значением проскальзывания. Пожалуйста введите значение между 0.01 и 100 '
                    + 'процентов (без записи знака процента). Попробуйте заново:',
                    edit=False,
                )
                return self.next.SLIPPAGE
        else:
            assert update.callback_query
            query = update.callback_query
            assert query.data
            if query.data == 'cancel':
                return self.command_canceltoken(update, context)
            try:
                slippage = Decimal(query.data)
            except Exception:
                self.command_error(update, context, text='Не верное значение стандартного проскальзывания.')
                return ConversationHandler.END
        if slippage < Decimal("0.01") or slippage > 100:
            chat_message(
                update,
                context,
                text='⚠️ Это не является действительным значением проскальзывания. Пожалуйста введите значение между 0.01 и 100 '
                + 'процентов. Попробуйте заново:',
                edit=False,
            )
            return self.next.SLIPPAGE
        edit['default_slippage'] = f'{slippage:.2f}'

        token_record = token.token_record
        try:
            db.connect()
            with db.atomic():
                token_record.default_slippage = edit['default_slippage']
                token_record.save()
        except Exception as e:
            self.command_error(update, context, text=f'Failed to update database record: {e}')
            return ConversationHandler.END
        finally:
            del context.user_data['edittoken']
            db.close()
        token.default_slippage = Decimal(token_record.default_slippage)
        chat_message(
            update,
            context,
            text=f'✅ Хорошо, токен {token.name} '
            + f'будет использовать <b>{edit["default_slippage"]}%</b> проскальзывание как стандартное.',
            edit=self.config.update_messages,
        )
        return ConversationHandler.END

    @check_chat_id
    def command_edittoken_buyprice(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        edit = context.user_data['edittoken']
        token: TokenWatcher = self.parent.watchers[edit['token_address']]
        effective_buy_price: Optional[Decimal]
        if update.message is not None:
            assert update.message.text
            user_input = update.message.text.strip().lower()
            if 'bnb' in user_input:
                balance = self.net.get_token_balance(token_address=token.address)
                if balance == 0:  # would lead to division by zero
                    chat_message(
                        update,
                        context,
                        text='⚠️ Баланс токена равен 0, нельзя использовать расчет от суммы BNB. '
                        + 'Попробуйте еще раз, указав цену:',
                        edit=False,
                    )
                    return self.next.BUYPRICE
                try:
                    buy_amount = Decimal(user_input[:-3])
                except Exception:
                    chat_message(
                        update, context, text='⚠️ Введенная вами сумма BNB недействительна. Попробуйте заново:', edit=False
                    )
                    return self.next.BUYPRICE
                effective_buy_price = buy_amount / balance
            else:
                try:
                    effective_buy_price = Decimal(user_input)
                except ValueError:
                    chat_message(
                        update,
                        context,
                        text='⚠️ Это не действительное значение цены. Попробуйте заново:',
                        edit=False,
                    )
                    return self.next.BUYPRICE
        else:
            assert update.callback_query
            query = update.callback_query
            assert query.data
            if query.data == 'cancel':
                return self.command_canceltoken(update, context)
            elif query.data == 'None':
                effective_buy_price = None
            else:
                self.command_error(update, context, text='Invalid callback.')
                return ConversationHandler.END

        edit['effective_buy_price'] = effective_buy_price

        token_record = token.token_record
        try:
            db.connect()
            with db.atomic():
                token_record.effective_buy_price = (
                    str(edit['effective_buy_price']) if edit['effective_buy_price'] else None
                )
                token_record.save()
        except Exception as e:
            self.command_error(update, context, text=f'Failed to update database record: {e}')
            return ConversationHandler.END
        finally:
            del context.user_data['edittoken']
            db.close()
        token.effective_buy_price = edit['effective_buy_price']
        if effective_buy_price is None:
            chat_message(
                update,
                context,
                text='✅ Хорошо, эффективная цена покупки для расчета прибыли отключена.',
                edit=self.config.update_messages,
            )
        else:
            chat_message(
                update,
                context,
                text=f'✅ Хорошо, токен {token.name} '
                + f'был куплен {token.effective_buy_price:.4g} BNB за токен.',
                edit=self.config.update_messages,
            )
        return ConversationHandler.END

    @check_chat_id
    def command_canceltoken(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        del context.user_data['edittoken']
        chat_message(update, context, text='⚠️ OK, я отменяю команду.', edit=False)
        return ConversationHandler.END

    def command_error(self, update: Update, context: CallbackContext, text: str):
        assert context.user_data is not None
        del context.user_data['edittoken']
        chat_message(update, context, text=f'⛔️ {text}', edit=False)
