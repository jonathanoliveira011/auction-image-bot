"""
Cliente da API Big Miner — Imagens de Leilão
=============================================

Extração do script original bigminer_imagens_leilao.py,
adaptada para retornar imagens em memória (sem gravar em disco).
"""
import io
import time
import logging
import requests
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURAÇÃO
# ============================================================

BASE_URL = "https://api.bigminer.com.br"
ENDPOINT_CONSULTA = f"{BASE_URL}/v1/imagem/consulta"
TIMEOUT_CONSULTA = 60
TIMEOUT_DOWNLOAD = 30


# ============================================================
# MODELOS
# ============================================================

@dataclass
class ImagemLeilao:
    """Uma imagem baixada em memória."""
    leilao_numero: int
    indice: int
    url: str
    dados: bytes = field(repr=False)
    extensao: str = ".png"

    @property
    def nome_arquivo(self) -> str:
        return f"leilao{self.leilao_numero}_img{self.indice:02d}{self.extensao}"


@dataclass
class ResultadoConsulta:
    """Resultado completo de uma consulta."""
    sucesso: bool
    placa: str
    chassi: str
    imagens: List[ImagemLeilao] = field(default_factory=list)
    total_leiloes: int = 0
    total_imagens_api: int = 0
    erros_download: List[str] = field(default_factory=list)
    erro: str = ""
    tempo_total: float = 0.0
    dados_brutos: Optional[Dict[str, Any]] = None


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def _normalizar_placa(placa: str) -> str:
    return placa.upper().replace("-", "").replace(" ", "").strip()


def _normalizar_chassi(chassi: str) -> str:
    return chassi.upper().replace(" ", "").strip()


def _extrair_extensao(url: str) -> str:
    """Extrai extensão da URL ou retorna .png como padrão."""
    from pathlib import PurePosixPath
    try:
        path = PurePosixPath(url.split("?")[0])
        return path.suffix if path.suffix else ".png"
    except Exception:
        return ".png"


# ============================================================
# CONSULTA + DOWNLOAD EM MEMÓRIA
# ============================================================

def consultar(
    placa: str,
    chassi: str,
    token: str
) -> ResultadoConsulta:
    """
    Consulta imagens de leilão e baixa em memória.

    Args:
        placa: Placa do veículo
        chassi: Chassi do veículo
        token: Token Bearer da API BigMiner

    Returns:
        ResultadoConsulta com imagens em bytes
    """
    inicio_total = time.time()
    placa_norm = _normalizar_placa(placa)
    chassi_norm = _normalizar_chassi(chassi)

    resultado = ResultadoConsulta(
        sucesso=False,
        placa=placa_norm,
        chassi=chassi_norm,
    )

    # --- 1. Consulta à API ---
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "placa": placa_norm,
        "Chassi": chassi_norm,
    }

    try:
        logger.info("Consultando BigMiner: placa=%s chassi=%s", placa_norm, chassi_norm)
        resp = requests.post(
            ENDPOINT_CONSULTA,
            json=body,
            headers=headers,
            timeout=TIMEOUT_CONSULTA,
        )
    except requests.exceptions.Timeout:
        resultado.erro = "Timeout na comunicação com Big Miner"
        return resultado
    except requests.exceptions.ConnectionError:
        resultado.erro = "Erro de conexão com Big Miner"
        return resultado
    except Exception as e:
        resultado.erro = f"Erro inesperado na consulta: {e}"
        return resultado

    if resp.status_code == 401:
        resultado.erro = "Token inválido ou expirado"
        return resultado

    if resp.status_code != 200:
        resultado.erro = f"API retornou HTTP {resp.status_code}"
        try:
            err_body = resp.json()
            if isinstance(err_body, dict) and "message" in err_body:
                resultado.erro += f": {err_body['message']}"
        except Exception:
            pass
        return resultado

    # --- 2. Parsear resposta ---
    try:
        data = resp.json()
    except Exception:
        resultado.erro = "Resposta da API não é JSON válido"
        return resultado

    resultado.dados_brutos = data

    imagens_leilao = data.get("imagensLeilao") or {}
    leiloes = (imagens_leilao.get("leiloes") or []) if isinstance(imagens_leilao, dict) else []
    resultado.total_leiloes = len(leiloes)
    resultado.total_imagens_api = sum(
        len(l.get("imagens") or []) for l in leiloes if isinstance(l, dict)
    )

    if resultado.total_imagens_api == 0:
        resultado.sucesso = True
        resultado.tempo_total = round(time.time() - inicio_total, 2)
        return resultado

    # --- 3. Download das imagens em memória ---
    for leilao in leiloes:
        if not isinstance(leilao, dict):
            continue

        numero = leilao.get("numeroLeilao", 0)
        imagens = leilao.get("imagens") or []

        for idx, img in enumerate(imagens, start=1):
            if not isinstance(img, dict):
                continue

            url = img.get("url", "")
            if not url:
                continue

            try:
                r = requests.get(url, timeout=TIMEOUT_DOWNLOAD)
                r.raise_for_status()

                resultado.imagens.append(ImagemLeilao(
                    leilao_numero=numero,
                    indice=idx,
                    url=url,
                    dados=r.content,
                    extensao=_extrair_extensao(url),
                ))
            except Exception as e:
                erro_msg = f"Leilão {numero}, img {idx}: {e}"
                logger.warning("Falha no download: %s", erro_msg)
                resultado.erros_download.append(erro_msg)

    resultado.sucesso = True
    resultado.tempo_total = round(time.time() - inicio_total, 2)
    logger.info(
        "Consulta finalizada: %d imagens baixadas, %d erros, %.1fs",
        len(resultado.imagens), len(resultado.erros_download), resultado.tempo_total,
    )
    return resultado
