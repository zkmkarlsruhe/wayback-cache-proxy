FROM python:3.14-slim@sha256:0741d101873c12ab927e6f8653feb8862b9bd58771177acb1b885b95141f91b4

WORKDIR /app

# Install poetry
RUN pip install poetry

# Copy project files
COPY pyproject.toml poetry.lock ./
COPY wayback_proxy ./wayback_proxy
COPY error_pages ./error_pages
COPY landing_page ./landing_page

# Install dependencies
RUN poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi

# Expose proxy port
EXPOSE 8888

# Run proxy
CMD ["python", "-m", "wayback_proxy"]
