"""
api/routers/auth_ml.py — OAuth Authorization Code flow para Mercado Livre.

Endpoints
─────────
  GET /auth/ml          → redireciona para página de autorização do ML
  GET /auth/ml/callback → captura o code, troca por tokens e salva em ml_tokens.json
  GET /auth/ml/status   → mostra se há token válido salvo
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/ml", tags=["auth"])

TOKEN_FILE = Path("/var/www/axen/ml_tokens.json")
ML_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

CLIENT_ID     = os.getenv("ML_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "")
REDIRECT_URI  = os.getenv("ML_REDIRECT_URI", "https://app.useaxen.com.br/api/auth/ml/callback")


def _save_tokens(data: dict) -> None:
    data["saved_at"] = time.time()
    TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log.info("[ML Auth] Tokens salvos em %s", TOKEN_FILE)


def _load_tokens() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def refresh_token_if_needed() -> str | None:
    """Retorna access_token válido, renovando via refresh_token se necessário."""
    tokens = _load_tokens()
    if not tokens:
        return None

    saved_at    = tokens.get("saved_at", 0)
    expires_in  = tokens.get("expires_in", 21600)
    access_token = tokens.get("access_token")

    # Se ainda válido (com margem de 5 min), retorna direto
    if access_token and time.time() < saved_at + expires_in - 300:
        return access_token

    # Tenta renovar via refresh_token
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        log.warning("[ML Auth] Sem refresh_token — faça a autorização novamente em /auth/ml")
        return None

    try:
        resp = httpx.post(
            ML_TOKEN_URL,
            data={
                "grant_type":    "refresh_token",
                "client_id":     CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        new_tokens = resp.json()
        _save_tokens(new_tokens)
        log.info("[ML Auth] Token renovado com sucesso.")
        return new_tokens.get("access_token")
    except Exception as e:
        log.error("[ML Auth] Falha ao renovar token: %s", e)
        return None


@router.get("", response_class=RedirectResponse)
def auth_ml_start():
    """Redireciona para a página de autorização do Mercado Livre."""
    url = (
        f"{ML_AUTH_URL}"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
    )
    log.info("[ML Auth] Redirecionando para autorização: %s", url)
    return RedirectResponse(url=url)


@router.get("/callback", response_class=HTMLResponse)
def auth_ml_callback(code: str | None = None, error: str | None = None):
    """Captura o authorization code, troca por tokens e salva."""
    if error or not code:
        return HTMLResponse(f"""
        <h2>❌ Erro na autorização ML</h2>
        <p>Erro: {error or 'código não recebido'}</p>
        <p><a href="/api/auth/ml">Tentar novamente</a></p>
        """, status_code=400)

    try:
        resp = httpx.post(
            ML_TOKEN_URL,
            data={
                "grant_type":    "authorization_code",
                "client_id":     CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code":          code,
                "redirect_uri":  REDIRECT_URI,
            },
            timeout=15,
        )
        resp.raise_for_status()
        tokens = resp.json()
        _save_tokens(tokens)

        user_id = tokens.get("user_id", "")
        return HTMLResponse(f"""
        <h2>✅ Autorização ML concluída!</h2>
        <p>User ID: {user_id}</p>
        <p>Token salvo. O scraper de preços já pode buscar concorrentes no ML.</p>
        <p>O token é renovado automaticamente a cada sync.</p>
        """)
    except Exception as e:
        log.error("[ML Auth] Falha ao trocar code por token: %s", e)
        return HTMLResponse(f"""
        <h2>❌ Falha ao obter token</h2>
        <p>Erro: {e}</p>
        <p><a href="/api/auth/ml">Tentar novamente</a></p>
        """, status_code=500)


@router.get("/status", response_class=HTMLResponse)
def auth_ml_status():
    """Mostra o status do token ML salvo."""
    tokens = _load_tokens()
    if not tokens:
        return HTMLResponse("""
        <h2>⚠️ Nenhum token ML salvo</h2>
        <p><a href="/api/auth/ml">Autorizar agora</a></p>
        """)

    saved_at   = tokens.get("saved_at", 0)
    expires_in = tokens.get("expires_in", 21600)
    expires_at = saved_at + expires_in
    remaining  = int(expires_at - time.time())
    valid      = remaining > 0
    user_id    = tokens.get("user_id", "?")
    has_refresh = bool(tokens.get("refresh_token"))

    return HTMLResponse(f"""
    <h2>{'✅' if valid else '⚠️'} Status do Token ML</h2>
    <ul>
      <li>User ID: {user_id}</li>
      <li>Token válido: {'sim' if valid else 'expirado'}</li>
      <li>Expira em: {remaining // 60} minutos</li>
      <li>Refresh token: {'sim' if has_refresh else 'não'}</li>
    </ul>
    {'<p>Token será renovado automaticamente no próximo sync.</p>' if has_refresh else '<p><a href="/api/auth/ml">Reautorizar</a></p>'}
    """)
