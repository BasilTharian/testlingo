# Testlingo - container for online hosting (Render, Railway, Fly.io, any Docker host)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    BLOCK_PRIVATE_NETWORKS=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY . .
RUN useradd -m tester && chown -R tester /app /ms-playwright
USER tester

# The host sets PORT. Threads let the server's own browser load the built-in demo pages
# while a test is running; long timeout because a test can take a minute or two.
CMD gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 8 --timeout 300
