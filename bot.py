import re
import os
import config
import logging
import traceback
import decorators
from md2tgmd import escape
from runasync import run_async
from chatgpt2api.V3 import Chatbot as GPT
from telegram.constants import ChatAction
from agent import docQA, get_doc_from_local, search_summary
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, MessageHandler, ApplicationBuilder, filters, CallbackQueryHandler


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger()

# 获取 httpx 的 logger
httpx_logger = logging.getLogger("httpx")
# 设置 httpx 的日志级别为 WARNING
httpx_logger.setLevel(logging.WARNING)

httpx_logger = logging.getLogger("chromadb.telemetry.posthog")
httpx_logger.setLevel(logging.WARNING)

botNick = config.NICK.lower() if config.NICK else None
botNicKLength = len(botNick) if botNick else 0
print("nick:", botNick)
translator_prompt = "You are a translation engine, you can only translate text and cannot interpret it, and do not explain. Translate the text to {}, please do not explain any sentences, just translate or leave them as they are. this is the content you need to translate: "
@decorators.Authorization
async def command_bot(update, context, language=None, prompt=translator_prompt, title="", robot=None, has_command=True):
    if config.SEARCH_USE_GPT and not has_command:
        title = f"`🤖️ {config.DEFAULT_SEARCH_MODEL}`\n\n"
    if update.message.reply_to_message is None:
        if has_command == False or len(context.args) > 0:
            message = update.message.text if config.NICK is None else update.message.text[botNicKLength:].strip() if update.message.text[:botNicKLength].lower() == botNick else None
            if has_command:
                message = ' '.join(context.args)
            print("\033[32m", update.effective_user.username, update.effective_user.id, update.message.text, "\033[0m")
            if prompt and has_command:
                prompt = prompt.format(language)
                message = prompt + message
            if message:
                await context.bot.send_chat_action(chat_id=update.message.chat_id, action=ChatAction.TYPING)
                await getChatGPT(update, context, title, robot, message, config.SEARCH_USE_GPT, has_command)
        else:
            message = await context.bot.send_message(
                chat_id=update.message.chat_id,
                text="Пожалуйста, поместите текст после команды.",
                parse_mode='MarkdownV2',
                reply_to_message_id=update.message.message_id,
            )
    else:
        if update.message.reply_to_message.document is None:
            message = (
                f"Ошибка форматирования oh~, нужно ответить на файл, чтобы я знал, о каком файле вы хотите задать вопрос, обратите внимание на пробел между командой и вопросом\n\n"
                f"Пожалуйста, введите `тематику вопроса, который будет задан`.\n\n"
                f"Например, загружен документ, и вопрос о том, как распределить грибы по категориям.？\n\n"
                f"Ответить на сообщение введите в поле чата вопрос -`Как сортировать грибы？`\n\n"
            )
            await context.bot.send_message(chat_id=update.effective_chat.id, text=escape(message), parse_mode='MarkdownV2', disable_web_page_preview=True)
            return
        print("\033[32m", update.effective_user.username, update.effective_user.id, update.message.text, "\033[0m")
        await context.bot.send_chat_action(chat_id=update.message.chat_id, action=ChatAction.TYPING)
        pdf_file = update.message.reply_to_message.document

        file_id = pdf_file.file_id
        new_file = await context.bot.get_file(file_id)

        file_url = new_file.file_path

        question = update.message.text

        file_name = pdf_file.file_name
        docpath = os.getcwd() + "/" + file_name
        result = await pdfQA(file_url, docpath, question)
        print(result)
        await context.bot.send_message(chat_id=update.message.chat_id, text=escape(result), parse_mode='MarkdownV2', disable_web_page_preview=True)

@decorators.Authorization
async def reset_chat(update, context):
    if config.API:
        config.ChatGPTbot.reset(convo_id=str(update.message.chat_id), system_prompt=config.systemprompt)
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text="Контекст очищен！",
    )

