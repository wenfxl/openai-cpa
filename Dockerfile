FROM python:3.11-slim

WORKDIR /app

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG NO_PROXY

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    xdg-utils \
    libdbus-glib-1-2 \
    libxt6 \
    libxrender1 \
    libpci3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

RUN --mount=type=secret,id=github_token \
    GITHUB_TOKEN=$(cat /run/secrets/github_token) python -m camoufox fetch

RUN --mount=type=secret,id=github_token \
    GITHUB_TOKEN=$(cat /run/secrets/github_token) python -m cloakbrowser install && \
    python -c "from pathlib import Path; from cloakbrowser.geoip import _ensure_geoip_db; p=_ensure_geoip_db(); assert p and Path(p).is_file() and Path(p).stat().st_size > 1000000, 'GeoIP download failed'; print(p)"

COPY . .

RUN rm -rf utils/auth_core/*.py 2>/dev/null || true

EXPOSE 8000
ENV PYTHONUNBUFFERED=1 \
    GROK_LOCAL_SOLVER_BROWSER=camoufox \
    CLOAKBROWSER_AUTO_UPDATE=false

CMD ["python", "wfxl_openai_regst.py"]
