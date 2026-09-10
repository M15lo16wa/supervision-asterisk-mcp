# Serveur MCP de supervision Asterisk — Module 1 (socle) + Module 2 (outils).
# Le Module 3 (pipeline vocal) s'exécute dans un conteneur dédié (voir
# ../docker-compose.yml, service `voice-pipeline`) car il embarque des modèles.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dépendances d'abord (cache Docker) : on copie seulement le manifeste.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

# Code applicatif
COPY src/ ./src/

EXPOSE 8000

# Compte non-root + répertoire du journal d'audit (monté en volume mcp_audit)
RUN useradd --create-home --uid 10001 mcp \
    && mkdir -p /app/logs && chown -R mcp:mcp /app
USER mcp

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/mcp', timeout=3).status < 500 else 1)" || exit 1

CMD ["python", "-m", "src.main"]
