import asyncio
import logging
import os
import tempfile

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from aiogram.utils.chat_action import ChatActionSender
from dotenv import load_dotenv

import analyzer
import generator
from extract import ScannedPdfError, image_data_url, pdf_text
from prompts import DISCLAIMER, DOC_TYPES
from schema import Report

load_dotenv()
logging.basicConfig(level=logging.INFO)

dp = Dispatcher()

WELCOME = (
    "🔍 <b>Договор-рентген</b>\n\n"
    "Проверю договор на кабальные пункты, скрытые платежи и невыгодные условия — "
    "или соберу тебе новый договор/КП с нуля.\n\n"
    "Что делаем?"
)


class Flow(StatesGroup):
    awaiting_doc = State()
    gen_brief = State()


WEBAPP_URL = os.environ.get("WEBAPP_URL", "")


def menu_kb() -> InlineKeyboardMarkup:
    # Главный вход — мини-апп: там договор виден целиком и пункты подсвечены.
    # Без WEBAPP_URL (локальный запуск без туннеля) остаётся разбор текстом в чате.
    rows = []
    if WEBAPP_URL:
        rows.append(
            [InlineKeyboardButton(text="🔍 Проверить договор", web_app=WebAppInfo(url=WEBAPP_URL))]
        )
        rows.append([InlineKeyboardButton(text="💬 Разобрать в чате", callback_data="check")])
    else:
        rows.append([InlineKeyboardButton(text="🔍 Проверить договор", callback_data="check")])
    rows.append([InlineKeyboardButton(text="📝 Создать документ", callback_data="create")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def types_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"gen:{key}")]
        for key, label in DOC_TYPES.items()
    ]
    rows.append([InlineKeyboardButton(text="⬅ Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅ В меню", callback_data="menu")]]
    )


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(WELCOME, reply_markup=menu_kb())


@dp.callback_query(F.data == "menu")
async def to_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer(WELCOME, reply_markup=menu_kb())
    await cb.answer()


@dp.callback_query(F.data == "check")
async def check(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Flow.awaiting_doc)
    await cb.message.answer(
        "Пришли договор — <b>текстом</b>, <b>PDF-файлом</b> или <b>фото страниц</b>. "
        "Разберу по косточкам."
    )
    await cb.answer()


@dp.callback_query(F.data == "create")
async def create(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer("Какой документ собрать?", reply_markup=types_kb())
    await cb.answer()


@dp.callback_query(F.data.startswith("gen:"))
async def pick_type(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":", 1)[1]
    await state.set_state(Flow.gen_brief)
    await state.update_data(doc_key=key, doc_label=DOC_TYPES[key], brief="", clarified=False)
    await cb.message.answer(
        f"<b>{DOC_TYPES[key]}</b>\n\n"
        "Опиши в одном сообщении: стороны, предмет, сумму и сроки. "
        "Чего не знаешь — пропусти, оставлю поля под заполнение."
    )
    await cb.answer()


# --- Анализ ---


def _split(text: str, limit: int = 4000) -> list[str]:
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    parts.append(text)
    return parts


MARKS = {"red": "🚩", "yellow": "⚠️", "green": "✅"}


def render(report: Report) -> str:
    lines = [report.verdict, "", report.summary]
    for f in report.findings:
        article = f" ({f.article})" if f.article else ""
        lines += ["", f"{MARKS[f.severity]} {f.title}{article}", f"«{f.quote}»", f.plain]
    if report.actions:
        lines += ["", "📌 Перед подписанием:"] + [f"— {a}" for a in report.actions]
    return "\n".join(lines)


async def _run_analysis(message: Message, coro):
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        try:
            report, replies = await coro
        except RuntimeError as e:
            await message.answer(f"ИИ временно недоступен ({e}). Попробуй ещё раз.")
            return
    logging.info("разбор: %d находок, %.2f ₽", len(report.findings), sum(r.cost for r in replies))
    for part in _split(render(report)):
        await message.answer(part, parse_mode=None)
    await message.answer(DISCLAIMER, reply_markup=back_kb())


@dp.message(Flow.awaiting_doc, F.document)
async def analyze_document(message: Message):
    path = await _download(message.bot, message.document.file_id, ".pdf")
    try:
        text = pdf_text(path)
    except ScannedPdfError:
        await message.answer("Не смог вытащить текст (похоже на скан). Пришли фото страниц.")
        return
    except Exception:
        await message.answer("Не смог прочитать файл. Пришли текстом или фото.")
        return
    finally:
        os.remove(path)
    await _run_analysis(message, analyzer.analyze(text))


@dp.message(Flow.awaiting_doc, F.photo)
async def analyze_photo(message: Message):
    path = await _download(message.bot, message.photo[-1].file_id, ".jpg")
    try:
        data_url = image_data_url(path)
    finally:
        os.remove(path)
    await _run_analysis(message, analyzer.analyze_image(data_url))


@dp.message(Flow.awaiting_doc, F.text)
async def analyze_pasted(message: Message):
    if len(message.text) < 40:
        await message.answer("Маловато текста для разбора. Пришли договор целиком.")
        return
    await _run_analysis(message, analyzer.analyze(message.text))


# --- Генерация ---


@dp.message(Flow.gen_brief, F.text)
async def collect_brief(message: Message, state: FSMContext):
    data = await state.get_data()
    brief = (data["brief"] + "\n" + message.text).strip()
    await state.update_data(brief=brief)

    if not data["clarified"]:
        ok, question = await generator.check_brief(data["doc_label"], brief)
        if not ok and question:
            await state.update_data(clarified=True)
            await message.answer(question, parse_mode=None)
            return

    async with ChatActionSender.upload_document(bot=message.bot, chat_id=message.chat.id):
        await message.answer("📝 Собираю документ…")
        try:
            body = await generator.generate_body(data["doc_label"], brief)
        except RuntimeError as e:
            await message.answer(f"ИИ временно недоступен ({e}). Попробуй ещё раз.")
            return
        out = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        out.close()
        try:
            await generator.render_pdf(body, out.name)
            filename = data["doc_label"].replace(" ", "_") + ".pdf"
            await message.answer_document(FSInputFile(out.name, filename=filename))
        finally:
            os.remove(out.name)
    await message.answer(
        "Готово. Проверь плейсхолдеры и сверь важное с юристом.", reply_markup=back_kb()
    )
    await state.clear()


async def _download(bot: Bot, file_id: str, suffix: str) -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
    await bot.download(file_id, destination=tmp.name)
    return tmp.name


async def main():
    bot = Bot(
        os.environ["TELEGRAM_BOT_TOKEN"],
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await bot.set_my_commands([BotCommand(command="start", description="В меню")])
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
