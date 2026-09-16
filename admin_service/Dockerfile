FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

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
