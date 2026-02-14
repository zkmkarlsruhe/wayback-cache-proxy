FROM python:3.12-slim

WORKDIR /app

# Install poetry
RUN pip install poetry

# Copy project files
COPY pyproject.toml poetry.lock ./
COPY wayback_proxy ./wayback_proxy

# Install dependencies
RUN poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi

# Expose proxy port
EXPOSE 8888

# Run proxy
CMD ["python", "-m", "wayback_proxy"]
