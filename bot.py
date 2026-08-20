"""
Bot Telegram — Imagens de Leilão (BigMiner)
============================================

Bot para grupo interno que consulta imagens de leilão
de veículos por placa + chassi via API Big Miner.

Fluxo:
    /consulta → digita placa → digita chassi → recebe fotos

Variáveis de ambiente:
    TELEGRAM_BOT_TOKEN  — token do @BotFather
    ALLOWED_CHAT_ID     — ID do grupo autorizado (negativo para grupos)
    BIGMINER_TOKEN      — token da API Big Miner
"""
import io
import os
import re
import logging
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    Update,
    InputMediaPhoto,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode, ChatAction

from bigminer_client import consultar, ResultadoConsulta

load_dotenv()

# ============================================================
# CONFIGURAÇÃO
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])
BIGMINER_TOKEN = os.environ.get(
    "BIGMINER_TOKEN",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1bmlxdWVfbmFtZSI6IlNWQ29uc3VsdGEiLCJyb2xlIjoiTUlORVItUEVTUVVJU0EiLCJuYW1laWQiOiJEMkM2MEE1Ni1BNDg3LTQ2MTEtQThBMy0yQzVENTM3NjhCNDYiLCJuYmYiOjE3NjE4Mzc1NzEsImV4cCI6MTkxOTYwMzk3MSwiaWF0IjoxNzYxODM3NTcxfQ.iNxNdhh-6G_4xMiJebBRor-tciJg6-AoapGePYF1NaU",
)

# Limites do Telegram para envio de álbum
MAX_ALBUM_SIZE = 10          # máx de fotos por sendMediaGroup
MAX_PHOTO_BYTES = 10_000_000  # 10 MB por foto

# Estados da conversa
AGUARDANDO_PLACA, AGUARDANDO_CHASSI = range(2)

# Regex de validação
RE_PLACA = re.compile(r"^[A-Z]{3}\d[A-Z0-9]\d{2}$")   # ABC1234 ou ABC1D23
RE_CHASSI = re.compile(r"^[A-Z0-9]{17}$")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ============================================================
# HELPERS
# ============================================================

def _chat_autorizado(update: Update) -> bool:
    """Verifica se a mensagem vem do grupo autorizado."""
    return update.effective_chat and update.effective_chat.id == ALLOWED_CHAT_ID


def _escape_md(text: str) -> str:
    """Escape para MarkdownV2."""
    chars = r"_*[]()~`>#+-=|{}.!"
    for c in chars:
        text = text.replace(c, f"\\{c}")
    return text


# ============================================================
# HANDLERS DO CONVERSATION
# ============================================================

async def cmd_consulta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o fluxo de consulta."""
    if not _chat_autorizado(update):
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]
    ])

    await update.message.reply_text(
        "🔍 *Consulta de Imagens de Leilão*\n\n"
        "Digite a *placa* do veículo\\.\n"
        "Formato: `ABC1234` ou `ABC1D23`",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=keyboard,
    )
    return AGUARDANDO_PLACA


async def receber_placa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Valida a placa e pede o chassi."""
    if not _chat_autorizado(update):
        return ConversationHandler.END

    texto = update.message.text.strip().upper().replace("-", "").replace(" ", "")

    if not RE_PLACA.match(texto):
        await update.message.reply_text(
            "⚠️ Placa inválida\\. Use o formato `ABC1234` ou `ABC1D23`\\.\n"
            "Tente novamente ou /cancelar\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return AGUARDANDO_PLACA

    context.user_data["placa"] = texto

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]
    ])

    await update.message.reply_text(
        f"✅ Placa: `{_escape_md(texto)}`\n\n"
        "Agora digite o *chassi* \\(17 caracteres\\)\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=keyboard,
    )
    return AGUARDANDO_CHASSI