async def getChatGPT(update, context, title, robot, message, use_search=config.SEARCH_USE_GPT, has_command=True):
    result = title
    text = message
    modifytime = 0
    lastresult = ''
    message = await context.bot.send_message(
        chat_id=update.message.chat_id,
        text="хмм...💭",
        parse_mode='MarkdownV2',
        reply_to_message_id=update.message.message_id,
    )
    messageid = message.message_id
    try:
        if not config.API or config.USE_G4F:
            result = f"`🤖️ {config.GPT_ENGINE}`\n\n"
            import gpt4free
            if "gpt-3.5" in config.GPT_ENGINE:
                for data in gpt4free.get_response(text, config.GPT_ENGINE):
                    result = result + data
                    tmpresult = result
                    modifytime = modifytime + 1
                    if re.sub(r"```", '', result).count("`") % 2 != 0:
                        tmpresult = result + "`"
                    if result.count("```") % 2 != 0:
                        tmpresult = result + "\n```"
                    if modifytime % 20 == 0 and lastresult != tmpresult:
                        if 'claude2' in title:
                            tmpresult = re.sub(r",", '，', tmpresult)
                        await context.bot.edit_message_text(chat_id=update.message.chat_id, message_id=messageid, text=escape(tmpresult), parse_mode='MarkdownV2', disable_web_page_preview=True)
                        lastresult = tmpresult
            else:
                result = f"`🤖️ {config.GPT_ENGINE}`\n\n"
                import gpt4free
                tmpresult = await gpt4free.get_async_response(text, config.GPT_ENGINE)
                result = result + tmpresult
                await context.bot.edit_message_text(chat_id=update.message.chat_id, message_id=messageid, text=escape(result), parse_mode='MarkdownV2', disable_web_page_preview=True)
                lastresult = result
        elif use_search and not has_command:
            for data in search_summary(text, model=config.DEFAULT_SEARCH_MODEL, use_goolge=config.USE_GOOGLE, use_gpt=config.SEARCH_USE_GPT):
                result = result + data
                tmpresult = result
                modifytime = modifytime + 1
                if re.sub(r"```", '', result).count("`") % 2 != 0:
                    tmpresult = result + "`"
                if result.count("```") % 2 != 0:
                    tmpresult = result + "\n```"
                if modifytime % 20 == 0 and lastresult != tmpresult:
                    if 'claude2' in title:
                        tmpresult = re.sub(r",", '，', tmpresult)
                    await context.bot.edit_message_text(chat_id=update.message.chat_id, message_id=messageid, text=escape(tmpresult), parse_mode='MarkdownV2', disable_web_page_preview=True)
                    lastresult = tmpresult
        else:
            for data in robot.ask_stream(text, convo_id=str(update.message.chat_id), pass_history=config.PASS_HISTORY):
                result = result + data
                tmpresult = result
                modifytime = modifytime + 1
                if re.sub(r"```", '', result).count("`") % 2 != 0:
                    tmpresult = result + "`"
                if result.count("```") % 2 != 0:
                    tmpresult = result + "\n```"
                if modifytime % 20 == 0 and lastresult != tmpresult:
                    if 'claude2' in title:
                        tmpresult = re.sub(r",", '，', tmpresult)
                    await context.bot.edit_message_text(chat_id=update.message.chat_id, message_id=messageid, text=escape(tmpresult), parse_mode='MarkdownV2', disable_web_page_preview=True)
                    lastresult = tmpresult
    except Exception as e:
        print('\033[31m')
        print("response_msg", result)
        print("error", e)
        traceback.print_exc()
        print('\033[0m')
        if config.API:
            robot.reset(convo_id=str(update.message.chat_id), system_prompt=config.systemprompt)
        if "You exceeded your current quota, please check your plan and billing details." in str(e):
            print("OpenAI api 已过期！")
            await context.bot.delete_message(chat_id=update.message.chat_id, message_id=messageid)
            messageid = ''
            config.API = ''
        result += f"`Упс ошибка(！{e}`"
    print(result)
    if lastresult != result and messageid:
        if 'claude2' in title:
            result = re.sub(r",", '，', result)
        await context.bot.edit_message_text(chat_id=update.message.chat_id, message_id=messageid, text=escape(result), parse_mode='MarkdownV2', disable_web_page_preview=True)

import time
import threading
async def delete_message(update, context, messageid, delay=10):
    time.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=messageid)
    except Exception as e:
        print('\033[31m')
        print("error", e)
        print('\033[0m')

