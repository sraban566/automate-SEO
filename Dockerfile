FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/sraban566/automate-SEO" \
      org.opencontainers.image.title="automate-SEO" \
      org.opencontainers.image.description="Technical SEO and AI-search audit CLI with Chromium" \
      org.opencontainers.image.licenses="MIT"

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
WORKDIR /opt/automate-seo
COPY pyproject.toml README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
COPY src/ ./src/
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && python -m pip install --no-cache-dir '.[browser,reports]' \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --user-group auditor \
    && mkdir /work \
    && chown auditor:auditor /work

WORKDIR /work
USER auditor
ENTRYPOINT ["automate-seo"]
CMD ["--help"]