async def receber_chassi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Valida o chassi, consulta a API e envia as fotos."""
    if not _chat_autorizado(update):
        return ConversationHandler.END

    texto = update.message.text.strip().upper().replace(" ", "")

    if not RE_CHASSI.match(texto):
        await update.message.reply_text(
            "⚠️ Chassi inválido\\. Deve ter exatamente 17 caracteres alfanuméricos\\.\n"
            "Tente novamente ou /cancelar\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return AGUARDANDO_CHASSI

    placa = context.user_data["placa"]
    chassi = texto

    # Feedback visual
    status_msg = await update.message.reply_text(
        f"⏳ Consultando *{_escape_md(placa)}* \\| `{_escape_md(chassi)}`\\.\\.\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    await update.effective_chat.send_action(ChatAction.UPLOAD_PHOTO)

    # --- Consulta BigMiner ---
    resultado = consultar(placa=placa, chassi=chassi, token=BIGMINER_TOKEN)

    await _enviar_resultado(update, context, status_msg, resultado)

    context.user_data.clear()
    return ConversationHandler.END


async def _enviar_resultado(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status_msg,
    resultado: ResultadoConsulta,
) -> None:
    """Formata e envia o resultado da consulta."""
    if not resultado.sucesso:
        await status_msg.edit_text(
            f"❌ *Erro na consulta*\n\n"
            f"Placa: `{_escape_md(resultado.placa)}`\n"
            f"Chassi: `{_escape_md(resultado.chassi)}`\n\n"
            f"Motivo: {_escape_md(resultado.erro)}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    if not resultado.imagens:
        await status_msg.edit_text(
            f"📭 *Nenhuma imagem de leilão encontrada*\n\n"
            f"Placa: `{_escape_md(resultado.placa)}`\n"
            f"Chassi: `{_escape_md(resultado.chassi)}`\n"
            f"Leilões verificados: {resultado.total_leiloes}\n"
            f"Tempo: {_escape_md(str(resultado.tempo_total))}s",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # Atualiza status
    total = len(resultado.imagens)
    await status_msg.edit_text(
        f"📸 *{total} imagem\\(ns\\) encontrada\\(s\\)\\!*\n\n"
        f"Placa: `{_escape_md(resultado.placa)}`\n"
        f"Chassi: `{_escape_md(resultado.chassi)}`\n"
        f"Leilões: {resultado.total_leiloes} \\| "
        f"Tempo: {_escape_md(str(resultado.tempo_total))}s\n\n"
        f"Enviando fotos\\.\\.\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    # Envia em álbuns de até 10 fotos
    fotos_validas = []
    for img in resultado.imagens:
        if len(img.dados) > MAX_PHOTO_BYTES:
            logger.warning("Imagem %s excede 10MB, pulando.", img.nome_arquivo)
            continue
        fotos_validas.append(img)

    for i in range(0, len(fotos_validas), MAX_ALBUM_SIZE):
        lote = fotos_validas[i : i + MAX_ALBUM_SIZE]
        media_group = []

        for j, img in enumerate(lote):
            buf = io.BytesIO(img.dados)
            buf.name = img.nome_arquivo

            caption = None
            if i == 0 and j == 0:
                caption = (
                    f"🚗 {resultado.placa}\n"
                    f"📋 Leilão {img.leilao_numero}"
                )

            media_group.append(InputMediaPhoto(media=buf, caption=caption))

        try:
            await update.effective_chat.send_media_group(media=media_group)
        except Exception as e:
            logger.error("Erro ao enviar álbum (offset %d): %s", i, e)
            await update.effective_chat.send_message(
                f"⚠️ Erro ao enviar lote de fotos: {e}"
            )

    # Resumo de erros de download, se houver
    if resultado.erros_download:
        erros_txt = "\n".join(f"• {e}" for e in resultado.erros_download[:5])
        await update.effective_chat.send_message(
            f"⚠️ {len(resultado.erros_download)} imagem(ns) falharam no download:\n{erros_txt}"
        )


async def callback_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Callback do botão Cancelar."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🚫 Consulta cancelada\\.", parse_mode=ParseMode.MARKDOWN_V2)
    context.user_data.clear()
    return ConversationHandler.END


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Comando /cancelar via texto."""
    await update.message.reply_text("🚫 Consulta cancelada.")
    context.user_data.clear()
    return ConversationHandler.END


# ============================================================
# COMANDOS AUXILIARES
# ============================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mensagem de boas-vindas."""
    if not _chat_autorizado(update):
        return

    await update.message.reply_text(
        "🏢 *Bot Imagens de Leilão \\— BigMiner*\n\n"
        "Comandos disponíveis:\n"
        "  /consulta — Consultar imagens por placa \\+ chassi\n"
        "  /cancelar — Cancelar consulta em andamento\n"
        "  /status — Verificar se o bot está ativo",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Health check rápido."""
    if not _chat_autorizado(update):
        return

    await update.message.reply_text("✅ Bot ativo e operacional.")


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    """Inicializa e roda o bot."""
    logger.info("Iniciando Bot Imagens Leilão...")
    logger.info("Chat autorizado: %s", ALLOWED_CHAT_ID)

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # ConversationHandler para fluxo guiado
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("consulta", cmd_consulta)],
        states={
            AGUARDANDO_PLACA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receber_placa),
                CallbackQueryHandler(callback_cancelar, pattern="^cancelar$"),
            ],
            AGUARDANDO_CHASSI: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receber_chassi),
                CallbackQueryHandler(callback_cancelar, pattern="^cancelar$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancelar", cmd_cancelar),
            CallbackQueryHandler(callback_cancelar, pattern="^cancelar$"),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
        conversation_timeout=120,
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))

    logger.info("Bot pronto. Polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