buttons = [
    [
        InlineKeyboardButton("gpt-3.5-turbo", callback_data="gpt-3.5-turbo"),
        InlineKeyboardButton("gpt-3.5-turbo-16k", callback_data="gpt-3.5-turbo-16k"),
    ],
    [
        InlineKeyboardButton("gpt-3.5-turbo-0301", callback_data="gpt-3.5-turbo-0301"),
    ],
    [
        InlineKeyboardButton("gpt-3.5-turbo-0613", callback_data="gpt-3.5-turbo-0613"),
    ],
    [
        InlineKeyboardButton("gpt-4", callback_data="gpt-4"),
        InlineKeyboardButton("gpt-4-0314", callback_data="gpt-4-0314"),
    ],
    [
        InlineKeyboardButton("gpt-4-32k", callback_data="gpt-4-32k"),
        InlineKeyboardButton("gpt-4-32k-0314", callback_data="gpt-4-32k-0314"),
    ],
    [
        InlineKeyboardButton("gpt-4-0613", callback_data="gpt-4-0613"),
        InlineKeyboardButton("gpt-4-32k-0613", callback_data="gpt-4-32k-0613"),
    ],
    [
        InlineKeyboardButton("claude-2-web", callback_data="claude-2-web"),
    ],
    [
        InlineKeyboardButton("返回上一级", callback_data="返回上一级"),
    ],
]

first_buttons = [
    [
        InlineKeyboardButton("Замена модели вопросов и ответов", callback_data="Замена модели вопросов и ответов"),
        InlineKeyboardButton("Замена поисковой модели", callback_data="Замена поисковой модели"),
    ],
    [
        InlineKeyboardButton("История закрыта", callback_data="История закрыта"),
        InlineKeyboardButton("google", callback_data="google"),
    ],
    [
        InlineKeyboardButton("Поиск", callback_data="Поиск"),
        InlineKeyboardButton("анализ PDF", callback_data="pdf"),
    ],
    [
        InlineKeyboardButton("gpt4free已关闭", callback_data="gpt4free"),
    ],
]
if os.environ.get('GOOGLE_API_KEY', None) == None and os.environ.get('GOOGLE_CSE_ID', None) == None:
    first_buttons[1][1] = InlineKeyboardButton("google已关闭", callback_data="google")


