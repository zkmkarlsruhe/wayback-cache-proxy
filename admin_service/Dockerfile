FROM python:3.14-slim@sha256:0741d101873c12ab927e6f8653feb8862b9bd58771177acb1b885b95141f91b4

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
