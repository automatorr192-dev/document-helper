import asyncio
import os
import tempfile
from contextlib import suppress

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    ErrorEvent,
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
import ocr
import quota
from extract import ScannedPdfError, image_data_url, pdf_text
from observability import log, setup
from prompts import DISCLAIMER, DOC_TYPES
from schema import Report
from storage import FileStorage

load_dotenv()
setup()

dp = Dispatcher(storage=FileStorage())

MAX_DOC_BYTES = 10 * 1024 * 1024
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", 5))

WELCOME = (
    "🔍 <b>Документ-хелпер</b>\n\n"
    "Проверю договор на кабальные пункты, скрытые платежи и невыгодные условия — "
    "или соберу тебе новый договор/КП с нуля.\n\n"
    "Что делаем?"
)

# Видно в пустом чате ещё до нажатия «Начать» — единственный шанс объяснить, зачем бот.
DESCRIPTION = (
    "Проверяю договор на опасные пункты: невозвратные предоплаты, автопродление, "
    "штрафы без потолка, право второй стороны менять условия задним числом.\n\n"
    "Бросаешь PDF — за минуту получаешь разбор: что опасно, что спорно, что нормально. "
    "Каждый пункт цитирую дословно и перевожу на человеческий. Ещё умею собирать "
    "договор или КП с нуля.\n\n"
    "Нажми «Начать»."
)
SHORT_DESCRIPTION = (
    "Проверяю договор на опасные пункты и собираю новый с нуля. Бросай PDF — за минуту разбор."
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


async def _quota(message: Message, bucket: str) -> bool:
    """Разбор и генерация стоят живых денег, а чат открыт всему интернету."""
    try:
        quota.take(message.from_user.id, DAILY_LIMIT, bucket)
        return True
    except quota.Exhausted as e:
        await message.answer(f"На сегодня хватит — {e.limit} разборов в сутки. Возвращайся завтра.")
        return False


async def _run_analysis(message: Message, coro):
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        try:
            report, replies = await coro
        except RuntimeError as e:
            await message.answer(f"ИИ временно недоступен ({e}). Попробуй ещё раз.")
            return
    log.info(
        "analyze.done",
        source="chat",
        user_id=message.chat.id,
        findings=len(report.findings),
        cost_rub=round(sum(r.cost for r in replies), 2),
    )
    for part in _split(render(report)):
        await message.answer(part, parse_mode=None)
    await message.answer(DISCLAIMER, reply_markup=back_kb())


@dp.message(Flow.awaiting_doc, F.document)
async def analyze_document(message: Message):
    if (message.document.file_size or 0) > MAX_DOC_BYTES:
        await message.answer("Файл больше 10 МБ. Пришли договор одним PDF поменьше.")
        return
    if not await _quota(message, "analyze"):
        return
    path = await _download(message.bot, message.document.file_id, ".pdf")
    try:
        try:
            # pdfplumber синхронный: в event loop он вешает и бота, и веб-часть процесса.
            text = await asyncio.to_thread(pdf_text, path)
        except ScannedPdfError:
            await message.answer("Внутри скан, текста нет — распознаю страницы, это дольше…")
            text, _ = await ocr.text_from_scan(path)
    except ScannedPdfError:
        await message.answer("На страницах не нашлось текста — разбирать нечего.")
        return
    except RuntimeError as e:
        await message.answer(f"Не смог распознать скан ({e}). Попробуй ещё раз.")
        return
    except Exception:
        await message.answer("Не смог прочитать файл. Пришли текстом или фото.")
        return
    finally:
        os.remove(path)
    await _run_analysis(message, analyzer.analyze(text))


@dp.message(Flow.awaiting_doc, F.photo)
async def analyze_photo(message: Message):
    if not await _quota(message, "analyze"):
        return
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
    if not await _quota(message, "analyze"):
        return
    await _run_analysis(message, analyzer.analyze(message.text))


# --- Генерация ---


@dp.message(Flow.gen_brief, F.text)
async def collect_brief(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data:
        await message.answer("Я потерял нить разговора. Начнём заново: /start")
        await state.clear()
        return
    if not await _quota(message, "generate"):
        return
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


@dp.errors()
async def on_error(event: ErrorEvent) -> bool:
    """Ошибка в хендлере не должна ронять polling и не должна оставлять человека молча
    смотреть в экран."""
    log.exception("handler.failed", exc_info=event.exception)
    if isinstance(event.exception, TelegramForbiddenError):
        return True
    message = getattr(event.update, "message", None) or getattr(
        getattr(event.update, "callback_query", None), "message", None
    )
    if isinstance(message, Message):
        with suppress(TelegramAPIError):
            await message.answer("Что-то сломалось на моей стороне. Попробуй ещё раз: /start")
    return True


async def main():
    bot = Bot(
        os.environ["TELEGRAM_BOT_TOKEN"],
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await bot.set_my_commands([BotCommand(command="start", description="В меню")])
    await bot.set_my_description(description=DESCRIPTION)
    await bot.set_my_short_description(short_description=SHORT_DESCRIPTION)
    try:
        # drop_pending_updates: после долгого простоя Telegram отдаёт всё накопленное, и бот
        # начинает отвечать на вчерашние нажатия. Заодно снимает чужой вебхук — иначе 409.
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
