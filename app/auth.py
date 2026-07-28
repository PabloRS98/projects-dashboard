"""Autenticacion HTTP Basic opcional, activable via ENABLE_AUTH en .env."""
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import settings

security = HTTPBasic(auto_error=False)


def verify_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> bool:
    if not settings.enable_auth:
        return True
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticacion requerida",
            headers={"WWW-Authenticate": "Basic"},
        )
    # Se compara sobre bytes: compare_digest lanza TypeError con str no ASCII,
    # así que una contraseña con tilde o ñ devolvía un 500 en lugar de un 401.
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"), settings.auth_username.encode("utf-8")
    )
    pass_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"), settings.auth_password.encode("utf-8")
    )
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True
