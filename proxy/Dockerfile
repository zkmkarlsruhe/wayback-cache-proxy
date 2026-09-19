FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2

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
