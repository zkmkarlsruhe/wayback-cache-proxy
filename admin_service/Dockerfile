FROM python:3.14-slim@sha256:c3e521df8b2b498a7a682e7e18676771cb80c6b75b8699af886b2d554ce40151

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
