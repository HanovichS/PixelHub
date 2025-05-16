from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, DECIMAL, TypeDecorator, BigInteger, Boolean, JSON
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime
from passlib.context import CryptContext
from passlib.hash import bcrypt
from zoneinfo import ZoneInfo

Base = declarative_base()

# Настройка алгоритма хеширования
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class MoscowDateTime(TypeDecorator):
    impl = DateTime

    def process_bind_param(self, value, dialect):
        # При сохранении в базу данных конвертируем время в UTC
        if value is not None:
            return value.astimezone(ZoneInfo('UTC'))
        return value

    def process_result_value(self, value, dialect):
        # При извлечении из базы данных конвертируем время в московское
        if value is not None:
            return value.replace(tzinfo=ZoneInfo('UTC')).astimezone(ZoneInfo('Europe/Moscow'))
        return value

class Client(Base):
    __tablename__ = 'client'
    
    id = Column(Integer, primary_key=True)
    login = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)  # Здесь теперь хеш
    telegram_id = Column(BigInteger, unique=True, nullable=True)
    telegram_username = Column(String(255), unique=True, nullable=True)
    orders = relationship('OrderRequest', backref='client')
    carts = relationship('Cart', backref='client')

    # Хеширование пароля при создании
    def set_password(self, password: str):
        self.password_hash = pwd_context.hash(password)

    # Проверка пароля
    def verify_password(self, password: str) -> bool:
        return pwd_context.verify(password, self.password_hash)

class Executor(Base):
    __tablename__ = 'executor'
    
    id = Column(Integer, primary_key=True)
    login = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)  # Хеш пароля
    telegram_id = Column(BigInteger, unique=True, nullable=True)
    telegram_username = Column(String(255), unique=True, nullable=True)
    category = Column(String(255), nullable=False)  # Категория заданий, например "Дизайнер", "Разработчик"
    difficulty_level = Column(Integer, nullable=False, default=1)  # Уровень сложности (например, от 1 до 5)

    orders_services = relationship('OrderServices', backref='executor')

    def set_password(self, password: str):
        self.password_hash = pwd_context.hash(password)

    def verify_password(self, password: str) -> bool:
        return pwd_context.verify(password, self.password_hash)

class Manager(Base):
    __tablename__ = 'manager'
    
    id = Column(Integer, primary_key=True)
    login = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)  # Хеш
    telegram_id = Column(BigInteger, unique=True, nullable=True) 
    telegram_username = Column(String(255), unique=True, nullable=True)

    def set_password(self, password: str):
        self.password_hash = pwd_context.hash(password)

    def verify_password(self, password: str) -> bool:
        return pwd_context.verify(password, self.password_hash)

class Service(Base):
    __tablename__ = 'service'
    
    id = Column(Integer, primary_key=True)
    category = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    min_price = Column(DECIMAL(10, 2), nullable=False)

    order_services = relationship('OrderServices', backref='service')
    cart_services = relationship('CartServices', backref='service')


class OrderRequest(Base):
    __tablename__ = 'order_request'
    
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('client.id'), nullable=False)
    created_at = Column(MoscowDateTime, default=lambda: datetime.now(ZoneInfo('Europe/Moscow')))
    estimated_completion = Column(DateTime)
    status = Column(String(50), nullable=False)
    price = Column(DECIMAL(10, 2),)

class OrderServices(Base):
    __tablename__ = 'order_services'
    
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('order_request.id'), nullable=False)
    service_id = Column(Integer, ForeignKey('service.id'), nullable=False)
    quantity = Column(Integer, nullable=False)
    executor_id = Column(Integer, ForeignKey('executor.id'))
    created_at = Column(MoscowDateTime, default=lambda: datetime.now(ZoneInfo('Europe/Moscow')))
    service_price = Column(DECIMAL(10, 2), nullable=False)
    estimated_completion = Column(DateTime)
    status = Column(String(50), nullable=False)
    order = relationship('OrderRequest', backref='order_services')

class Cart(Base):
    __tablename__ = 'cart'
    
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('client.id'), nullable=False)

class CartServices(Base):
    __tablename__ = 'cart_services'
    
    id = Column(Integer, primary_key=True)
    cart_id = Column(Integer, ForeignKey('cart.id'), nullable=False)
    service_id = Column(Integer, ForeignKey('service.id'), nullable=False)
    quantity = Column(Integer, nullable=False)

class MessageModeration(Base):
    __tablename__ = 'message_moderation'
    
    id = Column(Integer, primary_key=True)
    message_id = Column(String(36), unique=True, nullable=False)
    message_text = Column(String, nullable=False)
    receiver_telegram_id = Column(BigInteger)
    receiver_username = Column(String(255))
    receiver_type = Column(String(50))
    sender_username = Column(String(255))
    service_id = Column(Integer)
    processed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    moderator_messages = Column(JSON, default=[]) 

class TokenBlocklist(Base):
    __tablename__ = 'token_blocklist'

    id = Column(Integer, primary_key=True)
    jti = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