banner = "👇Модель gpt, используемая по умолчанию, может быть изменена в любое время ниже:"
async def button_press(update, context):
    """Function to handle the button press"""
    info_message = (
        f"`Hi, {update.effective_user.username}!`\n\n"
        f"**Default engine:** `{config.GPT_ENGINE}`\n"
        f"**Default search model:** `{config.DEFAULT_SEARCH_MODEL}`\n"
        f"**temperature:** `{config.temperature}`\n"
        f"**API_URL:** `{config.API_URL}`\n\n"
        f"**API:** `{config.API}`\n\n"
        f"**WEB_HOOK:** `{config.WEB_HOOK}`\n\n"
    )
    callback_query = update.callback_query
    await callback_query.answer()
    data = callback_query.data
    if ("gpt-" or "cluade") in data:
        if config.ENGINE_FLAG:
            config.GPT_ENGINE = data
        else:
            config.DEFAULT_SEARCH_MODEL = data
        if config.API:
            config.ChatGPTbot = GPT(api_key=f"{config.API}", engine=config.GPT_ENGINE, system_prompt=config.systemprompt, temperature=config.temperature)
            config.ChatGPTbot.reset(convo_id=str(update.effective_chat.id), system_prompt=config.systemprompt)
        try:
            info_message = (
                f"`Hi, {update.effective_user.username}!`\n\n"
                f"**Default engine:** `{config.GPT_ENGINE}`\n"
                f"**Default search model:** `{config.DEFAULT_SEARCH_MODEL}`\n"
                f"**temperature:** `{config.temperature}`\n"
                f"**API_URL:** `{config.API_URL}`\n\n"
                f"**API:** `{config.API}`\n\n"
                f"**WEB_HOOK:** `{config.WEB_HOOK}`\n\n"
            )
            message = await callback_query.edit_message_text(
                text=escape(info_message + banner),
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode='MarkdownV2'
            )
        except Exception as e:
            logger.info(e)
            pass
    elif "Замена модели вопросов и ответов" in data:
        message = await callback_query.edit_message_text(
            text=escape(info_message + banner),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode='MarkdownV2'
        )
        config.ENGINE_FLAG = True
    elif "Замена модели поиска" in data:
        message = await callback_query.edit_message_text(
            text=escape(info_message + banner),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode='MarkdownV2'
        )
        config.ENGINE_FLAG = False
    elif "Назад" in data:
        message = await callback_query.edit_message_text(
            text=escape(info_message),
            reply_markup=InlineKeyboardMarkup(first_buttons),
            parse_mode='MarkdownV2'
        )
    elif "Справка по истории" in data:
        config.PASS_HISTORY = not config.PASS_HISTORY
        if config.PASS_HISTORY == False:
            first_buttons[1][0] = InlineKeyboardButton("отключить Историю", callback_data="отключить Историю")
        else:
            first_buttons[1][0] = InlineKeyboardButton("Открыть историю", callback_data="Открыть историю")
        info_message = (
            f"`Hi, {update.effective_user.username}!`\n\n"
            f"**Default engine:** `{config.GPT_ENGINE}`\n"
            f"**Default search model:** `{config.DEFAULT_SEARCH_MODEL}`\n"
            f"**temperature:** `{config.temperature}`\n"
            f"**API_URL:** `{config.API_URL}`\n\n"
            f"**API:** `{config.API}`\n\n"
            f"**WEB_HOOK:** `{config.WEB_HOOK}`\n\n"
        )
        message = await callback_query.edit_message_text(
            text=escape(info_message),
            reply_markup=InlineKeyboardMarkup(first_buttons),
            parse_mode='MarkdownV2'
        )
    elif "搜索" in data:
        config.SEARCH_USE_GPT = not config.SEARCH_USE_GPT
        if config.SEARCH_USE_GPT == False:
            first_buttons[2][0] = InlineKeyboardButton("Отключен поиск", callback_data="Отключен поиск")
        else:
            first_buttons[2][0] = InlineKeyboardButton("Включен поиск", callback_data="Включен поиск")

        info_message = (
            f"`Hi, {update.effective_user.username}!`\n\n"
            f"**Default engine:** `{config.GPT_ENGINE}`\n"
            f"**Default search model:** `{config.DEFAULT_SEARCH_MODEL}`\n"
            f"**temperature:** `{config.temperature}`\n"
            f"**API_URL:** `{config.API_URL}`\n\n"
            f"**API:** `{config.API}`\n\n"
            f"**WEB_HOOK:** `{config.WEB_HOOK}`\n\n"
        )

        message = await callback_query.edit_message_text(
            text=escape(info_message),
            reply_markup=InlineKeyboardMarkup(first_buttons),
            parse_mode='MarkdownV2'
        )
    elif "google" in data:
        if os.environ.get('GOOGLE_API_KEY', None) == None and os.environ.get('GOOGLE_CSE_ID', None) == None:
            return
        config.USE_GOOGLE = not config.USE_GOOGLE
        if config.USE_GOOGLE == False:
            first_buttons[1][1] = InlineKeyboardButton("google закрыт", callback_data="google")
        else:
            first_buttons[1][1] = InlineKeyboardButton("google открыт", callback_data="google")

        info_message = (
            f"`Hi, {update.effective_user.username}!`\n\n"
            f"**Default engine:** `{config.GPT_ENGINE}`\n"
            f"**Default search model:** `{config.DEFAULT_SEARCH_MODEL}`\n"
            f"**temperature:** `{config.temperature}`\n"
            f"**API_URL:** `{config.API_URL}`\n\n"
            f"**API:** `{config.API}`\n\n"
            f"**WEB_HOOK:** `{config.WEB_HOOK}`\n\n"
        )
        message = await callback_query.edit_message_text(
            text=escape(info_message),
            reply_markup=InlineKeyboardMarkup(first_buttons),
            parse_mode='MarkdownV2'
        )
    elif "pdf" in data:
        config.PDF_EMBEDDING = not config.PDF_EMBEDDING
        if config.PDF_EMBEDDING == False:
            first_buttons[2][1] = InlineKeyboardButton("анализ PDF закрыт", callback_data="pdf")
        else:
            first_buttons[2][1] = InlineKeyboardButton("анализ PDF открыт", callback_data="pdf")

        info_message = (
            f"`Hi, {update.effective_user.username}!`\n\n"
            f"**Default engine:** `{config.GPT_ENGINE}`\n"
            f"**Default search model:** `{config.DEFAULT_SEARCH_MODEL}`\n"
            f"**temperature:** `{config.temperature}`\n"
            f"**API_URL:** `{config.API_URL}`\n\n"
            f"**API:** `{config.API}`\n\n"
            f"**WEB_HOOK:** `{config.WEB_HOOK}`\n\n"
        )
        message = await callback_query.edit_message_text(
            text=escape(info_message),
            reply_markup=InlineKeyboardMarkup(first_buttons),
            parse_mode='MarkdownV2'
        )
    elif "gpt4free" in data:
        config.USE_G4F = not config.USE_G4F
        print(config.USE_G4F)
        if config.USE_G4F == False:
            first_buttons[3][0] = InlineKeyboardButton("gpt4free не работает", callback_data="gpt4free")
        else:
            first_buttons[3][0] = InlineKeyboardButton("gpt4free не рабочий", callback_data="gpt4free")

        info_message = (
            f"`Hi, {update.effective_user.username}!`\n\n"
            f"**Default engine:** `{config.GPT_ENGINE}`\n"
            f"**Default search model:** `{config.DEFAULT_SEARCH_MODEL}`\n"
            f"**temperature:** `{config.temperature}`\n"
            f"**API_URL:** `{config.API_URL}`\n\n"
            f"**API:** `{config.API}`\n\n"
            f"**WEB_HOOK:** `{config.WEB_HOOK}`\n\n"
        )
        message = await callback_query.edit_message_text(
            text=escape(info_message),
            reply_markup=InlineKeyboardMarkup(first_buttons),
            parse_mode='MarkdownV2'
        )

