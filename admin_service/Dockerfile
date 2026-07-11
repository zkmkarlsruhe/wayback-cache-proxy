FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

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
