FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

WORKDIR /app

# Install poetry
RUN pip install poetry

# Copy project files
COPY pyproject.toml ./
COPY admin_service ./admin_service

# Install dependencies
RUN poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi

# Expose admin port
EXPOSE 8080

# Run admin service
CMD ["python", "-m", "admin_service", "--host", "0.0.0.0", "--port", "8080"]