@decorators.Authorization
async def info(update, context):
    info_message = (
        f"`Hi, {update.effective_user.username}!`\n\n"
        f"**Default engine:** `{config.GPT_ENGINE}`\n"
        f"**Default search model:** `{config.DEFAULT_SEARCH_MODEL}`\n"
        f"**temperature:** `{config.temperature}`\n"
        f"**API_URL:** `{config.API_URL}`\n\n"
        f"**API:** `{config.API}`\n\n"
        f"**WEB_HOOK:** `{config.WEB_HOOK}`\n\n"
    )
    message = await context.bot.send_message(chat_id=update.message.chat_id, text=escape(info_message), reply_markup=InlineKeyboardMarkup(first_buttons), parse_mode='MarkdownV2', disable_web_page_preview=True)

    messageid = message.message_id
    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)

from agent import pdfQA, getmd5, persist_emdedding_pdf
@decorators.Authorization
async def handle_pdf(update, context):
    # 获取接收到的文件
    pdf_file = update.message.document
    # 得到文件的url
    file_name = pdf_file.file_name
    docpath = os.getcwd() + "/" + file_name
    persist_db_path = getmd5(docpath)
    match_embedding = os.path.exists(persist_db_path)
    file_id = pdf_file.file_id
    new_file = await context.bot.get_file(file_id)
    file_url = new_file.file_path

    question = update.message.caption
    if question is None:
        if not match_embedding:
            persist_emdedding_pdf(file_url, persist_db_path)
        message = (
            f"Я прочитал документ！\n\n"
            f"Задайте вопрос`\n\n"
            f"Чем конкретней он будет сформулирован\n\n"
            f"Тем точней я отвечу`\n\n"
        )
        await context.bot.send_message(chat_id=update.effective_chat.id, text=escape(message), parse_mode='MarkdownV2', disable_web_page_preview=True)
        return

    result = await pdfQA(file_url, docpath, question)
    print(result)
    await context.bot.send_message(chat_id=update.message.chat_id, text=escape(result), parse_mode='MarkdownV2', disable_web_page_preview=True)

