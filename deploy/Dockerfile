FROM python:3.11-slim

WORKDIR /app

# System dependencies
# - rng-tools: for rngd to feed /dev/hwrng into kernel entropy pool
# - build-essential: needed for some Python native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    rng-tools \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY python/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Application source
COPY python/ /app/python/
RUN pip install --no-cache-dir -e /app/python/

ENV ANDYRIA_DATA_DIR=/data/andyria
ENV ANDYRIA_NODE_ID=andyria-node-0

VOLUME ["/data/andyria"]
EXPOSE 7700

ENTRYPOINT ["python", "-m", "andyria", "serve", "--host", "0.0.0.0", "--port", "7700"]
