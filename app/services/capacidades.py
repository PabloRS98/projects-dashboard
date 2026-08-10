"""Qué dato ofrece cada forge, declarado en un solo sitio.

Los tres proveedores no dan lo mismo, y hasta ahora la interfaz no lo decía: el
filtro "CI en rojo" nunca marcaba un proyecto de Bitbucket, el KPI subestimaba y
`/tv` —que existe para dejarlo en un monitor— omitía esos fallos en silencio. Un
usuario con proyectos en Bitbucket veía "0 CI en rojo" y confiaba.

El problema de fondo es que **la ausencia de un dato y el valor cero se pintaban
igual**. Con esta tabla, la interfaz puede decir "no lo da Bitbucket" donde antes
ponía un guion indistinguible de "ninguna estrella".

Va de la mano de la política de `sync.CAMPOS_REMOTOS`: lo que un proveedor no da
no se escribe, así que el campo se queda a `None` — y esta tabla explica por qué.
"""

# Campo -> proveedores que lo ofrecen.
CAPACIDADES: dict[str, dict[str, bool]] = {
    "github": {
        "stars": True, "open_issues": True, "open_prs": True,
        "oldest_open_pr_days": True, "ci_status": True, "commit_weeks": True,
        "homepage": True,
    },
    "gitlab": {
        "stars": True, "open_issues": True, "open_prs": True,
        # GitLab no ordena los MRs por antigüedad en el mismo endpoint que los
        # cuenta, así que no se calcula la edad del más viejo.
        "oldest_open_pr_days": False,
        "ci_status": True,
        # No hay equivalente a /stats/participation.
        "commit_weeks": False,
        # `web_url` es la URL del repo, no una web publicada. Ver [PD-M5].
        "homepage": False,
    },
    "bitbucket": {
        # Bitbucket no tiene "estrellas": tiene "watchers", que no es lo mismo.
        "stars": False,
        "open_issues": True, "open_prs": True,
        "oldest_open_pr_days": False,
        # Pipelines, añadido en [PD-M20].
        "ci_status": True,
        "commit_weeks": False,
        "homepage": True,
    },
}

NOMBRES = {"github": "GitHub", "gitlab": "GitLab", "bitbucket": "Bitbucket"}

ETIQUETAS = {
    "stars": "Estrellas",
    "open_issues": "Issues abiertas",
    "open_prs": "PRs / MRs abiertos",
    "oldest_open_pr_days": "Antigüedad del PR más viejo",
    "ci_status": "Estado de CI",
    "commit_weeks": "Actividad semanal",
    "homepage": "Web publicada",
}


def soporta(proveedor: str | None, campo: str) -> bool:
    """True si ese forge ofrece ese dato. Un proveedor desconocido no ofrece nada."""
    return CAPACIDADES.get(proveedor or "", {}).get(campo, False)


def nombre(proveedor: str | None) -> str:
    return NOMBRES.get(proveedor or "", proveedor or "—")


def tabla() -> list[dict]:
    """La tabla completa, lista para pintar en el panel de estado."""
    return [
        {
            "campo": campo,
            "label": etiqueta,
            "proveedores": [
                {"nombre": NOMBRES[p], "ok": soporta(p, campo)} for p in CAPACIDADES
            ],
        }
        for campo, etiqueta in ETIQUETAS.items()
    ]
