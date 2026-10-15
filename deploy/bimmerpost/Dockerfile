FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./requirements.txt
COPY deploy/bimmerpost/requirements.txt ./deploy/bimmerpost/requirements.txt
RUN pip install --no-cache-dir -r deploy/bimmerpost/requirements.txt \
    && python -m playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 scraper \
    && chmod -R a+rX /root/.cache/ms-playwright \
    && mv /root/.cache/ms-playwright /opt/ms-playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
COPY listing_utils.py ./listing_utils.py
COPY database ./database
COPY scraper ./scraper
USER scraper
CMD ["python", "-m", "scraper.bimmerpost_probe"]
