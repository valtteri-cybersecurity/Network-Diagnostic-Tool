FROM ubuntu:24.04

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        iputils-ping \
        iproute2 \
        bind9-dnsutils \
        libc-bin \
        hostname \
        network-manager && \
    rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

COPY requirements-dev.txt /app/

RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

RUN python -m pip install --no-cache-dir -e .

CMD ["bash"]