@decorators.Authorization
async def qa(update, context):
    if (len(context.args) != 2):
        message = (
            f "Ошибка форматирования oh~, требуется два параметра, обратите внимание на пробел между путем или ссылкой, вопросом \n\n"
            f "Пожалуйста, введите `/qa Ссылка на базу знаний Вопрос, который нужно задать`\n\n"
            f "Например, ссылка базы знаний - https://abc.com, а вопрос - Как классифицируются грибы? \n\n"
            f "Тогда введите `/qa https://abc.com Как классифицируются грибы? \n\n"
            f "Убедитесь, что в вопросе нет пробелов, 👆 Нажмите на команду выше, чтобы скопировать формат \n\n"
            f "Помимо ввода URL, поддерживается также локальная база знаний, путь к папке локальной базы знаний - `. /wiki`, вопрос в том, как классифицировать грибы? \n\n"
            f", затем введите `/qa . /wiki Как классифицируются грибы? \n\n"
            f "Убедитесь, что в вопросе нет пробелов, 👆 Нажмите на команду выше, чтобы скопировать формат \n\n"
            f "В настоящее время локальная база знаний поддерживает только файлы формата Markdown\n\n"
        )
        await context.bot.send_message(chat_id=update.effective_chat.id, text=escape(message), parse_mode='MarkdownV2', disable_web_page_preview=True)
        return
    print("\033[32m", update.effective_user.username, update.effective_user.id, update.message.text, "\033[0m")
    await context.bot.send_chat_action(chat_id=update.message.chat_id, action=ChatAction.TYPING)
    result = await docQA(context.args[0], context.args[1], get_doc_from_local)
    print(result["answer"])
    # source_url = set([i.metadata['source'] for i in result["source_documents"]])
    # source_url = "\n".join(source_url)
    # message = (
    #     f"{result['result']}\n\n"
    #     f"ссылка：\n"
    #     f"{source_url}"
    # )
    await context.bot.send_message(chat_id=update.message.chat_id, text=escape(result["answer"]), parse_mode='MarkdownV2', disable_web_page_preview=True)

async def start(update, context): # 当用户输入/start时，返回文本
    user = update.effective_user
    message = (
        "Привет я ChatGPT~\n\n"
        "Если у вас есть какие-либо ошибки, пожалуйста, свяжитесь с \n\n"
        "@makconmsk"
    )
    await update.message.reply_html(rf"Hi {user.mention_html()} ! I am an Assistant, a large language model trained by OpenAI. I will do my best to help answer your questions.",)
    await update.message.reply_text(escape(message), parse_mode='MarkdownV2', disable_web_page_preview=True)

async def error(update, context):
    logger.warning('Update "%s" caused error "%s"', update, context.error)
    await context.bot.send_message(chat_id=update.message.chat_id, text="出错啦！请重试。", parse_mode='MarkdownV2', disable_web_page_preview=True)

@decorators.Authorization
async def unknown(update, context): # 当用户输入未知命令时，返回文本
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Sorry, I didn't understand that command.")

def setup(token):
    application = ApplicationBuilder().read_timeout(10).connection_pool_size(50000).pool_timeout(1200.0).token(token).build()
    
    run_async(application.bot.set_my_commands([
        BotCommand('info', 'basic information'),
        BotCommand('qa', 'Document Q&A with Embedding Database Search'),
        BotCommand('en2zh', 'translate to Chinese'),
        BotCommand('zh2en', 'translate to English'),
        BotCommand('start', 'Start the bot'),
        BotCommand('reset', 'Reset the bot'),
    ]))

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_press))
    application.add_handler(CommandHandler("reset", reset_chat))
    application.add_handler(CommandHandler("en2zh", lambda update, context: command_bot(update, context, "simplified chinese", robot=config.ChatGPTbot)))
    application.add_handler(CommandHandler("zh2en", lambda update, context: command_bot(update, context, "english", robot=config.ChatGPTbot)))
    application.add_handler(CommandHandler("info", info))
    application.add_handler(CommandHandler("qa", qa))
    application.add_handler(MessageHandler(filters.Document.MimeType('application/pdf'), handle_pdf))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda update, context: command_bot(update, context, prompt=None, title=f"`🤖️ {config.GPT_ENGINE}`\n\n", robot=config.ChatGPTbot, has_command=False)))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))
    application.add_error_handler(error)

    return application
