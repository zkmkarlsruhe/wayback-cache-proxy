FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

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
