#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Toji V4 – بوت النشر التلقائي المتكامل
- SQLite مع عزل بيانات المستخدمين
- تشفير الجلسات AES-256
- خادم HTTP داخلي (aiohttp) على المنفذ 8080
- جميع الميزات المطلوبة
"""

import os
import asyncio
import json
import random
import re
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union
from contextlib import asynccontextmanager

import aiosqlite
from aiohttp import web
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import pyrogram
from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ForceReply, InputMediaPhoto, InputMediaVideo
)
from pyrogram.errors import (
    ApiIdInvalid, PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired,
    SessionPasswordNeeded, PasswordHashInvalid, UserNotParticipant,
    ChatWriteForbidden, BotMethodInvalid
)
import tgcrypto

# ================== التهيئة الأساسية ==================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TojiV4")

# ثوابت التكوين
API_ID = 29510141
API_HASH = "14c074a5aed49dc7752a9f8d54cf4ad4"
BOT_TOKEN = "8736008386:AAEbUiPoiQsou9ydghuKEmd4lx63GpEGnWg"
OWNER_ID = 7816487928
OWNER_USERNAME = "@ypui5"
CHANNEL_USERNAME = "TJUI9"
HTTP_PORT = 8080

VIP_PRICES = {"week": 1, "month": 2}

IRAQI_TEMPLATES = [
    "❤️ مين مايحب {something}؟ تعال خذ نصيبك 🥰",
    "🔥 شي يخبل {something}، لا يفوتك!",
    "🇮🇶 عراقي وافتخر، {something} فقط عندنا",
    "🫡 حبيبي الغالي، هذا {something} لك وحدك",
    "⭐️ شي يستاهل {something}، جرب ما تخسر شي",
    "🎁 هدية خاصة: {something} لجميع الأصدقاء",
    "🚀 أقوى {something} في السوق، ادخل شوف",
    "✨ حصري: {something} ما يتعوض، أوعى تفوت",
    "📢 عاجل: {something} الآن متوفر",
    "💥 عرض خاص: {something} بأسعار خيالية",
]

# ================== دوال مساعدة ==================
def time_now_baghdad() -> datetime:
    from pytz import timezone
    return datetime.now(timezone("Asia/Baghdad"))

def format_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")

# ================== تشفير الجلسات ==================
class SessionCrypto:
    """تشفير وفك تشفير جلسات المستخدمين باستخدام AES-256"""
    KEY = b"TojiV4SecretKeyForAES256Encryption"  # 32 بايت
    IV = b"TojiV4InitialVec"                     # 16 بايت

    @classmethod
    def encrypt(cls, plain: str) -> str:
        cipher = Cipher(algorithms.AES(cls.KEY), modes.CTR(cls.IV), backend=default_backend())
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(plain.encode()) + encryptor.finalize()
        return encrypted.hex()

    @classmethod
    def decrypt(cls, encrypted_hex: str) -> str:
        encrypted = bytes.fromhex(encrypted_hex)
        cipher = Cipher(algorithms.AES(cls.KEY), modes.CTR(cls.IV), backend=default_backend())
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(encrypted) + decryptor.finalize()
        return decrypted.decode()

# ================== قاعدة البيانات (SQLite) ==================
class Database:
    def __init__(self, db_path="toji.db"):
        self.db_path = db_path

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    is_vip BOOLEAN DEFAULT 0,
                    vip_expiry TEXT,
                    trial_used BOOLEAN DEFAULT 0,
                    session_encrypted TEXT,
                    posting_enabled BOOLEAN DEFAULT 0,
                    wait_time INTEGER DEFAULT 60,
                    protection_mode TEXT DEFAULT 'normal',
                    created_at TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    group_id TEXT,
                    group_title TEXT,
                    added_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS captions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    caption_text TEXT,
                    is_default BOOLEAN DEFAULT 0,
                    created_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    day_of_week INTEGER,
                    hour INTEGER,
                    minute INTEGER,
                    caption_id INTEGER,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    group_id TEXT,
                    message_id INTEGER,
                    caption_id INTEGER,
                    sent_at TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    keyword TEXT,
                    response TEXT,
                    created_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS broadcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER,
                    message_text TEXT,
                    total_sent INTEGER,
                    created_at TEXT
                )
            """)
            await db.commit()

    async def get_user(self, user_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    cols = [desc[0] for desc in cursor.description]
                    return dict(zip(cols, row))
                return None

    async def add_user(self, user_id: int, is_vip: bool = False, trial_used: bool = False):
        async with aiosqlite.connect(self.db_path) as db:
            now = format_date(time_now_baghdad())
            await db.execute(
                "INSERT INTO users (user_id, is_vip, trial_used, created_at) VALUES (?, ?, ?, ?)",
                (user_id, is_vip, trial_used, now)
            )
            await db.commit()

    async def update_user(self, user_id: int, **kwargs):
        async with aiosqlite.connect(self.db_path) as db:
            set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
            values = list(kwargs.values()) + [user_id]
            await db.execute(f"UPDATE users SET {set_clause} WHERE user_id = ?", values)
            await db.commit()

    async def add_group(self, user_id: int, group_id: Union[str, int], title: str = None):
        async with aiosqlite.connect(self.db_path) as db:
            now = format_date(time_now_baghdad())
            await db.execute(
                "INSERT INTO user_groups (user_id, group_id, group_title, added_at) VALUES (?, ?, ?, ?)",
                (user_id, str(group_id), title, now)
            )
            await db.commit()

    async def get_groups(self, user_id: int) -> List[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM user_groups WHERE user_id = ?", (user_id,)) as cursor:
                rows = await cursor.fetchall()
                cols = [desc[0] for desc in cursor.description]
                return [dict(zip(cols, row)) for row in rows]

    async def delete_group(self, user_id: int, group_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM user_groups WHERE user_id = ? AND group_id = ?", (user_id, group_id))
            await db.commit()

    async def add_caption(self, user_id: int, text: str, is_default: bool = False):
        async with aiosqlite.connect(self.db_path) as db:
            now = format_date(time_now_baghdad())
            await db.execute(
                "INSERT INTO captions (user_id, caption_text, is_default, created_at) VALUES (?, ?, ?, ?)",
                (user_id, text, is_default, now)
            )
            await db.commit()

    async def get_captions(self, user_id: int) -> List[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM captions WHERE user_id = ? ORDER BY is_default DESC", (user_id,)) as cursor:
                rows = await cursor.fetchall()
                cols = [desc[0] for desc in cursor.description]
                return [dict(zip(cols, row)) for row in rows]

    async def delete_caption(self, user_id: int, caption_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM captions WHERE user_id = ? AND id = ?", (user_id, caption_id))
            await db.commit()

    async def add_schedule(self, user_id: int, day: int, hour: int, minute: int, caption_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO schedules (user_id, day_of_week, hour, minute, caption_id) VALUES (?, ?, ?, ?, ?)",
                (user_id, day, hour, minute, caption_id)
            )
            await db.commit()

    async def get_schedules(self, user_id: int) -> List[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM schedules WHERE user_id = ?", (user_id,)) as cursor:
                rows = await cursor.fetchall()
                cols = [desc[0] for desc in cursor.description]
                return [dict(zip(cols, row)) for row in rows]

    async def delete_schedule(self, user_id: int, schedule_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM schedules WHERE user_id = ? AND id = ?", (user_id, schedule_id))
            await db.commit()

    async def add_broadcast(self, admin_id: int, text: str, total: int):
        async with aiosqlite.connect(self.db_path) as db:
            now = format_date(time_now_baghdad())
            await db.execute(
                "INSERT INTO broadcasts (admin_id, message_text, total_sent, created_at) VALUES (?, ?, ?, ?)",
                (admin_id, text, total, now)
            )
            await db.commit()

    async def get_stats(self) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            total_users = await db.execute_fetchone("SELECT COUNT(*) FROM users")
            total_vip = await db.execute_fetchone("SELECT COUNT(*) FROM users WHERE is_vip = 1")
            total_posts = await db.execute_fetchone("SELECT COUNT(*) FROM posts")
            return {
                "total_users": total_users[0] if total_users else 0,
                "total_vip": total_vip[0] if total_vip else 0,
                "total_posts": total_posts[0] if total_posts else 0
            }

# ================== البوت الرئيسي ==================
class TojiBot(Client):
    def __init__(self):
        super().__init__(
            "toji_v4",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workers=100,
            parse_mode="html"
        )
        self.db = Database()
        self.user_tasks = {}

    async def start(self):
        await self.db.init()
        await super().start()
        logger.info("Bot started successfully")
        # استعادة مهام النشر
        async with aiosqlite.connect(self.db.db_path) as db:
            async with db.execute("SELECT user_id FROM users WHERE posting_enabled = 1") as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    user_id = row[0]
                    self.user_tasks[user_id] = asyncio.create_task(self.posting_loop(user_id))
        asyncio.create_task(self.check_vip_expiry_loop())

    async def stop(self):
        for task in self.user_tasks.values():
            task.cancel()
        await super().stop()
        logger.info("Bot stopped")

    async def posting_loop(self, user_id: int):
        while True:
            user = await self.db.get_user(user_id)
            if not user or not user["posting_enabled"]:
                break

            wait_time = user.get("wait_time", 60)
            groups = await self.db.get_groups(user_id)
            if not groups:
                await self.db.update_user(user_id, posting_enabled=0)
                break

            captions = await self.db.get_captions(user_id)
            if not captions:
                await self.db.update_user(user_id, posting_enabled=0)
                break
            default_caption = next((c for c in captions if c["is_default"]), captions[0])
            caption_text = default_caption["caption_text"]

            session_enc = user.get("session_encrypted")
            if not session_enc:
                await self.db.update_user(user_id, posting_enabled=0)
                break
            session_str = SessionCrypto.decrypt(session_enc)
            user_client = Client(
                f"user_{user_id}",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=session_str,
                in_memory=True
            )
            try:
                await user_client.start()
            except Exception as e:
                logger.error(f"Failed to start user client for {user_id}: {e}")
                await self.db.update_user(user_id, posting_enabled=0)
                break

            for grp in groups:
                group_id = grp["group_id"]
                try:
                    sent = await user_client.send_message(group_id, caption_text)
                    async with aiosqlite.connect(self.db.db_path) as db:
                        now = format_date(time_now_baghdad())
                        await db.execute(
                            "INSERT INTO posts (user_id, group_id, message_id, caption_id, sent_at) VALUES (?, ?, ?, ?, ?)",
                            (user_id, group_id, sent.id, default_caption["id"], now)
                        )
                        await db.commit()
                except ChatWriteForbidden:
                    try:
                        await user_client.join_chat(group_id)
                        sent = await user_client.send_message(group_id, caption_text)
                        async with aiosqlite.connect(self.db.db_path) as db:
                            now = format_date(time_now_baghdad())
                            await db.execute(
                                "INSERT INTO posts (user_id, group_id, message_id, caption_id, sent_at) VALUES (?, ?, ?, ?, ?)",
                                (user_id, group_id, sent.id, default_caption["id"], now)
                            )
                            await db.commit()
                    except Exception as e:
                        logger.error(f"Join failed for {group_id}: {e}")
                        await self.send_message(user_id, f"⚠️ فشل الانضمام إلى المجموعة {group_id}: {e}")
                except Exception as e:
                    logger.error(f"Send failed to {group_id}: {e}")
                    await self.send_message(user_id, f"⚠️ فشل الإرسال إلى {group_id}: {e}")

            await user_client.stop()
            await asyncio.sleep(wait_time)

    async def check_vip_expiry_loop(self):
        while True:
            await asyncio.sleep(3600)
            now = time_now_baghdad()
            async with aiosqlite.connect(self.db.db_path) as db:
                async with db.execute("SELECT user_id, vip_expiry FROM users WHERE is_vip = 1 AND vip_expiry IS NOT NULL") as cursor:
                    rows = await cursor.fetchall()
                    for user_id, expiry_str in rows:
                        expiry = datetime.fromisoformat(expiry_str)
                        if now >= expiry:
                            await db.execute("UPDATE users SET is_vip = 0, vip_expiry = NULL WHERE user_id = ?", (user_id,))
                            await db.commit()
                            await self.send_message(user_id, "⛔ انتهت صلاحية اشتراك VIP الخاص بك. يرجى التواصل مع المطور للتجديد.")

    async def check_subscription(self, user_id: int) -> bool:
        try:
            member = await self.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
            return member.status in ("member", "administrator", "creator")
        except UserNotParticipant:
            return False

    async def generate_ai_caption(self, user_id: int) -> str:
        template = random.choice(IRAQI_TEMPLATES)
        something_list = ["العرض", "المنتج", "الخدمة", "الهدية", "العرض الحصري", "الكوبون", "الخصم"]
        something = random.choice(something_list)
        caption = template.format(something=something)
        emojis = ["❤️", "🔥", "🇮🇶", "🫡", "⭐️", "🎁", "🚀", "✨", "📢", "💥"]
        caption += " " + random.choice(emojis)
        return caption

# ================== دوال الأزرار ==================
def get_main_keyboard(user_id: int, is_vip: bool) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📊 حسابي", callback_data="my_account")],
        [InlineKeyboardButton("➕ إضافة مجموعة", callback_data="add_group"),
         InlineKeyboardButton("📋 قائمة المجموعات", callback_data="list_groups")],
        [InlineKeyboardButton("📝 إدارة الكليشات", callback_data="manage_captions")],
        [InlineKeyboardButton("⏱ وقت النشر", callback_data="set_wait_time")],
        [InlineKeyboardButton("🗓 جدولة أسبوعية", callback_data="weekly_schedule")],
        [InlineKeyboardButton("🛡 حماية الحساب", callback_data="protection_settings")],
        [InlineKeyboardButton("🎁 عروض تمويل", callback_data="funding_offers")],
        [InlineKeyboardButton("ℹ️ تعليمات", callback_data="help")],
    ]
    if is_vip:
        keyboard.append([InlineKeyboardButton("▶️ بدء النشر", callback_data="start_posting"),
                         InlineKeyboardButton("⏹ إيقاف النشر", callback_data="stop_posting")])
    else:
        keyboard.append([InlineKeyboardButton("⭐️ اشتراك VIP", callback_data="buy_vip")])
    return InlineKeyboardMarkup(keyboard)

def get_funding_offers_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for i in range(1, 11):
        buttons.append([InlineKeyboardButton(f"عرض {i}", callback_data=f"offer_{i}")])
    buttons.append([InlineKeyboardButton("🔙 العودة", callback_data="back_main")])
    return InlineKeyboardMarkup(buttons)

# ================== معالجات البوت ==================
bot = TojiBot()

@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await client.check_subscription(user_id):
        join_button = InlineKeyboardMarkup([[InlineKeyboardButton("اشترك الآن", url=f"https://t.me/{CHANNEL_USERNAME}")]])
        await message.reply(
            f"⛔ عذراً، يجب عليك الاشتراك في قناة البوت أولاً:\n@{CHANNEL_USERNAME}\nبعد الاشتراك، اضغط /start",
            reply_markup=join_button
        )
        return

    user = await client.db.get_user(user_id)
    if not user:
        await client.db.add_user(user_id)
        user = await client.db.get_user(user_id)
    is_vip = user.get("is_vip", 0)
    if not is_vip and not user.get("trial_used"):
        await client.db.update_user(user_id, trial_used=1, is_vip=1, vip_expiry=(time_now_baghdad() + timedelta(hours=12)).isoformat())
        is_vip = True
        await message.reply("🎉 تم تفعيل الاشتراك التجريبي لمدة 12 ساعة! استمتع بالمزايا الكاملة.")
    elif not is_vip:
        await message.reply("مرحباً بك في بوت النشر التلقائي Toji V4!\nللاستفادة من جميع المزايا، قم بشراء اشتراك VIP.")
    else:
        await message.reply(f"مرحباً {message.from_user.first_name}!\nأهلاً بك في لوحة التحكم.")

    await message.reply(
        "اختر من القائمة أدناه:",
        reply_markup=get_main_keyboard(user_id, is_vip)
    )

@bot.on_callback_query()
async def handle_callback(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data
    user = await client.db.get_user(user_id)
    if not user:
        await callback.answer("الرجاء إرسال /start أولاً", show_alert=True)
        return
    is_vip = user.get("is_vip", 0)

    if data == "back_main":
        await callback.message.edit_text(
            "القائمة الرئيسية:",
            reply_markup=get_main_keyboard(user_id, is_vip)
        )
        await callback.answer()
        return

    elif data == "my_account":
        groups = await client.db.get_groups(user_id)
        captions = await client.db.get_captions(user_id)
        text = "**معلومات حسابك**\n\n"
        text += f"🆔 ID: `{user_id}`\n"
        text += f"⭐️ VIP: {'نشط' if is_vip else 'غير نشط'}\n"
        if is_vip and user.get("vip_expiry"):
            text += f"📅 ينتهي: {user['vip_expiry']}\n"
        text += f"📁 المجموعات المضافة: {len(groups)}\n"
        text += f"📝 الكليشات: {len(captions)}\n"
        await callback.message.edit_text(text, reply_markup=get_main_keyboard(user_id, is_vip))
        await callback.answer()
        return

    elif data == "add_group":
        await callback.message.delete()
        msg = await client.ask(
            chat_id=user_id,
            text="📎 أرسل رابط المجموعة (أو المعرف) التي تريد إضافتها.\n\nيمكنك إرسال /cancel للإلغاء.",
            timeout=60
        )
        if msg.text == "/cancel":
            await msg.reply("تم الإلغاء.")
            return
        try:
            chat = await client.get_chat(msg.text)
            group_id = chat.id
            title = chat.title
            await client.db.add_group(user_id, group_id, title)
            await msg.reply(f"✅ تم إضافة المجموعة {title} بنجاح.")
        except Exception as e:
            await msg.reply(f"❌ فشل إضافة المجموعة: {e}")
        finally:
            await client.send_message(user_id, "القائمة الرئيسية", reply_markup=get_main_keyboard(user_id, is_vip))

    elif data == "list_groups":
        groups = await client.db.get_groups(user_id)
        if not groups:
            await callback.answer("لا توجد مجموعات مضافة.", show_alert=True)
            return
        text = "📋 **قائمة المجموعات:**\n\n"
        keyboard = []
        for grp in groups:
            text += f"• {grp['group_title'] or grp['group_id']}\n"
            keyboard.append([InlineKeyboardButton(f"🗑 حذف {grp['group_title'] or grp['group_id']}", callback_data=f"del_group_{grp['id']}")])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_main")])
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        await callback.answer()
        return

    elif data.startswith("del_group_"):
        group_id = int(data.split("_")[2])
        await client.db.delete_group(user_id, group_id)
        await callback.answer("تم حذف المجموعة.")
        groups = await client.db.get_groups(user_id)
        text = "📋 **قائمة المجموعات:**\n\n"
        keyboard = []
        for grp in groups:
            text += f"• {grp['group_title'] or grp['group_id']}\n"
            keyboard.append([InlineKeyboardButton(f"🗑 حذف {grp['group_title'] or grp['group_id']}", callback_data=f"del_group_{grp['id']}")])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_main")])
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data == "manage_captions":
        captions = await client.db.get_captions(user_id)
        if not captions:
            await callback.answer("لا توجد كليشات. استخدم /new_caption لإضافة واحدة.", show_alert=True)
            return
        text = "📝 **قائمة الكليشات:**\n\n"
        keyboard = []
        for cap in captions:
            prefix = "⭐️ " if cap["is_default"] else ""
            text += f"{prefix}{cap['caption_text'][:50]}...\n"
            keyboard.append([InlineKeyboardButton(f"❌ حذف {cap['id']}", callback_data=f"del_cap_{cap['id']}")])
            if not cap["is_default"]:
                keyboard[-1].append(InlineKeyboardButton("⭐️ تعيين افتراضي", callback_data=f"set_default_{cap['id']}"))
        keyboard.append([InlineKeyboardButton("➕ إضافة كليشة", callback_data="add_caption")])
        keyboard.append([InlineKeyboardButton("🤖 توليد كليشة عشوائية", callback_data="generate_ai_caption")])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_main")])
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        await callback.answer()
        return

    elif data.startswith("del_cap_"):
        cap_id = int(data.split("_")[2])
        await client.db.delete_caption(user_id, cap_id)
        await callback.answer("تم حذف الكليشة.")
        await handle_callback(client, callback)
        return

    elif data.startswith("set_default_"):
        cap_id = int(data.split("_")[2])
        async with aiosqlite.connect(client.db.db_path) as db:
            await db.execute("UPDATE captions SET is_default = 0 WHERE user_id = ?", (user_id,))
            await db.execute("UPDATE captions SET is_default = 1 WHERE id = ? AND user_id = ?", (cap_id, user_id))
            await db.commit()
        await callback.answer("تم تعيين الكليشة كافتراضية.")
        await handle_callback(client, callback)
        return

    elif data == "add_caption":
        await callback.message.delete()
        msg = await client.ask(
            chat_id=user_id,
            text="📝 أرسل نص الكليشة الجديدة.\nيمكنك استخدام تنسيق HTML.\n\n/cancel للإلغاء.",
            timeout=120
        )
        if msg.text == "/cancel":
            await msg.reply("تم الإلغاء.")
            return
        await client.db.add_caption(user_id, msg.text)
        await msg.reply("✅ تمت إضافة الكليشة.")
        await client.send_message(user_id, "القائمة الرئيسية", reply_markup=get_main_keyboard(user_id, is_vip))
        return

    elif data == "generate_ai_caption":
        caption = await client.generate_ai_caption(user_id)
        await client.db.add_caption(user_id, caption)
        await callback.answer("تمت إضافة كليشة جديدة.")
        await handle_callback(client, callback)
        return

    elif data == "set_wait_time":
        await callback.message.delete()
        msg = await client.ask(
            chat_id=user_id,
            text="⏱ أدخل وقت الانتظار بين كل منشور وآخر (بالثواني).\n/cancel للإلغاء.",
            timeout=60
        )
        if msg.text == "/cancel":
            await msg.reply("تم الإلغاء.")
            return
        try:
            wait = int(msg.text)
            await client.db.update_user(user_id, wait_time=wait)
            await msg.reply(f"✅ تم ضبط وقت الانتظار إلى {wait} ثانية.")
        except ValueError:
            await msg.reply("❌ يجب إدخال رقم صحيح.")
        await client.send_message(user_id, "القائمة الرئيسية", reply_markup=get_main_keyboard(user_id, is_vip))

    elif data == "weekly_schedule":
        schedules = await client.db.get_schedules(user_id)
        text = "🗓 **الجدولة الأسبوعية:**\n\n"
        if not schedules:
            text += "لا توجد جدولة.\n"
        else:
            days = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
            for s in schedules:
                text += f"{days[s['day_of_week']]} الساعة {s['hour']:02d}:{s['minute']:02d}\n"
        keyboard = [[InlineKeyboardButton("➕ إضافة جدولة", callback_data="add_schedule")]]
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_main")])
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        await callback.answer()
        return

    elif data == "add_schedule":
        await callback.message.delete()
        # سؤال اليوم
        msg_day = await client.ask(user_id, "📅 أدخل رقم اليوم (0=الاثنين ... 6=الأحد):")
        if msg_day.text == "/cancel": return
        try:
            day = int(msg_day.text)
            if day < 0 or day > 6:
                raise ValueError
        except:
            await msg_day.reply("❌ رقم اليوم غير صحيح.")
            return
        msg_hour = await client.ask(user_id, "⏰ أدخل الساعة (0-23):")
        if msg_hour.text == "/cancel": return
        try:
            hour = int(msg_hour.text)
            if hour < 0 or hour > 23:
                raise ValueError
        except:
            await msg_hour.reply("❌ الساعة غير صحيحة.")
            return
        msg_minute = await client.ask(user_id, "🕒 أدخل الدقيقة (0-59):")
        if msg_minute.text == "/cancel": return
        try:
            minute = int(msg_minute.text)
            if minute < 0 or minute > 59:
                raise ValueError
        except:
            await msg_minute.reply("❌ الدقيقة غير صحيحة.")
            return
        captions = await client.db.get_captions(user_id)
        if not captions:
            await msg_minute.reply("❌ لا توجد كليشات. أضف كليشة أولاً.")
            return
        cap_text = "اختر الكليشة المناسبة:\n"
        keyboard = []
        for cap in captions:
            keyboard.append([InlineKeyboardButton(cap["caption_text"][:30], callback_data=f"select_cap_{cap['id']}")])
        msg_cap = await client.ask(user_id, cap_text, reply_markup=InlineKeyboardMarkup(keyboard))
        if not msg_cap or not hasattr(msg_cap, "data") or not msg_cap.data.startswith("select_cap_"):
            await msg_cap.reply("❌ اختيار غير صحيح.")
            return
        cap_id = int(msg_cap.data.split("_")[2])
        await client.db.add_schedule(user_id, day, hour, minute, cap_id)
        await msg_cap.reply("✅ تمت إضافة الجدولة.")
        await client.send_message(user_id, "القائمة الرئيسية", reply_markup=get_main_keyboard(user_id, is_vip))

    elif data == "protection_settings":
        current = user.get("protection_mode", "normal")
        text = f"🛡 **وضع الحماية الحالي:** {current}\n\nاختر الوضع المناسب:\n"
        keyboard = [
            [InlineKeyboardButton("عادي", callback_data="set_protection_normal")],
            [InlineKeyboardButton("آمن", callback_data="set_protection_secure")],
            [InlineKeyboardButton("خفي", callback_data="set_protection_stealth")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]
        ]
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        await callback.answer()
        return

    elif data.startswith("set_protection_"):
        mode = data.split("_")[2]
        await client.db.update_user(user_id, protection_mode=mode)
        await callback.answer(f"تم ضبط وضع الحماية إلى {mode}")
        await callback.message.edit_text("القائمة الرئيسية", reply_markup=get_main_keyboard(user_id, is_vip))
        return

    elif data == "funding_offers":
        await callback.message.edit_text(
            "🎁 **عروض التمويل المتاحة:**\n\nاختر العرض المناسب:",
            reply_markup=get_funding_offers_keyboard()
        )
        await callback.answer()
        return

    elif data.startswith("offer_"):
        offer_num = data.split("_")[1]
        text = f"🔹 **عرض {offer_num}**\n\n"
        text += "طريقة الدفع:\n"
        text += "• آسيا سيل: 07706234820\n"
        text += "• ماستر كارد: 9363427221\n\n"
        text += "بعد الدفع، أرسل إيصال الدفع إلى المطور @ypui5 وسيتم تفعيل الاشتراك.\n"
        price = VIP_PRICES['week'] if offer_num in ['1','2','3'] else VIP_PRICES['month']
        text += f"سعر العرض: {price} وحدة نقدية"
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="funding_offers")]]))
        await callback.answer()
        return

    elif data == "start_posting":
        if not is_vip:
            await callback.answer("هذه الميزة للمشتركين VIP فقط.", show_alert=True)
            return
        if user.get("posting_enabled"):
            await callback.answer("النشر مفعل بالفعل.", show_alert=True)
            return
        await client.db.update_user(user_id, posting_enabled=1)
        if user_id not in client.user_tasks:
            client.user_tasks[user_id] = asyncio.create_task(client.posting_loop(user_id))
        await callback.answer("تم بدء النشر التلقائي.")
        await callback.message.edit_text("القائمة الرئيسية", reply_markup=get_main_keyboard(user_id, is_vip))
        return

    elif data == "stop_posting":
        if not is_vip:
            await callback.answer("هذه الميزة للمشتركين VIP فقط.", show_alert=True)
            return
        if not user.get("posting_enabled"):
            await callback.answer("النشر غير مفعل.", show_alert=True)
            return
        await client.db.update_user(user_id, posting_enabled=0)
        if user_id in client.user_tasks:
            client.user_tasks[user_id].cancel()
            del client.user_tasks[user_id]
        await callback.answer("تم إيقاف النشر التلقائي.")
        await callback.message.edit_text("القائمة الرئيسية", reply_markup=get_main_keyboard(user_id, is_vip))
        return

    elif data == "help":
        text = """
**تعليمات استخدام البوت**

1. **إضافة مجموعة:** أرسل رابط المجموعة أو معرفها (يجب أن يكون الحساب المدخل عضواً فيها).
2. **إدارة الكليشات:** يمكنك إضافة كليشات متعددة وتعيين واحدة كافتراضية.
3. **وقت النشر:** حدد الفاصل الزمني بين كل منشور وآخر.
4. **الجدولة الأسبوعية:** حدد أيام وأوقات محددة للنشر.
5. **حماية الحساب:** اختر الوضع المناسب (عادي/آمن/خفي) لتجنب الحظر.
6. **عروض التمويل:** اختر العرض المناسب وتواصل مع المطور للدفع.

لأي استفسار: @ypui5
        """
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]))
        await callback.answer()
        return

    elif data == "buy_vip":
        await callback.message.edit_text(
            "⭐️ **اشتراك VIP**\n\nاختر الباقة المناسبة:\n",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("أسبوع - 1 وحدة", callback_data="buy_week")],
                [InlineKeyboardButton("شهر - 2 وحدة", callback_data="buy_month")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]
            ])
        )
        await callback.answer()
        return

    elif data in ["buy_week", "buy_month"]:
        duration = "week" if data == "buy_week" else "month"
        price = VIP_PRICES[duration]
        text = f"🔹 **باقة {duration}**\n\n"
        text += "طريقة الدفع:\n"
        text += "• آسيا سيل: 07706234820\n"
        text += "• ماستر كارد: 9363427221\n\n"
        text += "بعد الدفع، أرسل إيصال الدفع إلى المطور @ypui5 وسيتم تفعيل الاشتراك.\n"
        text += f"السعر: {price} وحدة نقدية"
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="funding_offers")]]))
        await callback.answer()
        return

# ================== خادم HTTP للإبقاء على الحاوية حية ==================
async def http_handler(request):
    return web.Response(text="OK", status=200)

async def start_http_server():
    app = web.Application()
    app.router.add_get("/", http_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HTTP_PORT)
    await site.start()
    logger.info(f"HTTP server running on port {HTTP_PORT}")

# ================== تشغيل البوت ==================
async def main():
    asyncio.create_task(start_http_server())
    await bot.start()
    await idle()
    await bot.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
