FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --gid 10001 travelos \
    && useradd --uid 10001 --gid travelos --create-home travelos \
    && mkdir -p /app/data /var/lib/travelos \
    && chown -R travelos:travelos /app/data /var/lib/travelos

COPY pyproject.toml README.md ./
COPY src ./src
# Runtime data: the LLM gateway builds a PromptRegistry at startup and refuses
# to start when this directory is missing.
COPY prompts ./prompts
# Migrations ship in the image so the one-shot `migrate` service can apply them.
COPY alembic.ini ./
COPY migrations ./migrations

RUN pip install --upgrade pip && pip install .

USER travelos

EXPOSE 8000

CMD ["uvicorn", "travelos.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
