# Two stages: dependencies are built in the first, and only the virtual environment
# is copied into the second, so the image carries no compilers or build caches.
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN python -m venv /venv && /venv/bin/pip install .

FROM python:3.12-slim
ENV PATH=/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DOC_EXTRACT_DB=/var/lib/doc-extract/doc_extract.db \
    DOC_EXTRACT_STORAGE=/var/lib/doc-extract/documents
# Run as an unprivileged user that owns only its data directory.
RUN useradd --system --uid 10001 --home-dir /var/lib/doc-extract app \
    && mkdir -p /var/lib/doc-extract \
    && chown app /var/lib/doc-extract
COPY --from=build /venv /venv
USER app
WORKDIR /var/lib/doc-extract
VOLUME /var/lib/doc-extract
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["doc-extract", "serve", "--host", "0.0.0.0", "--port", "8000"]
