import os
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    CallbackContext
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, joinedload
from sqlalchemy.exc import IntegrityError
from models.models import Base, Client, Executor, MessageModeration, Service, OrderRequest, OrderServices, Manager
from decimal import Decimal
from datetime import timedelta, datetime
import re, uuid, json, random
from config import TELEGRAM_TOKEN, DATABASE_URL

def is_valid_number(input_str: str) -> bool:
    try:
        float(input_str)  # Пробуем преобразовать в число
        return True
    except ValueError:
        return False

async def executor_button_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    chat_id = query.message.chat_id
    await query.answer()  # Подтверждаем нажатие кнопки

    if chat_id not in user_states:
        return

    state = user_states[chat_id]

    # Выбор категории исполнителя
    if state["action"] == "add_executor_category":
        category = query.data.split("_")[1]
        user_states[chat_id]["category"] = category

        # **Выводим кнопки выбора сложности**
        keyboard = [
            [InlineKeyboardButton("Лёгкая", callback_data="difficulty_1")],
            [InlineKeyboardButton("Средняя", callback_data="difficulty_2")],
            [InlineKeyboardButton("Сложная", callback_data="difficulty_3")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Выберите сложность", reply_markup=reply_markup)

        user_states[chat_id]["action"] = "add_executor_difficulty"

    # Выбор уровня сложности
    elif state["action"] == "add_executor_difficulty":
        difficulty_level = int(query.data.split("_")[1])
        user_states[chat_id]["difficulty_level"] = difficulty_level

        username = user_states[chat_id]["username"]
        category = user_states[chat_id]["category"]

        try:
            executor_id = create_executor(username, category, difficulty_level)
            if executor_id:
                await query.message.reply_text(f"✅ Исполнитель {username} зарегистрирован!")
            else:
                await query.message.reply_text("❌ Ошибка при регистрации исполнителя. Введите корректное ID")
        except Exception as e:
            await query.message.reply_text(f"Ошибка: {e}")
        finally:
            del user_states[chat_id]

async def service_button_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    chat_id = query.message.chat_id
    await query.answer()  # Подтверждаем нажатие кнопки

    if chat_id not in user_states:
        return

    state = user_states[chat_id]

    # Выбор категории услуги
    if state["action"] == "add_service_category":
        category = query.data.split("_")[2]  # Получаем выбранную категорию
        user_states[chat_id]["category"] = category

        await query.message.reply_text("Введите минимальную цену услуги:")
        user_states[chat_id]["action"] = "add_service_price"

    # Ввод цены и сохранение услуги
    elif state["action"] == "add_service_price":
        try:
            min_price = Decimal(update.message.text)
            user_states[chat_id]["min_price"] = min_price

            name = user_states[chat_id]["name"]
            category = user_states[chat_id]["category"]

            service_id = create_service(name, category, min_price)
            if service_id:
                await query.message.reply_text(f"✅ Услуга '{name}' добавлена в категорию '{category}' с ID {service_id}")
            else:
                await query.message.reply_text("❌ Ошибка при добавлении услуги")
        except Exception as e:
            await update.message.reply_text(f"Ошибка при добавлении услуги: {e}")
        finally:
            del user_states[chat_id]

def convert_currency(amount_usd, usd_to_rub=100, usd_to_byn=3.3):
    """
    Конвертирует сумму из долларов в рубли и белорусские рубли.

    :param amount_usd: Сумма в долларах (тип decimal.Decimal).
    :param usd_to_rub: Курс доллара к рублю (по умолчанию 100).
    :param usd_to_byn: Курс доллара к белорусскому рублю (по умолчанию 3.3).
    :return: Кортеж (price_rub, price_byn) — суммы в рублях и белорусских рублях.
    """
    if amount_usd is None:
        return None, None  # Возвращаем None для обеих валют, если amount_usd равно None
    # Преобразуем decimal.Decimal в float
    amount_usd_float = float(amount_usd)

    # Выполняем конвертацию
    price_rub = amount_usd_float * usd_to_rub
    price_byn = amount_usd_float * usd_to_byn

    return price_rub, price_byn

# Подключение к базе данных
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
user_states = {}
service_id = None

# Подключение к БД
def connect_db():
    try:
        # Создаем подключение и возвращаем его
        return SessionLocal()
    except Exception as e:
        print(f"Ошибка при подключении к базе данных: {e}")
        return None

def check_and_update_user(username: str, telegram_id: str) -> None:
    with SessionLocal() as session:
        # Проверяем, есть ли пользователь в таблице менеджеров
        session.expire_all()  # Очистка кэша сессии
        manager = session.query(Manager).filter(Manager.telegram_username == username).first()
        if manager:
            print(f"[DEBUG] Уже есть такой менеджер {username}")  # Логируем входящее сообщение
            # Если telegram_id отсутствует, обновляем запись
            if not manager.telegram_id:
                manager.telegram_id = telegram_id
                session.commit()
            return

        # Проверяем, есть ли пользователь в таблице исполнителей
        executor = session.query(Executor).filter(Executor.telegram_username == username).first()
        session.expire_all()  # Очистка кэша сессии
        if executor:
            print(f"[DEBUG] Уже есть такой исполнитель {username}")  # Логируем входящее сообщение
            # Если telegram_id отсутствует, обновляем запись
            if not executor.telegram_id:
                executor.telegram_id = telegram_id
                session.commit()
            return
        
        session.expire_all()  # Очистка кэша сессии
        # Проверяем, есть ли пользователь в таблице клиентов
        client = session.query(Client).filter(Client.telegram_username == username).first()
        if client:
            print(f"[DEBUG] Найден клиент в БД: {client}")  
            # Если telegram_id отсутствует, обновляем запись
            if not client.telegram_id:
                client.telegram_id = telegram_id
                session.commit()
            return

        # Если пользователя нет ни в одной из таблиц, добавляем его в таблицу клиентов
        new_client = Client(
            login=username,
            telegram_username=username,
            telegram_id=telegram_id
        )
        new_client.set_password("FX@&9+9№exfXRc#e)wlo")  # Дефолтный пароль
        session.add(new_client)
        session.commit()
        print(f"[DEBUG] записан новый клиент {username}")  # Логируем входящее сообщение

async def handle_admin_commands(update: Update, context: CallbackContext, text: str, user_id: str) -> bool:
    ADMIN_COMMANDS = {
        "↩️Назад↩️", "Посмотреть клиентов", "Посмотреть исполнителей", "Посмотреть услуги", 
        "Посмотреть заказы", "Посмотреть услуги в заказах", "👤Добавить клиента👤", 
        "👨‍💻Добавить исполнителя👨‍💻", "📄Добавить услугу📄", "📋Добавить заказ📋", 
        "➕Добавить услугу в заказ➕", "Удалить клиента", "Удалить исполнителя", 
        "Удалить услугу", "Удалить заказ", "Удалить услугу из заказа", "Изменить исполнителя", 
        "Изменить услугу", "Изменить заказ", "Изменить услугу в заказе", "Добавить", 
        "Удалить", "Изменить", "Посмотреть"
    }

    if text in ADMIN_COMMANDS and user_id not in SPECIAL_USERS:
        await update.message.reply_text("🚫 У вас нет доступа к этой команде.")
        return True
    return False

async def handle_user_state(update: Update, context: CallbackContext, text: str, chat_id: int):
    state = context.user_data
    action = state.get("action")

    if not action:
        await update.message.reply_text("⚠️ Ошибка: действие не выбрано. Пожалуйста, начните заново.")
        return

    match action:
        case "choose_order_for_client_chat":
            await handle_choose_order_for_client_chat(update, context, text)
        case "choose_order_to_complete":
            await handle_choose_order_to_complete(update, context, text)
        case _ if action.startswith("add_"):
            await handle_add_actions(update, context, state)
        case _ if action.startswith("delete_"):
            await handle_delete_actions(update, context, state)
        case _ if action.startswith("edit_"):
            await handle_edit_actions(update, context, state)
        case "choose_service_for_chat":
            await handle_choose_service_for_chat(update, context, text, chat_id)
        case "send_message_to_executor":
            await handle_send_message_to_executor(update, context, text)
        case "send_message_to_client":
            await handle_send_message_to_client(update, context, text)
        case "edit_message":
            await handle_edit_message(update, context, text)
        case _:
            await update.message.reply_text("⚠️ Неизвестное действие. Пожалуйста, начните заново.")
            context.user_data.pop("action", None)

async def handle_add_actions(update: Update, context: CallbackContext, state: dict):
    if state["action"].startswith("add_client"):
        await process_client_message(update, context, state)
    elif state["action"].startswith("add_executor"):
        await process_executor_message(update, context, state)
    elif state["action"].startswith("add_service_to_order"):
        await process_service_to_order_message(update, context, state)
    elif state["action"].startswith("add_service"):
        await process_service_message(update, context, state)
    elif state["action"].startswith("add_order"):
        await process_order_message(update, context, state)

async def handle_delete_actions(update: Update, context: CallbackContext, state: dict):
    if state["action"].startswith("delete_client"):
        await process_delete_client(update, context)
    elif state["action"].startswith("delete_executor"):
        await process_delete_executor(update, context)
    elif state["action"].startswith("delete_service_from_order"):
        await process_delete_service_from_order(update, context)
    elif state["action"].startswith("delete_service"):
        await process_delete_service(update, context)
    elif state["action"].startswith("delete_order"):
        await process_delete_order(update, context)

async def handle_edit_actions(update: Update, context: CallbackContext, state: dict):
    if state["action"].startswith("edit_executor"):
        await process_edit_executor(update, context)
    elif state["action"].startswith("edit_service_in_order"):
        await process_edit_service_in_order(update, context)
    elif state["action"].startswith("edit_service"):
        await process_edit_service(update, context, state)
    elif state["action"].startswith("edit_order"):
        await process_edit_order(update, context)

async def handle_choose_service_for_chat(update: Update, context: CallbackContext, text: str, chat_id: int):
    try:
        service_id = int(text)  # Пытаемся преобразовать текст в число (ID услуги)
        executor_telegram_id = get_executor_id_by_service(service_id)  # Получаем ID исполнителя
        
        if not executor_telegram_id:
            await update.message.reply_text("❌ Не удалось найти исполнителя для данной услуги.")
            return
        
        # Сохраняем состояние пользователя
        context.user_data["action"] = "send_message_to_executor"
        context.user_data["service_id"] = service_id
        context.user_data["executor_telegram_id"] = executor_telegram_id
        
        await update.message.reply_text("✍️ Введите ваше сообщение для исполнителя:")
    
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите корректный ID услуги.")

async def handle_send_message_to_executor(update: Update, context: CallbackContext, text: str):
    message_text = text
    service_id = context.user_data.get("service_id")
    executor_telegram_id = context.user_data.get("executor_telegram_id")
    executor_username = get_executor_username_by_service(service_id)
    
    if not service_id:
        await update.message.reply_text("❌ Ошибка: данные услуги не найдены.")
        return
    if not executor_telegram_id:
        await update.message.reply_text("❌ Ошибка: данные исполнителя не найдены.")
        return
    
    # Получаем информацию об услуге и заказе
    with SessionLocal() as session:
        service = (
            session.query(OrderServices)
            .options(joinedload(OrderServices.service), joinedload(OrderServices.order))
            .filter(OrderServices.id == service_id)
            .first()
        )
    
    if service:
        order_id = service.order_id  # ID заказа
        service_in_order_id = service.id  # ID услуги в заказе
        service_name = service.service.name if service.service else "Неизвестная услуга"
    else:
        order_id = "N/A"
        service_in_order_id = "N/A"
        service_name = "Неизвестная услуга"
    
    # Проверяем сообщение на подозрительные символы
    if is_suspicious(message_text):
        executor_username = get_executor_username_by_service(service_id)
        context.user_data["service_id"] = service_id
        await send_to_manager(update, context, message_text, executor_telegram_id, executor_username, "executor")
        await update.message.reply_text("🔎 Сообщение отправлено на проверку менеджеру.")
    else:
        # Форматируем сообщение для исполнителя
        formatted_message = (
            f"📨 *Новое сообщение*\n\n"
            f"📋 *Заказ:* №{order_id}\n"
            f"📦 *Услуга в заказе:* №{service_in_order_id}\n\n"
            f"📋 *Услуга:* {service_name}\n"
            f"💬 *Текст сообщения:*\n{message_text}"
        )
        
        # Отправляем сообщение исполнителю
        await context.bot.send_message(chat_id=executor_telegram_id, text=formatted_message)
        await update.message.reply_text("✅ Сообщение отправлено исполнителю.")
    
    await start(update, context)
    context.user_data.pop("action", None)

def store_message_data(session, message_id, message_text, receiver_telegram_id, receiver_username, receiver_type, sender_username, service_id):
    try:
        message_data = {
            'message_id': message_id,
            'message_text': message_text,
            'receiver_telegram_id': receiver_telegram_id,
            'receiver_username': receiver_username,
            'receiver_type': receiver_type,
            'sender_username': sender_username,
            'service_id': service_id,
            'processed': False,
            'created_at': datetime.now()
        }
        session.execute(text('''
            INSERT INTO message_moderation 
            (message_id, message_text, receiver_telegram_id, receiver_username, receiver_type, sender_username, service_id, processed, created_at)
            VALUES (:message_id, :message_text, :receiver_telegram_id, :receiver_username, :receiver_type, :sender_username, :service_id, :processed, :created_at)
        '''), message_data)
        session.commit()
        return True
    except Exception as e:
        print(f'Error storing message data: {e}')
        session.rollback()
        return False

async def handle_send_message_to_client(update: Update, context: CallbackContext, text: str):
    message_text = text
    state = context.user_data
    
    # Получаем все необходимые данные из состояния
    service_id = state.get("service_id")
    client_telegram_id = state.get("client_telegram_id")
    client_username = state.get("client_username") or get_client_username_by_service(service_id)
    sender_username = update.effective_user.username

    print(f"[DEBUG] service_id={service_id}, client_telegram_id={client_telegram_id}, client_username={client_username}")

    if not all([service_id, client_telegram_id, client_username]):
        await update.message.reply_text("❌ Ошибка: недостающие данные о заказе или клиенте.")
        return

    # Проверяем сообщение на подозрительность
    if is_suspicious(message_text):
        try:
            await send_to_manager(
                update=update,
                context=context,
                message_text=message_text,
                receiver_telegram_id=client_telegram_id,  # Важно!
                receiver_username=client_username,
                receiver_type="client",
                service_id=service_id
            )
            await update.message.reply_text("🔎 Сообщение отправлено на проверку менеджеру.")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при отправке на модерацию: {e}")
    else:
        try:
            # Форматируем сообщение для клиента
            formatted_msg = (
                f"📨 Сообщение от исполнителя:\n\n"
                f"💬 {message_text}\n\n"
                f"По услуге №{service_id}"
            )
            await context.bot.send_message(
                chat_id=client_telegram_id,
                text=formatted_msg
            )
            await update.message.reply_text(f"✅ Сообщение отправлено клиенту")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при отправке клиенту: {e}")

    # Очищаем состояние независимо от результата
    await start(update, context)
    context.user_data.clear()

async def handle_edit_message(update: Update, context: CallbackContext) -> None:
    if 'edit_message' not in context.user_data:
        return
    new_text = update.message.text
    print(f"[DEBUG] Получен новый текст для редактирования: {new_text}")

    # Получаем данные редактирования
    edit_data = context.user_data.get("edit_message")
    if not edit_data:
        print("[DEBUG] Нет активного запроса на редактирование.")
        await update.message.reply_text("❌ Нет активного запроса на редактирование.")
        return

    print(f"[DEBUG] edit_data: {edit_data}")
    
    # Извлекаем необходимые данные
    receiver_telegram_id = edit_data["receiver_telegram_id"]
    message_id = edit_data["message_id"]
    service_id = edit_data.get("service_id")
    original_text = edit_data.get("original_text", "")

    # Формируем базовую информацию о сообщении
    message_header = "📨 *Сообщение от клиента* "
    service_info = ""
    order_info = ""

    # Если есть service_id, пытаемся получить информацию о заказе
    if service_id:
        try:
            with SessionLocal() as session:
                service = (
                    session.query(OrderServices)
                    .options(
                        joinedload(OrderServices.service),
                        joinedload(OrderServices.order)
                    )
                    .filter(OrderServices.id == service_id)
                    .first()
                )
                
                if service:
                    order_id = service.order_id if service.order else "N/A"
                    service_name = service.service.name if service.service else "Неизвестная услуга"
                    service_info = (
                        f"\n\n📋 *Заказ:* №{order_id}\n"
                        f"📦 *Услуга:* {service_name}"
                    )
        except Exception as e:
            print(f"[ERROR] Ошибка при получении информации о заказе: {e}")

    # Форматируем полное сообщение
    formatted_message = (
        f"{message_header}"
        f"{service_info}"
        f"\n\n💬 *Текст сообщения:*\n{new_text}"
    )

    try:
        # Отправляем сообщение получателю
        await context.bot.send_message(
            chat_id=receiver_telegram_id,
            text=formatted_message,
            parse_mode="Markdown"
        )
        
        # Обновляем запись в базе данных
        with SessionLocal() as session:
            session.execute(text('''
                UPDATE message_moderation 
                SET message_text = :message_text,
                    processed = TRUE
                WHERE message_id = :message_id
            '''), {
                'message_text': new_text,
                'message_id': message_id
            })
            session.commit()
            
        await update.message.reply_text("✅ Сообщение изменено и отправлено.")
            
    except Exception as e:
        error_msg = f"❌ Ошибка при отправке сообщения: {str(e)}"
        print(f"[ERROR] {error_msg}")
        await update.message.reply_text(error_msg)
        
    finally:
        # Всегда очищаем состояние, даже если возникла ошибка
        context.user_data.pop("edit_message", None)

async def handle_main_menu(update: Update, context: CallbackContext, text: str):
    if text in ["Добавить", "Изменить", "Удалить", "Посмотреть", "↩️Назад↩️"]:
        await process_main_menu(update, context)
        return True
    return False

async def handle_add_submenu(update: Update, context: CallbackContext, text: str, chat_id: int):
    if text == "👤Добавить клиента👤":
        context.user_data.clear()
        context.user_data['action'] = "add_client_username"
        await update.message.reply_text("Введите Telegram username клиента:")
    elif text == "👨‍💻Добавить исполнителя👨‍💻":
        context.user_data.clear()
        context.user_data['action'] = {"action": "add_executor_username"}
        await update.message.reply_text("Введите Telegram username исполнителя:")
    elif text == "📄Добавить услугу📄":
        context.user_data.clear()
        context.user_data['action'] = {"action": "add_service_name"}
        await update.message.reply_text("Введите название услуги:")
    elif text == "📋Добавить заказ📋":
        context.user_data.clear()
        await add_order(update, context)
    elif text == "➕Добавить услугу в заказ➕":
        context.user_data.clear()
        await add_service_to_order(update, context)

async def handle_delete_submenu(update: Update, context: CallbackContext, text: str):
    if text == "Удалить клиента":
        await delete_client_handler(update, context)
    elif text == "Удалить исполнителя":
        await delete_executor_handler(update, context)
    elif text == "Удалить услугу":
        await delete_service_handler(update, context)
    elif text == "Удалить заказ":
        await delete_order_handler(update, context)
    elif text == "Удалить услугу из заказа":
        await delete_service_from_order_handler(update, context)

async def handle_edit_submenu(update: Update, context: CallbackContext, text: str, chat_id: int):
    if text == "Изменить исполнителя":
        await edit_executor_handler(update, context)
    elif text == "Изменить услугу":
        await view_services(update, context)
        await update.message.reply_text("Введите ID услуги для изменения:")
        user_states[chat_id] = {"action": "edit_service_select"}
    elif text == "Изменить заказ":
        await edit_order_handler(update, context)
    elif text == "Изменить услугу в заказе":
        await edit_service_in_order_handler(update, context)

async def handle_view_submenu(update: Update, context: CallbackContext, text: str):
    if text == "Посмотреть клиентов":
        await view_clients(update, context)
    elif text == "Посмотреть исполнителей":
        await view_executors(update, context)
    elif text == "Посмотреть услуги в заказах":
        await view_services_in_orders(update, context)
    elif text == "Посмотреть услуги":
        await view_services(update, context)
    elif text == "Посмотреть заказы":
        await view_orders(update, context)

async def handle_contact_executor(update: Update, context: CallbackContext, user_id: str, chat_id: int):
    # Получаем список услуг клиента
    services_info = get_client_services(user_id)
    
    if services_info is None:
        await update.message.reply_text(
            "ℹ️ У вас нет активных заказов для связи с исполнителями.\n\n",
            parse_mode="Markdown"
        )
        return
    # Добавляем кнопку Отмена
    keyboard = [["❌ Отмена"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        services_info,
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    context.user_data["action"] = "choose_service_for_chat"

async def handle_choose_order_for_client_chat(update: Update, context: CallbackContext, text: str):
    try:
        order_id = int(text)

        with SessionLocal() as session:
            order = (
                session.query(OrderRequest)
                .options(joinedload(OrderRequest.client))
                .filter(OrderRequest.id == order_id)
                .first()
            )

            if not order or not order.client:
                await update.message.reply_text("❌ Заказ или клиент не найдены.")
                return

            # Найдём первую услугу в заказе
            service = (
                session.query(OrderServices)
                .filter(OrderServices.order_id == order_id)
                .first()
            )
            if not service:
                await update.message.reply_text("❌ В этом заказе нет услуг.")
                return

            # Записываем всё в context
            context.user_data["order_id"] = order_id
            context.user_data["client_telegram_id"] = order.client.telegram_id
            context.user_data["client_username"] = order.client.telegram_username
            context.user_data["service_id"] = service.id  # <-- вот этого не хватало
            context.user_data["action"] = "send_message_to_client"

            await update.message.reply_text("✍️ Введите ваше сообщение для клиента:")

    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите корректный ID заказа.")

async def handle_choose_order_to_complete(update: Update, context: CallbackContext, text: str):
    try:
        order_id = int(text)
        # Помечаем заказ как выполненный
        with SessionLocal() as session:
            service = (
                session.query(OrderServices)
                .filter(OrderServices.order_id == order_id)
                .first()
            )
            
            if service:
                service.status = "Завершён"
                session.commit()
                await update.message.reply_text(f"✅ Заказ {order_id} отмечен как выполненный!")
            else:
                await update.message.reply_text("❌ Заказ не найден.")
            
        context.user_data.pop("action", None)
        
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите корректный ID заказа.")

SPECIAL_USERS = {"ROST_MONTAGE", "SofyaHanovich"}

async def process_user_message(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    text = update.message.text
    user_id = update.message.from_user.username

    # Отладочная информация
    print(f"[DEBUG] Сообщение: {text}")
    print(f"[DEBUG] Текущее состояние: {context.user_data.get('action')}")

    # Обработка отмены
    if text.lower() in ["отмена", "cancel", "❌ отмена"]:
        await cancel_command(update, context)
        return

    # Проверка административных команд
    if await handle_admin_commands(update, context, text, user_id):
        return
    
    # Проверка состояния редактирования сообщения
    if context.user_data.get("edit_message"):
        await handle_edit_message(update, context)
        return

    # Обработка текущего состояния (главный приоритет)
    current_action = context.user_data.get("action")
    if current_action:
        # Для команд добавления
        if current_action.startswith("add_"):
            await handle_add_actions(update, context, context.user_data)
            return
        # Для команд удаления
        elif current_action.startswith("delete_"):
            await handle_delete_actions(update, context, context.user_data)
            return
        # Для команд изменения
        elif current_action.startswith("edit_"):
            await handle_edit_actions(update, context, context.user_data)
            return
        # Другие состояния
        else:
            await handle_user_state(update, context, text, chat_id)
            return
    
    # Обработка команд исполнителя
    if text == "✉️ Связаться с клиентом":
        await handle_contact_client(update, context, user_id, chat_id)
        return
    elif text == "🛫 Отправить выполненный заказ":
        await handle_complete_order(update, context, user_id, chat_id)
        return
    elif text == "🪬 Посмотреть активные заказы":
        await handle_view_orders(update, context, user_id)
        return

    # Обработка главного меню
    if await handle_main_menu(update, context, text):
        return

    # Обработка подменю "Добавить"
    if text in ["👤Добавить клиента👤", "👨‍💻Добавить исполнителя👨‍💻", "📄Добавить услугу📄", "📋Добавить заказ📋", "➕Добавить услугу в заказ➕"]:
        await handle_add_submenu(update, context, text, chat_id)
        return

    # Обработка подменю "Удалить"
    if text in ["Удалить клиента", "Удалить исполнителя", "Удалить услугу", "Удалить заказ", "Удалить услугу из заказа"]:
        await handle_delete_submenu(update, context, text)
        return

    # Обработка подменю "Изменить"
    if text in ["Изменить исполнителя", "Изменить услугу", "Изменить заказ", "Изменить услугу в заказе"]:
        await handle_edit_submenu(update, context, text, chat_id)
        return

    # Обработка подменю "Посмотреть"
    if text in ["Посмотреть клиентов", "Посмотреть исполнителей", "Посмотреть услуги", "Посмотреть заказы", "Посмотреть услуги в заказах"]:
        await handle_view_submenu(update, context, text)
        return

    # Обработка команды "Связаться с исполнителем"
    if text == "✉️ Связаться с исполнителем":
        await handle_contact_executor(update, context, user_id, chat_id)
        return
        
    if text == "🛎 Сделать заказ":
        await handle_create_order(update, context)
        return

    # Если ни одно условие не сработало
    await update.message.reply_text("⚠️ Пожалуйста, выберите действие из меню.")

async def handle_contact_client(update: Update, context: CallbackContext, user_id: str, chat_id: int):
    # Получаем список заказов исполнителя
    with SessionLocal() as session:
        executor = session.query(Executor).filter(Executor.telegram_username == user_id).first()
        if not executor:
            await update.message.reply_text("❌ Вы не зарегистрированы как исполнитель.")
            return

        services = (
            session.query(OrderServices)
            .options(
                joinedload(OrderServices.order).joinedload(OrderRequest.client),
                joinedload(OrderServices.service)
            )
            .filter(OrderServices.executor_id == executor.id)
            .all()
        )

    if not services:
        await update.message.reply_text("ℹ️ У вас нет активных заказов для связи с клиентами.")
        return
    
    keyboard = [["❌ Отмена"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    message_text = "Выберите заказ для связи с клиентом (Введите ID заказа):\n"
    for service in services:
        message_text += (
            f"📍 ID заказа: {service.order_id}\n"
            f"📌 Услуга: {service.service.name if service.service else 'N/A'}\n"
            f"📌 Статус: {service.status}\n"
            "———————————————\n"
        )

    await update.message.reply_text(
        message_text, 
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    context.user_data["action"] = "choose_order_for_client_chat"

MANAGER_CONTACT = "@PixelHUB_Manager"
async def handle_complete_order(update: Update, context: CallbackContext, user_id: str, chat_id: int):
    # Получаем информацию об исполнителе
    with SessionLocal() as session:
        executor = session.query(Executor).filter(Executor.telegram_username == user_id).first()
        if not executor:
            await update.message.reply_text("❌ Вы не зарегистрированы как исполнитель.")
            return

    # Формируем приятное сообщение с похвалой
    praise_messages = [
        "Отличная работа! 💪",
        "Вы настоящий профессионал! 👏",
        "Прекрасно справились с заданием! 🌟",
        "Ваш труд достоин похвалы! 🎉",
        "Браво! Так держать! 🚀"
    ]
    praise = random.choice(praise_messages)
    
    message_text = (
        f"{praise}\n\n"
        "⏳ Функция автоматической отправки выполненного заказа находится в разработке.\n\n"
        "📨 Пока Вы можете отправить выполненный заказ менеджеру в личные сообщения:\n"
        f"👉 {MANAGER_CONTACT}\n\n"
        "Не забудьте указать:\n"
        "• Номер заказа\n"
        "• Ссылки на выполненные работы\n"
        "• Любые дополнительные комментарии"
    )

    # Убираем parse_mode="Markdown" так как в сообщении нет markdown-разметки
    await update.message.reply_text(message_text)
    
    # Очищаем состояние пользователя
    if chat_id in user_states:
        del user_states[chat_id]

def get_client_orders(username: str):
    with SessionLocal() as session:
        client = session.query(Client).filter(Client.telegram_username == username).first()
        if not client:
            return None

        orders = (
            session.query(OrderRequest)
            .options(
                joinedload(OrderRequest.order_services).joinedload(OrderServices.service)
            )
            .filter(OrderRequest.client_id == client.id)
            .order_by(OrderRequest.id)
            .all()
        )

        return orders

async def handle_view_orders(update: Update, context: CallbackContext, user_id: str):
    # Проверяем, является ли пользователь исполнителем
    with SessionLocal() as session:
        executor = session.query(Executor).filter(Executor.telegram_username == user_id).first()
        if executor:
            # Логика для исполнителя
            services = (
                session.query(OrderServices)
                .options(
                    joinedload(OrderServices.order).joinedload(OrderRequest.client),
                    joinedload(OrderServices.service)
                )
                .filter(OrderServices.executor_id == executor.id)
                .all()
            )

            if not services:
                await update.message.reply_text("❌ У вас нет активных заказов.")
                return

            message_text = "📋 Ваши активные заказы:\n\n"
            for service in services:
                price_rub, price_byn = convert_currency(service.service_price)

                message_text += (
                    f"📍 *ID заказа:* {service.order_id}\n"
                    f"📌 *Услуга:* {service.service.name if service.service else 'N/A'}\n"
                    f"📦 *Количество:* {service.quantity}\n"
                    f"📅 *Дата создания:* {service.created_at.strftime('%d.%m.%y %H:%M') if service.created_at else 'N/A'}\n"
                    f"⏳ *Дата завершения:* {service.estimated_completion.strftime('%d.%m.%y %H:%M') if service.estimated_completion else 'N/A'}\n"
                    f"📌 *Статус:* {service.status}\n"
                    "———————————————\n"
                )
            
            await update.message.reply_text(message_text, parse_mode="Markdown")
        else:
            # Логика для клиента
            orders = get_client_orders(user_id)
            if not orders:
                await update.message.reply_text("❌ У вас нет активных заказов.")
                return

            message_text = "📋 Ваши активные заказы:\n\n"
            for order in orders:
                total_rub, total_byn = convert_currency(order.price) if order.price else (None, None)
                message_text += f"🛒 *Заказ №{order.id}*\n"
                message_text += f"📅 *Дата создания:* {order.created_at.strftime('%d.%m.%y %H:%M') if order.created_at else 'N/A'}\n"
                message_text += f"⏳ *Дата завершения:* {order.estimated_completion.strftime('%d.%m.%y %H:%M') if order.estimated_completion else 'N/A'}\n"
                message_text += f"📌 *Статус:* {order.status}\n"
                message_text += f"💰 *Общая стоимость:* "
                message_text += f"{int(order.price)} USD | {int(total_rub)} RUB | {total_byn:.2f} BYN\n" if order.price else "N/A\n"
                
                # Добавляем информацию об услугах в заказе
                if order.order_services:
                    message_text += "\n📋 *Услуги в заказе:*\n"
                    for service in order.order_services:
                        price_rub, price_byn = convert_currency(service.service_price)
                        message_text += (
                            f"  • {service.service.name if service.service else 'N/A'} "
                            f"(x{service.quantity}) - {int(service.service_price)} USD | {int(price_rub)} RUB | {price_byn:.2f} BYN\n"
                            f"    Статус: {service.status}\n"
                        )
                
                message_text += "———————————————\n"

            await update.message.reply_text(message_text, parse_mode="Markdown")

async def process_client_message(update: Update, context: CallbackContext, state: dict) -> None:
    chat_id = update.message.chat_id
    text = update.message.text.strip()

    try:
        # Обработка отмены
        if text.lower() in ["отмена", "cancel"]:
            context.user_data.clear()
            await update.message.reply_text("✅ Добавление клиента отменено.")
            await start(update, context)
            return

        # Основная логика добавления клиента
        if state.get("action") == "add_client_username":
            # Валидация username
            if not text or len(text) < 3:
                await update.message.reply_text("❌ Username должен содержать минимум 3 символа. Попробуйте ещё раз:")
                return  # Состояние сохраняется для повторного ввода

            if not re.match(r'^[a-zA-Z0-9_]+$', text):
                await update.message.reply_text("❌ Username может содержать только буквы, цифры и подчёркивания. Попробуйте ещё раз:")
                return  # Состояние сохраняется

            try:
                client_id = create_client(text)
                if client_id:
                    await update.message.reply_text(f"✅ Клиент @{text} успешно зарегистрирован!")
                    # Только при успешном добавлении очищаем состояние
                    context.user_data.clear()
                    await start(update, context)
                else:
                    await update.message.reply_text("❌ Такой клиент уже существует. Введите другой username:")
                    # Состояние сохраняется для повторного ввода
                    
            except IntegrityError:
                await update.message.reply_text("❌ Ошибка: клиент с таким username уже существует. Введите другой username:")
                # Состояние сохраняется
                
            except Exception as e:
                await update.message.reply_text("⚠️ Произошла внутренняя ошибка. Попробуйте ещё раз:")
                # Состояние сохраняется для повторной попытки

    except Exception as e:
        await update.message.reply_text("⚠️ Произошла непредвиденная ошибка. Попробуйте ещё раз:")
        # Состояние не очищаем, чтобы пользователь мог повторить

async def process_executor_message(update: Update, context: CallbackContext, state: dict) -> None:
    chat_id = update.message.chat_id

    if state["action"] == "add_executor_username":
        user_states[chat_id]["username"] = update.message.text

        # **Кнопки выбора категории**
        keyboard = [
            [InlineKeyboardButton("Montage", callback_data="category_Montage")],
            [InlineKeyboardButton("Design", callback_data="category_Design")],
            [InlineKeyboardButton("IT", callback_data="category_IT")],
            [InlineKeyboardButton("Record", callback_data="category_Record")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Выберите категорию:", reply_markup=reply_markup)

        user_states[chat_id]["action"] = "add_executor_category"

async def process_service_message(update: Update, context: CallbackContext, state: dict) -> None:
    chat_id = update.message.chat_id

    if state["action"] == "add_service_name":
        user_states[chat_id]["name"] = update.message.text

        # **Кнопки выбора категории**
        keyboard = [
            [InlineKeyboardButton("Montage", callback_data="service_category_Montage")],
            [InlineKeyboardButton("Design", callback_data="service_category_Design")],
            [InlineKeyboardButton("IT", callback_data="service_category_IT")],
            [InlineKeyboardButton("Record", callback_data="service_category_Record")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Выберите категорию:", reply_markup=reply_markup)

        user_states[chat_id]["action"] = "add_service_category"

    elif state["action"] == "add_service_price":
        try:
            min_price = Decimal(update.message.text)  # Используем update.message.text
            user_states[chat_id]["min_price"] = min_price

            name = user_states[chat_id]["name"]
            category = user_states[chat_id]["category"]

            service_id = create_service(name, category, min_price)
            if service_id:
                await update.message.reply_text(f"✅ Услуга '{name}' добавлена в категорию '{category}' с ID {service_id}")
            else:
                await update.message.reply_text("❌ Ошибка при добавлении услуги.")
        except Exception as e:
            await update.message.reply_text(f"Ошибка при добавлении услуги: {e}")

async def process_order_message(update: Update, context: CallbackContext, state: dict) -> None:
    chat_id = update.message.chat_id

    if state["action"] == "add_order_client_username":
        client_username = update.message.text
        user_states[chat_id]["client_username"] = client_username  

        # **Устанавливаем статус по умолчанию ("В обработке")**
        order_status = "В обработке"
        client_username = user_states[chat_id]["client_username"]
        try:
            # Добавляем заказ в базу данных
            order_id = create_order(client_username, order_status)
            if order_id:
                await update.message.reply_text(f"✅ Заказ добавлен с ID {order_id}, статус: {order_status}")
            else:
                await update.message.reply_text("❌ Ошибка при добавлении заказа.")
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")
        finally:
            del user_states[chat_id]  # Очистить состояние после завершения процесса

async def process_service_to_order_message(update: Update, context: CallbackContext, state: dict) -> None:
    chat_id = update.message.chat_id

    try:
        if state["action"] == "add_service_to_order_order_id":
            order_id = update.message.text
            if not order_id.isdigit():  # Проверка, что это число
                await update.message.reply_text("❌ Ошибка: введите корректный ID заказа (число).")
                return

            user_states[chat_id]["order_id"] = int(order_id)

            # Получаем список всех услуг
            with SessionLocal() as session:
                services = session.query(Service).order_by(Service.category).all()

            if not services:
                await update.message.reply_text("❌ В базе нет доступных услуг.")
                return

            # Группируем услуги по категориям
            services_by_category = {}
            for service in services:
                if service.category not in services_by_category:
                    services_by_category[service.category] = []
                services_by_category[service.category].append(service)

            # Формируем текст для вывода
            message_text = "📋 *Доступные услуги:*\n"
            await view_services(update,context)

            # Запрашиваем ID услуги
            await update.message.reply_text("Введите ID услуги:")
            print(f"Текущее состояние пользователя {chat_id}: {state}")
            user_states[chat_id]["action"] = "add_service_to_order_service_id"  # Обновляем состояние
            print(f"Текущее состояние пользователя {chat_id}: {state}")

        elif state["action"] == "add_service_to_order_service_id":
            print(f"Текущее состояние пользователя {chat_id}: {state}")
            service_id = update.message.text
            if not service_id.isdigit():  # Проверка, что это число
                await update.message.reply_text("❌ Ошибка: введите корректный ID услуги (число).")
                return

            user_states[chat_id]["service_id"] = int(service_id)
            await update.message.reply_text("Введите количество:")
            user_states[chat_id]["action"] = "add_service_to_order_quantity"

        elif state["action"] == "add_service_to_order_quantity":
            quantity = update.message.text
            if not quantity.isdigit():  # Проверка, что это число
                await update.message.reply_text("❌ Ошибка: введите корректное количество (число).")
                return

            user_states[chat_id]["quantity"] = int(quantity)
            await update.message.reply_text("Введите цену услуги:")
            user_states[chat_id]["action"] = "add_service_to_order_price"

        elif state["action"] == "add_service_to_order_price":
            try:
                service_price = Decimal(update.message.text)
                user_states[chat_id]["service_price"] = service_price
                await update.message.reply_text("Введите срок выполнения (например, '2 дня', '1 неделя', '2023-12-31 18:00'):")
                user_states[chat_id]["action"] = "add_service_to_order_estimated_completion"
            except:
                await update.message.reply_text("❌ Ошибка: введите корректную цену (число).")
                return

        elif state["action"] == "add_service_to_order_estimated_completion":
            time_input = update.message.text.lower()
            moscow_offset = timedelta(hours=3)  # Смещение для московского времени (UTC+3)
            estimated_completion = None
            now = datetime.utcnow()

            try:
                if "день" in time_input or "дня" in time_input or "дней" in time_input:
                    days = int(time_input.split()[0])
                    estimated_completion = now + timedelta(days=days) + moscow_offset
                elif "неделя" in time_input or "недели" in time_input or "недель" in time_input:
                    weeks = int(time_input.split()[0])
                    estimated_completion = now + timedelta(weeks=weeks) + moscow_offset
                elif "месяц" in time_input or "месяца" in time_input or "месяцев" in time_input:
                    months = int(time_input.split()[0])
                    estimated_completion = now.replace(month=now.month + months) if now.month + months <= 12 else now.replace(year=now.year + (now.month + months) // 12, month=(now.month + months) % 12) + moscow_offset
                elif "час" in time_input or "часа" in time_input or "часов" in time_input:
                    hours = int(time_input.split()[0])
                    estimated_completion = now + timedelta(hours=hours) + moscow_offset
                else:
                    estimated_completion = datetime.strptime(time_input, "%Y-%m-%d %H:%M") + moscow_offset

                user_states[chat_id]["estimated_completion"] = estimated_completion

                # Сохраняем услугу в заказ
                order_id = user_states[chat_id]["order_id"]
                service_id = user_states[chat_id]["service_id"]
                quantity = user_states[chat_id]["quantity"]
                service_price = user_states[chat_id]["service_price"]

                service_to_order_id = create_service_to_order(order_id, service_id, quantity, service_price, estimated_completion)
                if service_to_order_id:
                    await update.message.reply_text(f"✅ Услуга добавлена в заказ с ID {service_to_order_id}, срок: {estimated_completion.strftime('%d.%m.%y %H:%M')}")
                else:
                    await update.message.reply_text("❌ Ошибка при добавлении услуги в заказ.")
                user_states[chat_id]["action"] = "add_service_to_order_end"
            

            except ValueError:
                await update.message.reply_text("❌ Ошибка в формате. Введите количество дней/недель/месяцев или дату (ГГГГ-ММ-ДД ЧЧ:ММ):")
                return

    except Exception as e:
        await update.message.reply_text(f"❌ Произошла ошибка: {e}")
    if chat_id in user_states and state["action"] == "add_service_to_order_end":
        del user_states[chat_id]

def create_client(username: str):
    with SessionLocal() as session:
        # Проверяем, существует ли клиент с таким Telegram username
        existing_client = session.query(Client).filter(Client.telegram_username == username).first()
        if existing_client:
            print(f"Клиент с Telegram username {username} уже существует.")
            return None

        try:
            # Создаем нового клиента
            new_client = Client(
                login=username,  # Логин = Telegram username
                telegram_username=username,
                telegram_id=None
            )
            new_client.set_password("FX@&9+9№exfXRc#e)wlo")  # Дефолтный пароль

            session.add(new_client)
            session.commit()
            session.refresh(new_client)
            print(f"Клиент с Telegram username {username} добавлен с ID {new_client.id}")
            return new_client.id

        except IntegrityError:
            session.rollback()
            print(f"Ошибка: Клиент с Telegram username '{username}' уже существует.")
            return None
       
async def add_client(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    print(f"[DEBUG] add_client called for chat {chat_id}")

    if chat_id in user_states:
        del user_states[chat_id]  

    # Обнуляем состояние перед началом процесса
    user_states[chat_id] = {"action": "add_client_username"}
    print(f"[DEBUG] Sending message to user {chat_id}")
    await update.message.reply_text("Введите Telegram username клиента:")

def create_executor(username: str, category: str, difficulty_level: int):
    with SessionLocal() as session:
        # Проверяем, существует ли исполнитель с таким Telegram username
        existing_executor = session.query(Executor).filter(Executor.telegram_username == username).first()
        if existing_executor:
            print(f"Исполнитель с Telegram username {username} уже существует.")
            return None

        try:
            # Создаем нового исполнителя
            new_executor = Executor(
                login=username,  # Логин = Telegram username
                telegram_username=username,
                telegram_id=None,
                category=category,
                difficulty_level=difficulty_level
            )
            new_executor.set_password("FX@&9+9№exfXRc#e)wlo")  # Дефолтный пароль

            session.add(new_executor)
            session.commit()
            session.refresh(new_executor)
            print(f"Исполнитель с Telegram username {username} добавлен с ID {new_executor.id}")
            return new_executor.id

        except IntegrityError:
            session.rollback()
            print(f"Ошибка: Исполнитель с Telegram username '{username}' уже существует.")
            return None
        
async def add_executor(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    if chat_id in user_states:
        del user_states[chat_id]  

    await update.message.reply_text("Введите Telegram username исполнителя:")
    user_states[chat_id] = {"action": "add_executor_username"}

def create_service(name: str, category: str, min_price: Decimal):
    with SessionLocal() as session:
        # Проверяем, существует ли услуга с таким названием и категорией
        existing_service = session.query(Service).filter(Service.name == name, Service.category == category).first()
        if existing_service:
            print(f"Услуга '{name}' в категории '{category}' уже существует.")
            return None

        try:
            # Создаем новую услугу
            new_service = Service(
                name=name,
                category=category,
                min_price=min_price
            )

            session.add(new_service)
            session.commit()
            session.refresh(new_service)
            print(f"Услуга '{name}' добавлена в категорию '{category}' с ID {new_service.id}")
            return new_service.id

        except IntegrityError:
            session.rollback()
            print(f"Ошибка: Услуга '{name}' в категории '{category}' уже существует.")
            return None

async def add_service(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    if chat_id in user_states:
        del user_states[chat_id]  

    await update.message.reply_text("Введите название услуги:")
    user_states[chat_id] = {"action": "add_service_name"}

def create_order(client_username: str, status: str):
    with SessionLocal() as session:
        moscow_offset = timedelta(hours=3)  # Смещение для московского времени (UTC+3)
        # Ищем клиента по telegram_username
        client = session.query(Client).filter(Client.telegram_username == client_username).first()
        if not client:
            print(f"Клиент с Telegram username '{client_username}' не найден.")
            return None

        try:
            # Создаем новый заказ с ID клиента
            new_order = OrderRequest(
                client_id=client.id,  # Используем ID клиента
                status=status
            )

            session.add(new_order)
            session.commit()
            session.refresh(new_order)
            print(f"✅ Заказ с ID {new_order.id} добавлен для клиента '{client_username}' (ID {client.id})")
            return new_order.id  # Возвращаем ID нового заказа

        except Exception as e:
            print(f"❌ Ошибка при добавлении заказа: {e}")
            session.rollback()
            return None

async def add_order(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    if chat_id in user_states:
        del user_states[chat_id]  

    await update.message.reply_text("Введите Telegram username клиента:")
    user_states[chat_id] = {"action": "add_order_client_username"}
    await process_order_message(update,context,user_states)

def create_service_to_order(order_id: int, service_id: int, quantity: int, service_price: Decimal, estimated_completion: datetime = None):
    with SessionLocal() as session:
        try:
            executor_id = None  # Для отсутствующего исполнителя
            new_order_service = OrderServices(
                order_id=order_id,
                service_id=service_id,
                quantity=quantity,
                service_price=service_price,
                executor_id=executor_id,  # Передаем None
                estimated_completion=estimated_completion,
                status="В обработке"  # Дефолтный статус
            )
            session.add(new_order_service)
            session.commit()
            session.refresh(new_order_service)
            print(f"Услуга {service_id} добавлена в заказ {order_id} с ID {new_order_service.id}")
            update_order_totals(order_id)

            return new_order_service.id
            
        except Exception as e:
            session.rollback()
            print(f"Ошибка при добавлении услуги в заказ: {e}")
            return None
        
async def add_service_to_order(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    await view_orders(update,context)
    await update.message.reply_text("Введите ID заказа для добавления услуги:")
    user_states[chat_id] = {"action":"add_service_to_order_order_id"}

def update_order_totals(order_id):
    with SessionLocal() as session:
        order = session.query(OrderRequest).filter(OrderRequest.id == order_id).first()
        if not order:
            return

        services = session.query(OrderServices).filter(OrderServices.order_id == order_id).all()

        total_price = sum(service.service_price for service in services)
        latest_completion = max((service.estimated_completion for service in services if service.estimated_completion), default=None)

        order.price = total_price
        order.estimated_completion = latest_completion
        session.commit()

async def start(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    if chat_id in user_states:
        del user_states[chat_id]
    # Получаем username пользователя
    user_id = update.message.from_user.username
    telegram_id = update.message.from_user.id

    check_and_update_user(user_id, telegram_id)
    print(f"[DEBUG] {user_id}")  # Логируем входящее сообщение

    # Проверяем, является ли пользователь исполнителем
    with SessionLocal() as session:
        executor = session.query(Executor).filter(Executor.telegram_username == user_id).first()
        is_special = user_id in SPECIAL_USERS
        if executor and is_special:
            # Комбинированное меню для спец.пользователей-исполнителей
            keyboard = [
                ["✉️ Связаться с клиентом"],
                ["🛫Отправить выполненный заказ","Посмотреть активные заказы"],
                ["Добавить", "Изменить", "Удалить", "Посмотреть"]
            ]
        elif is_special:
            keyboard = [["Добавить", "Изменить"],
                      ["Удалить", "Посмотреть"]]
        elif executor:
            # Меню для исполнителя
            keyboard = [
                ["✉️ Связаться с клиентом"],
                ["🛫 Отправить выполненный заказ"],
                ["🪬 Посмотреть активные заказы"]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await update.message.reply_text(
                "👋 Приветик! Чем займёмся сегодня?", 
                reply_markup=reply_markup
            )
        else:
            keyboard = [["🛎 Сделать заказ", "✉️ Связаться с исполнителем"],
                    ["🪬 Посмотреть активные заказы"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
       
    await update.message.reply_text("👋 Привет! Выберите действие:", reply_markup=reply_markup)

async def process_main_menu(update: Update, context: CallbackContext) -> None:
    text = update.message.text
    chat_id = update.message.chat_id

    if text == "Добавить":
        keyboard = [
            ["👤Добавить клиента👤", "👨‍💻Добавить исполнителя👨‍💻"],
            ["📄Добавить услугу📄", "📋Добавить заказ📋"],
            ["➕Добавить услугу в заказ➕", "↩️Назад↩️"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)
    elif text == "Изменить":
        keyboard = [
            ["Изменить услугу", "Изменить исполнителя"],
            ["Изменить заказ", "Изменить услугу в заказе"],
            ["↩️Назад↩️"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)
    elif text == "Удалить":
        keyboard = [
            ["Удалить клиента", "Удалить исполнителя"],
            ["Удалить услугу", "Удалить заказ"],
            ["Удалить услугу из заказа", "↩️Назад↩️"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)
    elif text == "Посмотреть":
        keyboard = [
            ["Посмотреть клиентов", "Посмотреть исполнителей"],
            ["Посмотреть услуги", "Посмотреть заказы", "Посмотреть услуги в заказах"],
            ["↩️Назад↩️"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("Выберите, что хотите посмотреть:", reply_markup=reply_markup)
    elif text == "↩️Назад↩️":
        keyboard = [
            ["Добавить", "Изменить", "Удалить", "Посмотреть"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("👋 Выберите действие:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("⚠️ Пожалуйста, выберите действие из меню.")

async def handle_create_order(update: Update, context: CallbackContext):
    # Получаем услуги, сгруппированные по категориям
    services_by_category = get_services_by_category()
    
    if not services_by_category:
        await update.message.reply_text("❌ В настоящее время нет доступных услуг.")
        return

    # Формируем сообщение
    message_text = (
        "🛎 *Чтобы сделать заказ, свяжитесь с менеджером:*\n"
        f"👉 @{MANAGER_CONTACT}\n\n"
        "📋 *Наши услуги:*\n\n"
    )

    # Добавляем услуги по категориям
    for category, services in services_by_category.items():
        message_text += f"*{category}:*\n"
        
        for service in services:
            price_rub, price_byn = convert_currency(service.min_price)
            message_text += (
                f"• {service.name} - {int(service.min_price)} USD "
                f"({int(price_rub)} RUB / {price_byn:.2f} BYN)\n"
            )
        
        message_text += "\n"

    # Добавляем подсказку
    message_text += (
        "\nПри обращении к менеджеру укажите:\n"
        "• Какие услуги вас интересуют\n"
        "• Желаемые сроки выполнения\n"
        "• Любые особые требования"
    )

    try:
        await update.message.reply_text(message_text, parse_mode="Markdown")
    except Exception as e:
        # Если возникла ошибка с Markdown, отправляем без форматирования
        await update.message.reply_text(
            message_text.replace('*', '').replace('_', ''),
            parse_mode=None
        )

async def delete_client_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    await view_clients(update, context)
    await update.message.reply_text("Введите ID клиента для удаления:")

    # Устанавливаем состояние для ожидания ввода ID клиента
    user_states[chat_id] = {"action": "delete_client_id"}

async def process_delete_client(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    state = user_states[chat_id]

    if state["action"] == "delete_client_id":
        try:
            client_id = int(update.message.text)
            user_states[chat_id]["client_id"] = client_id

            # Подтверждение удаления
            keyboard = [
                [InlineKeyboardButton("Да", callback_data="confirm_delete")],
                [InlineKeyboardButton("Нет", callback_data="cancel_delete")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Точно хотите удалить клиента?", reply_markup=reply_markup)

            user_states[chat_id]["action"] = "confirm_delete_client"
        except ValueError:
            await update.message.reply_text("❌ Ошибка: введите корректный ID клиента.")

    elif state["action"] == "confirm_delete_client":
        if update.callback_query.data == "confirm_delete":
            client_id = state["client_id"]
            delete_client(client_id)
            await update.message.reply_text(f"✅ Клиент с ID {client_id} удален.")
        else:
            await update.message.reply_text("❌ Удаление отменено.")

        # Очищаем состояние
        del user_states[chat_id]

async def delete_executor_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    await view_executors(update, context)
    await update.message.reply_text("Введите ID исполнителя для удаления:")

    # Устанавливаем состояние для ожидания ввода ID исполнителя
    user_states[chat_id] = {"action": "delete_executor_id"}

async def process_delete_executor(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    state = user_states[chat_id]

    if state["action"] == "delete_executor_id":
        try:
            executor_id = int(update.message.text)
            user_states[chat_id]["executor_id"] = executor_id

            # Подтверждение удаления
            keyboard = [
                [InlineKeyboardButton("Да", callback_data="confirm_delete")],
                [InlineKeyboardButton("Нет", callback_data="cancel_delete")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Точно хотите удалить исполнителя?", reply_markup=reply_markup)

            user_states[chat_id]["action"] = "confirm_delete_executor"
        except ValueError:
            await update.message.reply_text("❌ Ошибка: введите корректный ID исполнителя.")

    elif state["action"] == "confirm_delete_executor":
        if update.callback_query.data == "confirm_delete":
            executor_id = state["executor_id"]
            delete_executor(executor_id)
            await update.message.reply_text(f"✅ Исполнитель с ID {executor_id} удален.")
        else:
            await update.message.reply_text("❌ Удаление отменено.")

        # Очищаем состояние
        del user_states[chat_id]

async def delete_service_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    await view_services(update, context)
    await update.message.reply_text("Введите ID услуги для удаления:")

    # Устанавливаем состояние для ожидания ввода ID услуги
    user_states[chat_id] = {"action": "delete_service_id"}

async def process_delete_service(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    state = user_states[chat_id]

    if state["action"] == "delete_service_id":
        try:
            service_id = int(update.message.text)
            user_states[chat_id]["service_id"] = service_id

            # Подтверждение удаления
            keyboard = [
                [InlineKeyboardButton("Да", callback_data="confirm_delete")],
                [InlineKeyboardButton("Нет", callback_data="cancel_delete")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Точно хотите удалить услугу?", reply_markup=reply_markup)

            user_states[chat_id]["action"] = "confirm_delete_service"
        except ValueError:
            await update.message.reply_text("❌ Ошибка: введите корректный ID услуги.")

    elif state["action"] == "confirm_delete_service":
        if update.callback_query.data == "confirm_delete":
            service_id = state["service_id"]
            delete_service(service_id)
            await update.message.reply_text(f"✅ Услуга с ID {service_id} удалена.")
        else:
            await update.message.reply_text("❌ Удаление отменено.")

        # Очищаем состояние
        del user_states[chat_id]

async def delete_order_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    await view_orders(update, context)
    await update.message.reply_text("Введите ID заказа для удаления:")

    # Устанавливаем состояние для ожидания ввода ID заказа
    user_states[chat_id] = {"action": "delete_order_id"}

async def process_delete_order(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    state = user_states[chat_id]

    if state["action"] == "delete_order_id":
        try:
            order_id = int(update.message.text)
            user_states[chat_id]["order_id"] = order_id

            # Подтверждение удаления
            keyboard = [
                [InlineKeyboardButton("Да", callback_data="confirm_delete")],
                [InlineKeyboardButton("Нет", callback_data="cancel_delete")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Точно хотите удалить заказ?", reply_markup=reply_markup)

            user_states[chat_id]["action"] = "confirm_delete_order"
        except ValueError:
            await update.message.reply_text("❌ Ошибка: введите корректный ID заказа.")

    elif state["action"] == "confirm_delete_order":
        if update.callback_query.data == "confirm_delete":
            order_id = state["order_id"]
            delete_order(order_id)
            await update.message.reply_text(f"✅ Заказ с ID {order_id} удален.")
        else:
            await update.message.reply_text("❌ Удаление отменено.")

        # Очищаем состояние
        del user_states[chat_id]

async def delete_service_from_order_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    await view_orders(update, context)
    await update.message.reply_text("Введите ID заказа для удаления услуги:")

    # Устанавливаем состояние для ожидания ввода ID заказа
    user_states[chat_id] = {"action": "delete_service_from_order_id"}

async def process_delete_service_from_order(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    state = user_states[chat_id]

    if state["action"] == "delete_service_from_order_id":
        try:
            order_id = int(update.message.text)
            user_states[chat_id]["order_id"] = order_id
            # Получаем список услуг в заказе

            if(await view_services_in_order(update, context, order_id)==0):
                del user_states[chat_id]
                return
            await update.message.reply_text("Введите ID услуги в заказе для удаления:")

            user_states[chat_id]["action"] = "delete_service_from_order_service_id"
        except ValueError:
            await update.message.reply_text("❌ Ошибка: введите корректный ID заказа.")

    elif state["action"] == "delete_service_from_order_service_id":
        try:
            service_in_order_id = int(update.message.text)
            user_states[chat_id]["service_in_order_id"] = service_in_order_id

            # Подтверждение удаления
            keyboard = [
                [InlineKeyboardButton("Да", callback_data="confirm_delete")],
                [InlineKeyboardButton("Нет", callback_data="cancel_delete")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Точно хотите удалить услугу из заказа?", reply_markup=reply_markup)

            user_states[chat_id]["action"] = "confirm_delete_service_from_order"
        except ValueError:
            await update.message.reply_text("❌ Ошибка: введите корректный ID услуги в заказе.")

    elif state["action"] == "confirm_delete_service_from_order":
        if update.callback_query.data == "confirm_delete":
            service_in_order_id = state["service_in_order_id"]
            delete_service_from_order(service_in_order_id)
            await update.message.reply_text(f"✅ Услуга с ID {service_in_order_id} удалена из заказа.")
        else:
            await update.message.reply_text("❌ Удаление отменено.")

        # Очищаем состояние
        del user_states[chat_id]

async def process_edit_service(update: Update, context: CallbackContext, state: dict) -> None:
    chat_id = update.message.chat_id
    text = update.message.text

    if state["action"] == "edit_service_select":
        try:
            service_id = int(text)
            user_states[chat_id]["service_id"] = service_id

            # Предлагаем выбрать поле для изменения
            keyboard = [
                [InlineKeyboardButton("Изменить название", callback_data="edit_service_name")],
                [InlineKeyboardButton("Изменить категорию", callback_data="edit_service_category")],
                [InlineKeyboardButton("Изменить цену", callback_data="edit_service_price")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Выберите поле для изменения:", reply_markup=reply_markup)

            user_states[chat_id]["action"] = "edit_service_field"
        except ValueError:
            await update.message.reply_text("❌ Ошибка: введите корректный ID услуги.")
        return

    # Обработка изменения названия
    if state["action"] == "edit_service_name":
            new_name = text
            service_id = state["service_id"]
            if update_service_name(service_id, new_name):
                await update.message.reply_text(f"✅ Название услуги изменено на '{new_name}'.")
            else:
                await update.message.reply_text("❌ Ошибка при изменении названия услуги.")
            del user_states[chat_id]
            return

    # Обработка изменения цены
    if state["action"] == "edit_service_price":
            try:
                new_price = Decimal(text)
                service_id = state["service_id"]
                if update_service_price(service_id, new_price):
                    await update.message.reply_text(f"✅ Цена услуги изменена на {new_price} USD.")
                else:
                    await update.message.reply_text("❌ Ошибка при изменении цены услуги.")
                del user_states[chat_id]
            except ValueError:
                await update.message.reply_text("❌ Ошибка: введите корректную цену.")
            return
# Функции для работы с базой данных
def get_all_services():
    with SessionLocal() as session:
        return session.query(Service).all()

def get_all_executors():
    with SessionLocal() as session:
        return session.query(Executor).all()

def get_all_orders():
    with SessionLocal() as session:
        return session.query(OrderRequest).all()

def get_all_clients():
    with SessionLocal() as session:
        return session.query(Client).all()

def get_services_in_order(order_id):
    with SessionLocal() as session:
        return session.query(OrderServices).filter(OrderServices.order_id == order_id).all()

def delete_service(service_id):
    with SessionLocal() as session:
        service = session.query(Service).filter(Service.id == service_id).first()
        if service:
            session.delete(service)
            session.commit()

def delete_client(client_id):
    with SessionLocal() as session:
        client = session.query(Client).filter(Client.id == client_id).first()
        if client:
            session.delete(client)
            session.commit()

def delete_executor(executor_id):
    with SessionLocal() as session:
        executor = session.query(Executor).filter(Executor.id == executor_id).first()
        if executor:
            session.delete(executor)
            session.commit()

def delete_order(order_id):
    with SessionLocal() as session:
        order = session.query(OrderRequest).filter(OrderRequest.id == order_id).first()
        if order:
            session.delete(order)
            session.commit()

def delete_service_from_order(service_in_order_id):
    with SessionLocal() as session:
        service_in_order = session.query(OrderServices).filter(OrderServices.id == service_in_order_id).first()
        if service_in_order:
            session.delete(service_in_order)
            session.commit()

async def confirm_delete_client(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    text = update.message.text

    if chat_id in user_states and user_states[chat_id]["action"] == "delete_client_id":
        if text == "Да":
            client_id = user_states[chat_id]["client_id"]
            delete_client(client_id)
            await update.message.reply_text(f"✅ Клиент с ID {client_id} удален.")
        else:
            await update.message.reply_text("❌ Удаление отменено.")

        # Очищаем состояние
        del user_states[chat_id]
        
async def button_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()  # Обязательно подтверждаем нажатие

    chat_id = query.message.chat_id
    data = query.data
    print(f"[DEBUG] Получен callback_data: {data}")

    if data == "cancel_action":
        if chat_id in user_states:
            del user_states[chat_id]
        await query.message.reply_text("✅ Действие отменено")
        await start(update, context)
        return

    # Сначала проверяем кнопки модерации
    if data.startswith(('approve_', 'edit_', 'delete_')):
        try:
            action, receiver_telegram_id, message_id = data.split('_', 2)
            receiver_telegram_id = int(receiver_telegram_id)
            
            print(f"[MODERATION] Обработка: {action} для сообщения {message_id}")

            with SessionLocal() as session:
                # Явный запрос с commit/rollback
                try:
                    db_message = session.query(MessageModeration)\
                        .filter(MessageModeration.message_id == message_id)\
                        .first()

                    if not db_message:
                        print(f"[ERROR] Сообщение {message_id} не найдено в БД")
                        await query.edit_message_text(text="❌ Сообщение не найдено")
                        return

                    if db_message.processed:
                        print(f"[WARN] Сообщение {message_id} уже обработано")
                        await query.answer("Это сообщение уже обработано", show_alert=True)
                        return

                    # Обновляем сообщение
                    db_message.processed = True
                    if not db_message.moderator_messages:
                        db_message.moderator_messages = []
                    
                    db_message.moderator_messages.append({
                        'action': action,
                        'moderator_id': chat_id,
                        'timestamp': datetime.now().isoformat()
                    })

                    session.commit()
                    print(f"[DEBUG] Сообщение {message_id} помечено как обработанное")

                    # Обработка действий
                    if action == 'approve':
                        try:
                            # Получаем дополнительные данные для оформления
                            with SessionLocal() as session:
                                service = session.query(OrderServices).options(
                                    joinedload(OrderServices.service),
                                    joinedload(OrderServices.order)
                                ).filter(OrderServices.id == db_message.service_id).first()

                            if service:
                                order_id = service.order_id if service.order else "N/A"
                                service_name = service.service.name if service.service else "Неизвестная услуга"
            
                                # Форматируем сообщение в красивый вид
                                formatted_message = (
                                    f"📨 *Новое сообщение:*\n\n"
                                    f"📋 *Заказ:* №{order_id}\n"
                                    f"📦 *Услуга:* {service_name}\n\n"
                                    f"💬 *Текст сообщения:*\n{db_message.message_text}"
                                )
                            else:
                                formatted_message = db_message.message_text  # fallback, если не нашли данные

                            # Отправляем оформленное сообщение
                            await context.bot.send_message(
                                chat_id=receiver_telegram_id,
                                text=formatted_message,
                                parse_mode="Markdown"  # Включаем Markdown для форматирования
                            )
        
                            await query.edit_message_text("✅ Сообщение отправлено")
                            print(f"[DEBUG] Сообщение отправлено пользователю {receiver_telegram_id}")
                        except Exception as e:
                            print(f"[ERROR] Ошибка отправки: {str(e)}")
                            await query.edit_message_text("❌ Не удалось отправить сообщение")
                    elif action == 'delete':
                        await query.edit_message_text("❌ Сообщение удалено")

                    elif action == 'edit':
                        context.user_data['edit_message'] = {
                            'message_id': message_id,
                            'receiver_telegram_id': receiver_telegram_id,
                            'service_id': db_message.service_id,
                            'original_text': db_message.message_text
                        }
                        await query.edit_message_text("✏️ Введите новый текст:")

                    # Удаляем кнопки
                    try:
                        await query.message.edit_reply_markup(reply_markup=None)
                    except Exception as e:
                        print(f"[WARN] Не удалось убрать кнопки: {str(e)}")

                except Exception as db_error:
                    session.rollback()
                    print(f"[DB ERROR] Ошибка БД: {str(db_error)}")
                    await query.edit_message_text("❌ Ошибка базы данных")

        except Exception as e:
            print(f"[ERROR] Ошибка обработки: {str(e)}")
            await query.edit_message_text("❌ Ошибка обработки запроса")

        return  # Важно: выходим после обработки модерации
    
    elif chat_id in user_states:
        state = user_states[chat_id]

        # Обработка подтверждения удаления
        if state["action"] == "confirm_delete_client":
            if data == "confirm_delete":
                client_id = state["client_id"]
                delete_client(client_id)
                await query.message.reply_text(f"✅ Клиент с ID {client_id} удален.")
            else:
                await query.message.reply_text("❌ Удаление отменено.")
            del user_states[chat_id]

        elif state["action"] == "confirm_delete_executor":
            if data == "confirm_delete":
                executor_id = state["executor_id"]
                delete_executor(executor_id)
                await query.message.reply_text(f"✅ Исполнитель с ID {executor_id} удален.")
            else:
                await query.message.reply_text("❌ Удаление отменено.")
            del user_states[chat_id]

        elif state["action"] == "confirm_delete_service":
            if data == "confirm_delete":
                service_id = state["service_id"]
                delete_service(service_id)
                await query.message.reply_text(f"✅ Услуга с ID {service_id} удалена.")
            else:
                await query.message.reply_text("❌ Удаление отменено.")
            del user_states[chat_id]

        elif state["action"] == "confirm_delete_order":
            if data == "confirm_delete":
                order_id = state["order_id"]
                delete_order(order_id)
                await query.message.reply_text(f"✅ Заказ с ID {order_id} удален.")
            else:
                await query.message.reply_text("❌ Удаление отменено.")
            del user_states[chat_id]

        elif state["action"] == "confirm_delete_service_from_order":
            if data == "confirm_delete":
                service_in_order_id = state["service_in_order_id"]
                delete_service_from_order(service_in_order_id)
                await query.message.reply_text(f"✅ Услуга с ID {service_in_order_id} удалена из заказа.")
            else:
                await query.message.reply_text("❌ Удаление отменено.")
            del user_states[chat_id]

        # Обработка добавления
        elif state["action"] == "add_executor_category":
            category = data.split("_")[1]  # Получаем категорию из callback_data
            user_states[chat_id]["category"] = category

            # Показываем кнопки выбора сложности
            keyboard = [
                [InlineKeyboardButton("Лёгкая", callback_data="difficulty_1")],
                [InlineKeyboardButton("Средняя", callback_data="difficulty_2")],
                [InlineKeyboardButton("Сложная", callback_data="difficulty_3")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_text("Выберите сложность:", reply_markup=reply_markup)

            user_states[chat_id]["action"] = "add_executor_difficulty"

        elif state["action"] == "add_executor_difficulty":
            difficulty_level = int(data.split("_")[1])  # Получаем уровень сложности
            user_states[chat_id]["difficulty_level"] = difficulty_level

            username = user_states[chat_id]["username"]
            category = user_states[chat_id]["category"]

            try:
                executor_id = create_executor(username, category, difficulty_level)
                if executor_id:
                    await query.message.reply_text(f"✅ Исполнитель {username} зарегистрирован!")
                else:
                    await query.message.reply_text("❌ Ошибка при регистрации исполнителя.")
            except Exception as e:
                await query.message.reply_text(f"Ошибка: {e}")
            finally:
                del user_states[chat_id]

        elif state["action"] == "add_service_category":
            category = data.split("_")[2]  # Получаем категорию из callback_data
            user_states[chat_id]["category"] = category

            await query.message.reply_text("Введите минимальную цену услуги:")
            user_states[chat_id]["action"] = "add_service_price"
        
        elif state["action"] == "edit_service_category":
            category = data.split("_")[2]  # Получаем категорию из callback_data
            user_states[chat_id]["category"] = category

            user_states[chat_id]["action"] = "edit_service_value"

        elif state["action"] == "add_service_price":
            try:
                min_price = Decimal(query.message.text)
                user_states[chat_id]["min_price"] = min_price

                name = user_states[chat_id]["name"]
                category = user_states[chat_id]["category"]

                service_id = create_service(name, category, min_price)
                if service_id:
                    await query.message.reply_text(f"✅ Услуга '{name}' добавлена в категорию '{category}' с ID {service_id}")
                else:
                    await query.message.reply_text("❌ Ошибка при добавлении услуги.")
            except Exception as e:
                await query.message.reply_text(f"Ошибка при добавлении услуги: {e}")
            finally:
                del user_states[chat_id]

        # Обработка изменения категории
        if state["action"] == "edit_service_field" and data == "edit_service_category":
            # Показываем кнопки с категориями
            keyboard = [
                [InlineKeyboardButton("Montage", callback_data="edit_service_category_Montage")],
                [InlineKeyboardButton("Design", callback_data="edit_service_category_Design")],
                [InlineKeyboardButton("IT", callback_data="edit_service_category_IT")],
                [InlineKeyboardButton("Record", callback_data="edit_service_category_Record")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_text("Выберите новую категорию:", reply_markup=reply_markup)
            user_states[chat_id]["action"] = "edit_service_category_"
            return

        # Обработка изменения цены
        if state["action"] == "edit_service_field" and data == "edit_service_price":
            await query.message.reply_text("Введите новую цену услуги:")
            user_states[chat_id]["action"] = "edit_service_price"
            return
        
        # Обработка выбора категории
        if state["action"] == "edit_service_category_":
            # Получаем новую категорию из callback_data
            new_category = data.split("_")[-1]  # Например, "Montage", "Design" и т.д.
            service_id = state["service_id"]

            # Обновляем категорию в базе данных
            if update_service_category(service_id, new_category):
                await query.message.reply_text(f"✅ Категория услуги изменена на '{new_category}'.")
            else:
                await query.message.reply_text("❌ Ошибка при изменении категории услуги.")

            # Очищаем состояние
            del user_states[chat_id]
            return
        
        # Обработка изменения исполнителя
        elif state["action"] == "edit_executor_field":
            if data == "edit_executor_username":
                await query.message.reply_text("Введите новый username исполнителя:")
                user_states[chat_id]["action"] = "edit_executor_username"
            elif data == "edit_executor_category":
                keyboard = [
                    [InlineKeyboardButton("Montage", callback_data="edit_executor_category_Montage")],
                    [InlineKeyboardButton("Design", callback_data="edit_executor_category_Design")],
                    [InlineKeyboardButton("IT", callback_data="edit_executor_category_IT")],
                    [InlineKeyboardButton("Record", callback_data="edit_executor_category_Record")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.reply_text("Выберите новую категорию:", reply_markup=reply_markup)
                user_states[chat_id]["action"] = "edit_executor_category_"
            elif data == "edit_executor_difficulty":
                keyboard = [
                    [InlineKeyboardButton("Лёгкая", callback_data="edit_executor_difficulty_1")],
                    [InlineKeyboardButton("Средняя", callback_data="edit_executor_difficulty_2")],
                    [InlineKeyboardButton("Сложная", callback_data="edit_executor_difficulty_3")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.reply_text("Выберите новую сложность:", reply_markup=reply_markup)
                user_states[chat_id]["action"] = "edit_executor_difficulty_"

        # Обработка изменения категории исполнителя
        elif state["action"] == "edit_executor_category_":
            new_category = data.split("_")[-1]
            executor_id = state["executor_id"]
            if update_executor_category(executor_id, new_category):
                await query.message.reply_text(f"✅ Категория исполнителя изменена на '{new_category}'")
            else:
                await query.message.reply_text("❌ Ошибка при изменении категории исполнителя.")
            del user_states[chat_id]

        # Обработка изменения сложности исполнителя
        elif state["action"] == "edit_executor_difficulty_":
            new_difficulty = int(data.split("_")[-1])
            executor_id = state["executor_id"]
            if update_executor_difficulty(executor_id, new_difficulty):
                await query.message.reply_text(f"✅ Сложность исполнителя изменена на {new_difficulty}")
            else:
                await query.message.reply_text("❌ Ошибка при изменении сложности исполнителя.")
            del user_states[chat_id]

        # Обработка изменения заказа
        elif state["action"] == "edit_order_field":
            if data == "edit_order_client":
                await query.message.reply_text("Введите новый username клиента:")
                user_states[chat_id]["action"] = "edit_order_client"
            elif data == "edit_order_completion":
                await query.message.reply_text("Введите новое время завершения (например, '2 дня', '1 неделя', '2023-12-31 18:00'):")
                user_states[chat_id]["action"] = "edit_order_completion"
            elif data == "edit_order_status":
                keyboard = [
                    [InlineKeyboardButton("В обработке", callback_data="edit_order_status_processing")],
                    [InlineKeyboardButton("Выполняется", callback_data="edit_order_status_in_progress")],
                    [InlineKeyboardButton("Ожидание правок", callback_data="edit_order_status_waiting")],
                    [InlineKeyboardButton("Завершён", callback_data="edit_order_status_completed")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.reply_text("Выберите новый статус:", reply_markup=reply_markup)
                user_states[chat_id]["action"] = "edit_order_status_"

        # Обработка выбора статуса заказа
        elif state["action"] == "edit_order_status_":
            status_map = {
                "processing": "В обработке",
                "in_progress": "Выполняется",
                "waiting": "Ожидание правок",
                "completed": "Завершён"
            }
            new_status = status_map[data.split("_")[-1]]
            order_id = state["order_id"]
            if update_order_status(order_id, new_status):
                await query.message.reply_text(f"✅ Статус заказа изменен на '{new_status}'")
            else:
                await query.message.reply_text("❌ Ошибка при изменении статуса заказа.")
            del user_states[chat_id]

                # Обработка изменения услуги в заказе
        elif state['action'] == 'edit_service_in_order_field':
            if data == 'edit_service_in_order_service':
                await view_services(update, context)
                await query.message.reply_text('Введите ID новой услуги:')
                user_states[chat_id]['action'] = 'edit_service_in_order_service_select'
            elif data == 'edit_service_in_order_quantity':
                await query.message.reply_text('Введите новое количество:')
                user_states[chat_id]['action'] = 'edit_service_in_order_quantity'
            elif data == 'edit_service_in_order_price':
                await query.message.reply_text('Введите новую цену:')
                user_states[chat_id]['action'] = 'edit_service_in_order_price'
            elif data == 'edit_service_in_order_executor':
                await view_executors(update, context)
                await query.message.reply_text('Введите ID нового исполнителя:')
                user_states[chat_id]['action'] = 'edit_service_in_order_executor'
            elif data == 'edit_service_in_order_completion':
                await query.message.reply_text('Введите новую дату завершения (например, "2 дня", "1 неделя", "2023-12-31 18:00"):')
                user_states[chat_id]['action'] = 'edit_service_in_order_completion'
            elif data == 'edit_service_in_order_status':
                keyboard = [
                    [InlineKeyboardButton('В обработке', callback_data='edit_service_in_order_status_processing')],
                    [InlineKeyboardButton('Выполняется', callback_data='edit_service_in_order_status_in_progress')],
                    [InlineKeyboardButton('Ожидание правок', callback_data='edit_service_in_order_status_waiting')],
                    [InlineKeyboardButton('Завершён', callback_data='edit_service_in_order_status_completed')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.reply_text('Выберите новый статус:', reply_markup=reply_markup)
                user_states[chat_id]['action'] = 'edit_service_in_order_status_'


        # Обработка выбора статуса услуги в заказе
        elif state["action"] == "edit_service_in_order_status_":
            status_map = {
                "processing": "В обработке",
                "in_progress": "Выполняется",
                "waiting": "Ожидание правок",
                "completed": "Завершён"
            }
            new_status = status_map[data.split("_")[-1]]
            service_id = state["service_id"]
            if update_service_in_order_status(service_id, new_status):
                await query.message.reply_text(f"✅ Статус услуги изменен на '{new_status}'")
            else:
                await query.message.reply_text("❌ Ошибка при изменении статуса услуги.")
            del user_states[chat_id]

        elif state["action"] == "confirm_send_message":
            if data == "confirm_send":
                try:
                    order_id = state["order_id"]
                    service_id = state["service_id"]
                    message_text = state["message"]
                    sender_username = update.effective_user.username
                    client_username = get_client_username_by_service(service_id)

                    with SessionLocal() as session:
                        service = session.query(OrderServices).options(joinedload(OrderServices.order)).filter(OrderServices.id == service_id).first()
                        if service and service.order and service.order.client:
                            client_telegram_id = service.order.client.telegram_id
                            moderation_entry = MessageModeration(
                                message_id=str(uuid.uuid4()),
                                message_text=message_text,
                                receiver_telegram_id=client_telegram_id,
                                receiver_username=client_username,
                                receiver_type="client",
                                sender_username=sender_username,
                                service_id=service_id,
                                created_at=datetime.now(),
                                processed=False,
                            )
                            session.add(moderation_entry)
                            session.commit()

                            if is_suspicious(message_text):
                                await send_to_manager(update, context, message_text, client_username, "client")
                                await query.message.reply_text("🔎 Сообщение отправлено на проверку менеджеру.")
                            else:
                                await send_message(context, client_telegram_id, message_text)
                                await query.message.reply_text("✅ Сообщение отправлено клиенту.")
                        else:
                            await query.message.reply_text("❌ Не удалось найти клиента.")
                except Exception as e:
                    await query.message.reply_text(f"❌ Ошибка при отправке сообщения: {e}")
            else:
                await query.message.reply_text("❌ Отправка сообщения отменена.")
            
            await start(update, context)
            del user_states[chat_id]
        
async def handle_edited_message(update: Update, context: CallbackContext):
    if 'edit_message' not in context.user_data:
        return
    
    edit_data = context.user_data['edit_message']
    new_text = update.message.text
    
    with SessionLocal() as session:
        # Обновляем сообщение в базе данных
        db_message = session.query(MessageModeration).get(edit_data['db_message_id'])
        if db_message:
            # Обновляем текст сообщения
            db_message.message_text = new_text
            
            # Добавляем запись о редактировании
            if db_message.moderator_messages is None:
                db_message.moderator_messages = []
                
            db_message.moderator_messages.append({
                'action': 'edited',
                'new_text': new_text,
                'timestamp': datetime.now().isoformat(),
                'moderator_id': update.effective_user.id
            })
            
            session.commit()
            
            # Отправляем новую версию получателю
            try:
                await context.bot.send_message(
                    chat_id=edit_data['receiver_telegram_id'],
                    text=new_text
                )
                
                # Удаляем сообщение с кнопками у модератора
                try:
                    await context.bot.delete_message(
                        chat_id=edit_data['moderator_chat_id'],
                        message_id=edit_data['moderator_message_id']
                    )
                except Exception as e:
                    print(f"[ERROR] Ошибка при удалении сообщения модератора: {e}")
                
                await update.message.reply_text("✅ Сообщение отредактировано и отправлено.")
            except Exception as e:
                await update.message.reply_text(f"❌ Не удалось отправить сообщение: {e}")
        
        del context.user_data['edit_message']

async def process_edit_executor(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    text = update.message.text

    if chat_id not in user_states:
        return

    state = user_states[chat_id]

    if state['action'] == 'edit_executor_select':
        try:
            executor_id = int(text)
            user_states[chat_id]['executor_id'] = executor_id

            keyboard = [
                [InlineKeyboardButton('Изменить username', callback_data='edit_executor_username')],
                [InlineKeyboardButton('Изменить категорию', callback_data='edit_executor_category')],
                [InlineKeyboardButton('Изменить сложность', callback_data='edit_executor_difficulty')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text('Выберите, что хотите изменить:', reply_markup=reply_markup)
            user_states[chat_id]['action'] = 'edit_executor_field'
        except ValueError:
            await update.message.reply_text('❌ Ошибка: введите корректный ID исполнителя.')

    elif state['action'] == 'edit_executor_username':
        executor_id = state['executor_id']
        new_username = text
        if update_executor_username(executor_id, new_username):
            await update.message.reply_text(f'✅ Username исполнителя изменен на {new_username}')
        else:
            await update.message.reply_text('❌ Ошибка при изменении username исполнителя')
        del user_states[chat_id]

    elif state['action'] == 'edit_executor_difficulty':
        executor_id = state['executor_id']
        try:
            new_difficulty = int(text)
            if 1 <= new_difficulty <= 3:
                if update_executor_difficulty(executor_id, new_difficulty):
                    await update.message.reply_text(f'✅ Сложность исполнителя изменена на {new_difficulty}')
                else:
                    await update.message.reply_text('❌ Ошибка при изменении сложности исполнителя')
            else:
                await update.message.reply_text('❌ Сложность должна быть от 1 до 3')
            del user_states[chat_id]
        except ValueError:
            await update.message.reply_text('❌ Введите корректное число от 1 до 3')

def update_executor_username(executor_id: int, new_username: str) -> bool:
    with SessionLocal() as session:
        executor = session.query(Executor).filter(Executor.id == executor_id).first()
        if executor:
            executor.telegram_username = new_username
            executor.login = new_username
            session.commit()
            return True
        return False

def update_executor_category(executor_id: int, new_category: str) -> bool:
    with SessionLocal() as session:
        executor = session.query(Executor).filter(Executor.id == executor_id).first()
        if executor:
            executor.category = new_category
            session.commit()
            return True
        return False

def update_executor_difficulty(executor_id: int, new_difficulty: int) -> bool:
    with SessionLocal() as session:
        executor = session.query(Executor).filter(Executor.id == executor_id).first()
        if executor:
            executor.difficulty_level = new_difficulty
            session.commit()
            return True
        return False

# Функции для изменения заказа
async def edit_order_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    await view_orders(update, context)
    await update.message.reply_text('Введите ID заказа для изменения:')
    user_states[chat_id] = {'action': 'edit_order_select'}

async def process_edit_order(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    text = update.message.text

    if chat_id not in user_states:
        return

    state = user_states[chat_id]

    if state['action'] == 'edit_order_select':
        try:
            order_id = int(text)
            user_states[chat_id]['order_id'] = order_id

            keyboard = [
                [InlineKeyboardButton('Изменить клиента', callback_data='edit_order_client')],
                [InlineKeyboardButton('Изменить время завершения', callback_data='edit_order_completion')],
                [InlineKeyboardButton('Изменить статус', callback_data='edit_order_status')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text('Выберите, что хотите изменить:', reply_markup=reply_markup)
            user_states[chat_id]['action'] = 'edit_order_field'
        except ValueError:
            await update.message.reply_text('❌ Ошибка: введите корректный ID заказа.')

    elif state['action'] == 'edit_order_client':
        order_id = state['order_id']
        new_username = text
        if update_order_client(order_id, new_username):
            await update.message.reply_text(f'✅ Клиент заказа изменен на {new_username}')
        else:
            await update.message.reply_text('❌ Ошибка при изменении клиента заказа')
        del user_states[chat_id]

    elif state['action'] == 'edit_order_completion':
        order_id = state['order_id']
        time_input = text.lower()
        moscow_offset = timedelta(hours=3)
        estimated_completion = None
        now = datetime.utcnow()

        try:
            if 'день' in time_input or 'дня' in time_input or 'дней' in time_input:
                days = int(time_input.split()[0])
                estimated_completion = now + timedelta(days=days) + moscow_offset
            elif 'неделя' in time_input or 'недели' in time_input or 'недель' in time_input:
                weeks = int(time_input.split()[0])
                estimated_completion = now + timedelta(weeks=weeks) + moscow_offset
            elif 'месяц' in time_input or 'месяца' in time_input or 'месяцев' in time_input:
                months = int(time_input.split()[0])
                estimated_completion = now.replace(month=now.month + months) if now.month + months <= 12 else now.replace(year=now.year + (now.month + months) // 12, month=(now.month + months) % 12) + moscow_offset
            elif 'час' in time_input or 'часа' in time_input or 'часов' in time_input:
                hours = int(time_input.split()[0])
                estimated_completion = now + timedelta(hours=hours) + moscow_offset
            else:
                estimated_completion = datetime.strptime(time_input, '%Y-%m-%d %H:%M') + moscow_offset

            if update_order_completion(order_id, estimated_completion):
                await update.message.reply_text(f"✅ Время завершения заказа изменено на {estimated_completion.strftime('%d.%m.%y %H:%M')}")
            else:
                await update.message.reply_text('❌ Ошибка при изменении времени завершения заказа')
            del user_states[chat_id]
        except ValueError:
            await update.message.reply_text('❌ Ошибка в формате. Введите количество дней/недель/месяцев или дату (ГГГГ-ММ-ДД ЧЧ:ММ):')

def update_order_client(order_id: int, new_username: str) -> bool:
    with SessionLocal() as session:
        client = session.query(Client).filter(Client.telegram_username == new_username).first()
        if not client:
            return False
        
        order = session.query(OrderRequest).filter(OrderRequest.id == order_id).first()
        if order:
            order.client_id = client.id
            session.commit()
            return True
        return False

def update_order_completion(order_id: int, new_completion: datetime) -> bool:
    with SessionLocal() as session:
        order = session.query(OrderRequest).filter(OrderRequest.id == order_id).first()
        if order:
            order.estimated_completion = new_completion
            session.commit()
            return True
        return False

def update_order_status(order_id: int, new_status: str) -> bool:
    with SessionLocal() as session:
        order = session.query(OrderRequest).filter(OrderRequest.id == order_id).first()
        if order:
            order.status = new_status
            session.commit()
            return True
        return False

# Функции для изменения услуги в заказе
async def edit_service_in_order_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    await view_orders(update, context)
    await update.message.reply_text("Введите ID заказа:")
    user_states[chat_id] = {"action": "edit_service_in_order_select_order"}

async def process_edit_service_in_order(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    text = update.message.text
    state = user_states[chat_id]

    if state["action"] == "edit_service_in_order_select_order":
        try:
            order_id = int(text)
            user_states[chat_id]["order_id"] = order_id
            if await view_services_in_order(update, context, order_id) == 0:
                del user_states[chat_id]
                return
            await update.message.reply_text("Введите ID услуги в заказе для изменения:")
            user_states[chat_id]["action"] = "edit_service_in_order_select_service"
        except ValueError:
            await update.message.reply_text("❌ Ошибка: введите корректный ID заказа.")
            return

    elif state["action"] == "edit_service_in_order_select_service":
        try:
            service_id = int(text)
            user_states[chat_id]["service_id"] = service_id
            
            # Показываем кнопки с вариантами изменения
            keyboard = [
                [InlineKeyboardButton("Изменить услугу", callback_data="edit_service_in_order_service")],
                [InlineKeyboardButton("Изменить количество", callback_data="edit_service_in_order_quantity")],
                [InlineKeyboardButton("Изменить цену", callback_data="edit_service_in_order_price")],
                [InlineKeyboardButton("Изменить исполнителя", callback_data="edit_service_in_order_executor")],
                [InlineKeyboardButton("Изменить дату завершения", callback_data="edit_service_in_order_completion")],
                [InlineKeyboardButton("Изменить статус", callback_data="edit_service_in_order_status")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Выберите, что хотите изменить:", reply_markup=reply_markup)
            user_states[chat_id]["action"] = "edit_service_in_order_field"
        except ValueError:
            await update.message.reply_text("❌ Ошибка: введите корректный ID услуги.")
            return

    elif state["action"] == "edit_service_in_order_service_select":
        try:
            new_service_id = int(text)
            service_id = state["service_id"]
            if update_service_in_order_service(service_id, new_service_id):
                await update.message.reply_text("✅ Услуга успешно изменена.")
            else:
                await update.message.reply_text("❌ Ошибка при изменении услуги.")
            del user_states[chat_id]
        except ValueError:
            await update.message.reply_text("❌ Ошибка: введите корректный ID услуги.")
            return

    elif state["action"] == "edit_service_in_order_quantity":
        try:
            new_quantity = int(text)
            service_id = state["service_id"]
            if update_service_in_order_quantity(service_id, new_quantity):
                await update.message.reply_text(f"✅ Количество изменено на {new_quantity}.")
            else:
                await update.message.reply_text("❌ Ошибка при изменении количества.")
            del user_states[chat_id]
        except ValueError:
            await update.message.reply_text("❌ Ошибка: введите корректное количество.")
            return

    elif state["action"] == "edit_service_in_order_price":
        try:
            new_price = Decimal(text)
            service_id = state["service_id"]
            if update_service_in_order_price(service_id, new_price):
                await update.message.reply_text(f"✅ Цена изменена на {new_price}.")
            else:
                await update.message.reply_text("❌ Ошибка при изменении цены.")
            del user_states[chat_id]
        except ValueError:
            await update.message.reply_text("❌ Ошибка: введите корректную цену.")
            return

    elif state["action"] == "edit_service_in_order_executor":
        try:
            new_executor_id = int(text)
            service_id = state["service_id"]
            if update_service_in_order_executor(service_id, new_executor_id):
                await update.message.reply_text(f"✅ Исполнитель изменен.")
            else:
                await update.message.reply_text("❌ Ошибка при изменении исполнителя.")
            del user_states[chat_id]
        except ValueError:
            await update.message.reply_text("❌ Ошибка: введите корректный ID исполнителя.")
            return

def update_service_in_order_service(service_in_order_id: int, new_service_id: int) -> bool:
    with SessionLocal() as session:
        service_in_order = session.query(OrderServices).filter(OrderServices.id == service_in_order_id).first()
        if service_in_order:
            service_in_order.service_id = new_service_id
            session.commit()
            return True
        return False

def update_service_in_order_quantity(service_in_order_id: int, new_quantity: int) -> bool:
    with SessionLocal() as session:
        service_in_order = session.query(OrderServices).filter(OrderServices.id == service_in_order_id).first()
        if service_in_order:
            service_in_order.quantity = new_quantity
            session.commit()
            return True
        return False

def update_service_in_order_price(service_in_order_id: int, new_price: Decimal) -> bool:
    with SessionLocal() as session:
        service_in_order = session.query(OrderServices).filter(OrderServices.id == service_in_order_id).first()
        if service_in_order:
            service_in_order.service_price = new_price
            session.commit()
            return True
        return False

def update_service_in_order_executor(service_in_order_id: int, new_executor_id: int) -> bool:
    with SessionLocal() as session:
        service_in_order = session.query(OrderServices).filter(OrderServices.id == service_in_order_id).first()
        if service_in_order:
            service_in_order.executor_id = new_executor_id
            session.commit()
            return True
        return False

def update_service_in_order_completion(service_in_order_id: int, new_completion: datetime) -> bool:
    with SessionLocal() as session:
        service_in_order = session.query(OrderServices).filter(OrderServices.id == service_in_order_id).first()
        if service_in_order:
            service_in_order.estimated_completion = new_completion
            session.commit()
            return True
        return False

def update_service_in_order_status(service_in_order_id: int, new_status: str) -> bool:
    with SessionLocal() as session:
        service_in_order = session.query(OrderServices).filter(OrderServices.id == service_in_order_id).first()
        if service_in_order:
            service_in_order.status = new_status
            session.commit()
            return True
        return False
    
async def send(update: Update, text: str, **kwargs) -> None:
    """Отправляет сообщение в зависимости от типа update"""
    if update.message:
        await update.message.reply_text(text, **kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, **kwargs)

async def view_clients(update: Update, context: CallbackContext) -> None:
    clients = get_all_clients()
    if not clients:
        await update.message.reply_text("Нет зарегистрированных клиентов.")
        return

    message_text = "📋 *Список клиентов:*\n\n"
    message_text += "```\n"  # Начинаем блок кода для моноширинного текста
    message_text += "| ID | Telegram username  |\n"
    message_text += "|----|--------------------|\n"
    for client in clients:
        if client.telegram_username is None:
            continue
        message_text += f"| {client.id:2} | {client.telegram_username:18} |\n"
    message_text += "```"  # Закрываем блок кода

    await send(update, message_text, parse_mode="Markdown")

async def view_executors(update: Update, context: CallbackContext) -> None:
    executors = get_all_executors()
    if not executors:
        await update.message.reply_text("Нет зарегистрированных исполнителей.")
        return

    message_text = "📋 *Список исполнителей:*\n\n"
    message_text += "```\n"
    message_text += "| ID | Telegram username | Категория       | Уровень сложности  |\n"
    message_text += "|----|-------------------|-----------------|--------------------|\n"
    for executor in executors:
        username = executor.telegram_username or "N/A"
        category = executor.category or "N/A"
        difficulty = executor.difficulty_level or "N/A"
        message_text += f"| {executor.id:2} | {username:17} | {category:15} | {difficulty:18} |\n"
    message_text += "```"

    await send(update, message_text, parse_mode="Markdown")

async def view_services(update: Update, context: CallbackContext) -> None:
    services = get_all_services()
    if not services:
        await update.message.reply_text("Нет доступных услуг.")
        return
    
    # Группируем услуги по категориям
    services_by_category = {}
    for service in services:
        if service.category not in services_by_category:
            services_by_category[service.category] = []
        services_by_category[service.category].append(service)

    message_text = "📋 *Список услуг:*\n\n"
    message_text += "```\n"  # Начинаем блок кода для моноширинного текста
    message_text += "| ID |            Название            | Категория       |  USD   |  RUB   |   BYN   |\n"
    message_text += "|----|--------------------------------|-----------------|--------|--------|---------|\n"
    for service in services:
        # Конвертируем валюту
        price_rub, price_byn = convert_currency(service.min_price)

        # Формируем строку таблицы
        message_text += (
            f"| {service.id:2} | {service.name:30} | {service.category:15} | "
            f"{int(service.min_price):6} | {int(price_rub):6} | {price_byn:7.2f} |\n"
        )
    message_text += "```"  # Закрываем блок кода

    await send(update, message_text, parse_mode="Markdown")

async def view_orders(update: Update, context: CallbackContext) -> None:
    with SessionLocal() as session:
        orders = session.query(OrderRequest).options(joinedload(OrderRequest.client)).order_by(OrderRequest.id).all()
    
    if not orders:
        await update.message.reply_text("Нет активных заказов.")
        return

    message_text = "📋 *Список заказов:*\n\n"
    message_text += "```\n"
    message_text += "| ID |      Клиент      |  USD  |  RUB   |   BYN   |    Статус    | Дата создания  | Дата завершения |\n"
    message_text += "|----|------------------|-------|--------|---------|--------------|----------------|-----------------|\n"
    
    for order in orders:
        # Handle possible None values
        client_username = order.client.telegram_username if order.client and order.client.telegram_username else "N/A"
        price = order.price if order.price is not None else "N/A"
        price_rub, price_byn = convert_currency(order.price) if order.price else ("N/A", "N/A")
        status = order.status if order.status else "N/A"
        created_at = order.created_at.strftime('%d.%m.%y %H:%M') if order.created_at else "N/A"
        completion = order.estimated_completion.strftime('%d.%m.%y %H:%M') if order.estimated_completion else "N/A"
        
        # Format the row with proper handling of "N/A" values
        message_text += (
            f"| {order.id:2} | {client_username[:16]:16} | "
            f"{price if price == 'N/A' else int(price):5} | "
            f"{price_rub if price_rub == 'N/A' else int(price_rub):6} | "
            f"{price_byn if price_byn == 'N/A' else f'{price_byn:.2f}':7} | "
            f"{status[:12]:12} | {created_at[:14]:14} | {completion[:15]:15} |\n"
        )
    
    message_text += "```"
    await send(update, message_text, parse_mode="Markdown")

async def view_services_in_orders(update: Update, context: CallbackContext) -> None:
    with SessionLocal() as session:
        services_in_order = (
            session.query(OrderServices)
            .join(OrderRequest, OrderRequest.id == OrderServices.order_id)
            .join(Service, Service.id == OrderServices.service_id)
            .options(joinedload(OrderServices.service), joinedload(OrderServices.executor))
            .order_by(OrderServices.order_id)
            .all()
        )

    if not services_in_order:
        await update.message.reply_text("Нет услуг в заказах.")
        return

    message_text = "📋 *Список услуг в заказах:*\n\n"
    
    for service in services_in_order:
        # Получаем данные, обрабатывая возможные None значения
        service_name = service.service.name if service.service else "Неизвестная услуга"
        order_id = service.order_id
        service_in_order_id = service.id  # ID услуги в заказе
        quantity = service.quantity
        price = int(service.service_price) if service.service_price else 0
        price_rub, price_byn = convert_currency(service.service_price) if service.service_price else (0, 0)
        executor = service.executor.telegram_username if service.executor else "Не назначен"
        status = service.status or "Не указан"
        created_at = service.created_at.strftime('%d.%m.%Y %H:%M') if service.created_at else "Не указана"
        completion = service.estimated_completion.strftime('%d.%m.%Y %H:%M') if service.estimated_completion else "Не указана"

        # Формируем сообщение для каждой услуги
        message_text += (
            f"🛒 *Заказ №{order_id}*\n"
            f"🆔 *ID услуги в заказе:* {service_in_order_id}\n"
            f"📦 *Услуга:* {service_name}\n"
            f"🔢 *Количество:* {quantity}\n"
            f"💰 *Цена:* {price} USD | {int(price_rub)} RUB | {price_byn:.2f} BYN\n"
            f"👨‍💻 *Исполнитель:* @{executor}\n"
            f"🔄 *Статус:* {status}\n"
            f"📅 *Создано:* {created_at}\n"
            f"⏳ *Завершение:* {completion}\n"
            f"────────────────────\n"
        )

    # Разбиваем сообщение на части, если оно слишком длинное
    max_length = 4000  # Максимальная длина сообщения в Telegram
    if len(message_text) > max_length:
        parts = [message_text[i:i+max_length] for i in range(0, len(message_text), max_length)]
        for part in parts:
            await update.message.reply_text(part, parse_mode="Markdown")
    else:
        await update.message.reply_text(message_text, parse_mode="Markdown")

    # Разбиваем сообщение на части, если оно слишком длинное
    max_length = 4000  # Максимальная длина сообщения в Telegram
    if len(message_text) > max_length:
        parts = [message_text[i:i+max_length] for i in range(0, len(message_text), max_length)]
        for part in parts:
            await update.message.reply_text(part, parse_mode="Markdown")
    else:
        await update.message.reply_text(message_text, parse_mode="Markdown")


async def view_services_in_order(update: Update, context: CallbackContext, order_id: int) -> None:

    #Выводит список услуг в конкретном заказе
    with SessionLocal() as session:
        services_in_order = (
        session.query(OrderServices)
        .join(OrderRequest, OrderRequest.id == OrderServices.order_id)  # Присоединяем заказы
        .join(Service, Service.id == OrderServices.service_id)  # Присоединяем услуги
        .options(joinedload(OrderServices.service), joinedload(OrderServices.executor))  # Загружаем связанные услуги и исполнителей
        .order_by(OrderServices.order_id)  # Сортируем по заказу
        .all()
    )

    if not services_in_order:
        await update.message.reply_text(f"Нет услуг в заказе с ID {order_id}.")
        return 0

    # Формируем сообщение
    message_text = f"📋 *Список услуг в заказе {order_id}:*\n\n"
    message_text += "```\n"  # Начинаем блок кода для моноширинного текста
    message_text += "| ID |    Название услуги    | Кол-во |  USD  |  RUB  |   BYN   | Дата создания  | Дата завершения |    Статус    | Исполнитель |\n"
    message_text += "|----|-----------------------|--------|-------|-------|---------|----------------|-----------------|--------------|-------------|\n"
    for service in services_in_order:
        # Получаем название услуги
        service_name = service.service.name if service.service else "N/A"
        service_name = service_name[:20] + "..." if len(service_name) > 20 else service_name

        # Конвертируем валюту
        price_rub, price_byn = convert_currency(service.service_price)

        # Форматируем дату создания
        created_at = service.created_at.strftime('%d.%m.%y %H:%M') if service.created_at else "N/A"

        # Форматируем дату завершения
        completion_date = service.estimated_completion.strftime('%d.%m.%y %H:%M') if service.estimated_completion else "N/A"

        executor_username = service.executor.telegram_username if service.executor else "N/A"
        # Формируем строку таблицы
        message_text += (
            f"| {service.id:2} | {service_name:21} | {service.quantity:6} | "
            f"{int(service.service_price):5} | {int(price_rub):5} | {price_byn:7.2f} | "
            f"{created_at:17} | {completion_date:15} | {service.status:12} | {executor_username:11} |\n"
        )
    message_text += "```"  # Закрываем блок кода

    # Отправляем сообщение в Telegram
    await send(update, message_text, parse_mode="Markdown")

def update_service_name(service_id: int, new_name: str) -> bool:
    with SessionLocal() as session:
        service = session.query(Service).filter(Service.id == service_id).first()
        if service:
            service.name = new_name
            session.commit()
            return True
        return False

def update_service_category(service_id: int, new_category: str) -> bool:
    with SessionLocal() as session:
        service = session.query(Service).filter(Service.id == service_id).first()
        if service:
            service.category = new_category
            session.commit()
            return True
        return False

def update_service_price(service_id: int, new_price: Decimal) -> bool:
    with SessionLocal() as session:
        service = session.query(Service).filter(Service.id == service_id).first()
        if service:
            service.min_price = new_price
            session.commit()
            return True
        return False
# Проверяем, является ли сообщение подозрительным (фильтры добавим позже)
def is_suspicious(message: str) -> bool:
    russian_numbers_regex = re.compile(
        r'\b(нол[ьяюеи]|один|одног[оа]|одним?|дв[ауе]|двух|двумя?|тр[иеяю]|трех|тремя?|'
        r'четыр[еиьяю]|пят[иьяю]|шест[иьяю]|сем[иьяю]|восьм[иьяю]|девят[иьяю]|'
        r'десят[иьяю]|сорок|сто|двести|триста|четыреста|пятьсот|'
        r'тысяч[иауе]?|миллион[ауе]?)\b',
        re.IGNORECASE
    )
    
    has_russian_numbers = bool(russian_numbers_regex.search(message))

    digit_count = sum(c.isdigit() for c in message)
    has_too_many_digits = digit_count > 5

    forbidden_emojis = [
        "0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", 
        "🔟"
        ]
    has_forbidden_emojis = any(emoji in message for emoji in forbidden_emojis)
    # 1) Проверка на наличие английских букв
    has_english_letters = bool(re.search(r'[a-zA-Z]', message))
    
    # 2) Проверка на наличие более 3 цифр подряд
    has_long_digit_sequence = bool(re.search(r'\d{4,}', message))
    
    # 3) Проверка на наличие подозрительных символов
    suspicious_symbols = ["@", "*", "_", "#", "$"]
    has_suspicious_symbols = any(char in message for char in suspicious_symbols)
    
    # 4) Проверка на вложения (ссылки, изображения, файлы)
    has_attachments = bool(re.search(r'http[s]?://|www\.', message, re.IGNORECASE))
    
    # 5) Проверка на ключевые слова (соцсети и мессенджеры)
    suspicious_keywords = [
    # Полные названия (разные регистры)
    "тг", "ТГ", "TГ", "Tг", "тГ",
    "вк", "ВК", "VK", "vk", "Vк", "vК",
    "вайбер", "Вайбер", "Viber", "viber", "VIBER",
    "ватсап", "Ватсап", "WhatsApp", "whatsapp", "WHATSAPP", "ватс ап", "ватс-ап",
    "телеграм", "Телеграм", "Telegram", "telegram", "TELEGRAM", "тлг", "ТЛГ", "TLG",
    "инстаграм", "Инстаграм", "Instagram", "instagram", "INSTAGRAM", "инста", "Инста", "insta", "Insta",
    "viber", "Viber", "VIBER",
    "whatsapp", "WhatsApp", "WHATSAPP",
    "telegram", "Telegram", "TELEGRAM",
    "instagram", "Instagram", "INSTAGRAM",
    "vk", "VK", "vK", "Vk",
    "tg", "TG", "tG", "Tg", "лс", "директ", "ссылка"
    
    # Альтернативные названия и сленг
    "тeлeграм", "т3л3грам", "тележка", "телега", "тлг", "тлгрм", "тг-канал", "тг канал",
    "инст", "инстик", "инсту", "инстик", "инстаграмм", "инстаграмчик",
    "вацап", "вотсап", "вотс ап", "вац ап", "watsapp", "watsap", "watsup",
    "вайберчик", "вайберуха", "вайб", "вайбера",
    "вконтакте", "в контакте", "вкнтакте", "вкнт", "вк-страница", "вк страница",
    
    # Попытки обхода (с пробелами, точками, спецсимволами)
    "т г", "в к", "v k", "t g",
    "т.г", "в.к", "v.k", "t.g",
    "т_г", "в_к", "v_k", "t_g",
    "т-г", "в-к", "v-k", "t-g",
    "тг.", "вк.", "vk.", "tg.",
    
    # Другие соцсети и мессенджеры
    "facebook", "Facebook", "FACEBOOK", "фейсбук", "Фейсбук", "фб", "ФБ", "fb", "FB",
    "twitter", "Twitter", "TWITTER", "твиттер", "Твиттер", "твт", "ТВТ", "twt", "TWT",
    "tiktok", "TikTok", "TIKTOK", "тикток", "ТикТок", "тик-ток", "tt", "TT",
    "linkedin", "LinkedIn", "LINKEDIN", "линкедин", "Линкедин", "линк", "Линк",
    "discord", "Discord", "DISCORD", "дискорд", "Дискорд", "дис", "Дис", "dc", "DC",
    "signal", "Signal", "SIGNAL", "сигнал", "Сигнал", "sg", "SG",
    "snapchat", "Snapchat", "SNAPCHAT", "снэпчат", "Снэпчат", "снап", "Снап", "sc", "SC",
    "reddit", "Reddit", "REDDIT", "реддит", "Реддит", "рдт", "РДТ", "rdt", "RDT",
    "twitch", "Twitch", "TWITCH", "твич", "Твич", "твч", "ТВЧ", "tvch", "TVCH",
    "youtube", "YouTube", "YOUTUBE", "ютуб", "Ютуб", "ют", "ЮТ", "yt", "YT",
    "pinterest", "Pinterest", "PINTEREST", "пинтерест", "Пинтерест", "пин", "Пин", "pt", "PT",
    "onlyfans", "OnlyFans", "ONLYFANS", "онлифанс", "Онлифанс", "оф", "ОФ", "of", "OF",
    "tinder", "Tinder", "TINDER", "тиндер", "Тиндер", "тинд", "Тинд", "tdr", "TDR",
    "zoom", "Zoom", "ZOOM", "зум", "Зум", "зм", "ЗМ", "zm", "ZM",
    "slack", "Slack", "SLACK", "слак", "Слак", "слк", "СЛК", "slk", "SLK",
    "skype", "Skype", "SKYPE", "скайп", "Скайп", "ск", "СК", "sk", "SK",
    
    # Кибер-сленг и эмодзи
    "дотуп", "дотупь", "дотyп", "дотyпь", "пиши в", "напиши в", "добавь в", "кинь ссылку",
    "✉️", "📱", "📲", "🔗", "📧", "💬", "📨", "📩", "👾", "🤖", "🖇️", "📎", "📌", "📍", "📞", "📟", "📠", "🔌", "📡",
    "пиши в лс", "напиши в лс", "добавь в лс", "кинь ссылку в лс", "контакты в лс", "контакт в лс",
    ]
    has_suspicious_keywords = any(keyword.lower() in message.lower() for keyword in suspicious_keywords)
    
    # Сообщение считается подозрительным, если выполняется хотя бы одно из условий
    return (
        has_russian_numbers or
        has_too_many_digits or
        has_english_letters or
        has_long_digit_sequence or
        has_suspicious_symbols or
        has_attachments or
        has_suspicious_keywords or
        has_forbidden_emojis
    )
# Получаем ID исполнителя по ID услуги
def get_executor_id_by_service(service_id: int):
    with SessionLocal() as session:
        service = session.query(OrderServices).filter(OrderServices.id == service_id).first()
        if service and service.executor:
            return service.executor.telegram_id
    return None

def get_executor_username_by_service(service_id: int):
    with SessionLocal() as session:
        service = session.query(OrderServices).options(joinedload(OrderServices.executor)).filter(OrderServices.id == service_id).first()
        if service and service.executor:
            return service.executor.telegram_username
    return None

def get_client_id_by_service(service_id: int):
    with SessionLocal() as session:
        # Ищем услугу по ID и загружаем связанный заказ и клиента
        service = (
            session.query(OrderServices)
            .options(joinedload(OrderServices.order).joinedload(OrderRequest.client))
            .filter(OrderServices.id == service_id)
            .first()
        )
        if service and service.order and service.order.client:
            return service.order.client.id  # Возвращаем ID клиента
    return None  # Если услуга, заказ или клиент не найдены, возвращаем None

def get_client_username_by_service(service_id: int):
    with SessionLocal() as session:
        # Ищем услугу по ID и загружаем связанный заказ и клиента
        service = (
                session.query(OrderServices)
                .options(
                    joinedload(OrderServices.order).joinedload(OrderRequest.client),
                    joinedload(OrderServices.service)
                )
                .filter(OrderServices.id == service_id)
                .first()
            )
        if service and service.order and service.order.client:
            print(f"[DEBUG] Found client: {service.order.client.telegram_username}")  # Добавим отладочный вывод
            return service.order.client.telegram_username
        else:
            print(f"[DEBUG] Service, order or client not found for service_id: {service_id}")
            if service:
                print(f"[DEBUG] Service found: {service.id}")
                if service.order:
                    print(f"[DEBUG] Order found: {service.order.id}")
                    if not service.order.client:
                        print("[DEBUG] Client not found for order")
                else:
                    print("[DEBUG] Order not found for service")
            else:
                print("[DEBUG] Service not found")
            return None
    return None  # Если услуга, заказ или клиент не найдены, возвращаем None

def get_all_manager_telegram_id():
    with SessionLocal() as session:
        managers = session.query(Manager).all()
        return [manager.telegram_id for manager in managers if manager.telegram_id]

# Получаем список услуг клиента
def get_client_services(username):
    with SessionLocal() as session:
        client = session.query(Client).filter(Client.telegram_username == username).first()
        if not client:
            return None  # Клиент не найден

        services = (
            session.query(OrderServices)
            .join(OrderRequest, OrderRequest.id == OrderServices.order_id)
            .join(Service, Service.id == OrderServices.service_id)
            .options(
                joinedload(OrderServices.service),
                joinedload(OrderServices.executor)
            )
            .filter(OrderRequest.client_id == client.id)
            .all()
        )

        if not services:
            return None  # Нет активных заказов

        message_text = "📋 Ваши услуги в заказах:\n\n"
        for service in services:
            price_rub, price_byn = convert_currency(service.service_price)
            executor_username = service.executor.telegram_username if service.executor else 'не назначен'
            
            message_text += (
                f"📍 ID услуги: {service.id}\n"
                f"📌 Услуга: {service.service.name if service.service else 'N/A'}\n"
                f"📦 Количество: {service.quantity}\n"
                f"💰 Цена: {int(service.service_price)} USD | {int(price_rub)} RUB | {price_byn:.2f} BYN\n"
                f"📅 Дата создания: {service.created_at.strftime('%d.%m.%y %H:%M') if service.created_at else 'N/A'}\n"
                f"⏳ Дата завершения: {service.estimated_completion.strftime('%d.%m.%y %H:%M') if service.estimated_completion else 'N/A'}\n"
                f"📌 Статус: {service.status}\n"
                f"👨‍💻 Исполнитель: @{executor_username}\n"
                "———————————————\n"
            )
        return message_text

# Отправка сообщения через бота
async def send_message(context: CallbackContext, user_id, text):
    await context.bot.send_message(chat_id=user_id, text=text)

async def send_to_manager(
    update: Update,
    context: CallbackContext,
    message_text: str,
    receiver_telegram_id: int,
    receiver_username: str,
    receiver_type: str,
    service_id: int = None
):
    message_id = str(uuid.uuid4())
    
    # Получаем информацию о заказе и услуге
    order_id = "N/A"
    service_in_order_id = "N/A"
    service_name = "Неизвестная услуга"
    
    if service_id:
        with SessionLocal() as session:
            service_in_order = session.query(OrderServices).options(
                joinedload(OrderServices.order),
                joinedload(OrderServices.service)
            ).filter(OrderServices.id == service_id).first()
            
            if service_in_order:
                order_id = service_in_order.order_id if service_in_order.order else "N/A"
                service_in_order_id = service_in_order.id
                service_name = service_in_order.service.name if service_in_order.service else "Неизвестная услуга"

    # Клавиатура модерации
    keyboard = [
        [
            InlineKeyboardButton('✔️ Одобрить', callback_data=f'approve_{receiver_telegram_id}_{message_id}'),
            InlineKeyboardButton('✏️ Изменить', callback_data=f'edit_{receiver_telegram_id}_{message_id}'),
            InlineKeyboardButton('❌ Удалить', callback_data=f'delete_{receiver_telegram_id}_{message_id}')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Отправляем менеджерам
    manager_ids = get_all_manager_telegram_id()
    sent_messages = []

    for manager_id in manager_ids:
        try:
            msg = await context.bot.send_message(
                chat_id=manager_id,
                text=(
                    f"⚠️ Подозрительное сообщение:\n\n"
                    f"{message_text}\n\n"
                    f"📨 Отправитель: @{update.effective_user.username}\n"
                    f"👤 Получатель: @{receiver_username}\n"
                    f"🔹 Для кого: {receiver_type}\n"
                    f"📦 Номер услуги в заказе: #{service_in_order_id}\n"
                    f"🛠 Название услуги: {service_name}"
                ),
                reply_markup=reply_markup
            )
            sent_messages.append({
                "chat_id": manager_id,
                "message_id": msg.message_id
            })
        except Exception as e:
            print(f'Error sending to manager {manager_id}: {e}')

    # Сохраняем в базу
    with SessionLocal() as session:
        try:
            session.execute(text('''
                INSERT INTO message_moderation 
                (message_id, message_text, receiver_telegram_id, receiver_username, 
                 receiver_type, sender_username, service_id, processed, created_at, moderator_messages)
                VALUES 
                (:message_id, :message_text, :receiver_telegram_id, :receiver_username,
                 :receiver_type, :sender_username, :service_id, FALSE, NOW(), :moderator_messages)
            '''), {
                'message_id': message_id,
                'message_text': message_text,
                'receiver_telegram_id': receiver_telegram_id,
                'receiver_username': receiver_username,
                'receiver_type': receiver_type,
                'sender_username': update.effective_user.username,
                'service_id': service_id,
                'moderator_messages': json.dumps(sent_messages)
            })
            session.commit()
        except Exception as e:
            print(f'DB error: {e}')
            session.rollback()
            raise

def get_message_data(message_id):
    with SessionLocal() as session:
        result = session.execute(text('''
            SELECT * FROM message_moderation 
            WHERE message_id = :message_id AND processed = FALSE
        '''), {'message_id': message_id})
        message_data = result.fetchone()
        return dict(message_data) if message_data else None

def mark_message_processed(message_id):
    with SessionLocal() as session:
        try:
            session.execute(text('''
                UPDATE message_moderation 
                SET processed = TRUE 
                WHERE message_id = :message_id
            '''), {'message_id': message_id})
            session.commit()
            return True
        except Exception as e:
            print(f'Error marking message as processed: {e}')
            session.rollback()
            return False

async def cancel_command(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    if chat_id in user_states:
        del user_states[chat_id]
    if 'action' in context.user_data:
        context.user_data.pop('action')
    
    # Возвращаем основное меню
    await start(update, context)

def get_services_by_category():
    with SessionLocal() as session:
        services = session.query(Service).order_by(Service.category, Service.name).all()
        
        services_by_category = {}
        for service in services:
            if service.category not in services_by_category:
                services_by_category[service.category] = []
            services_by_category[service.category].append(service)
        
        return services_by_category
    
# Основная функция
def main() -> None:
    if not TELEGRAM_TOKEN:
        print("Ошибка: Telegram токен не задан!")
        return
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Обработчик команды /start
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_command))

    app.add_handler(MessageHandler(filters.Regex(r'^❌ Отмена$'), cancel_command))

    # Обработчик для всех текстовых сообщений
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_user_message))

    # Обработчик для нажатий на кнопки
    app.add_handler(CallbackQueryHandler(button_callback))

    # Запуск бота
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()