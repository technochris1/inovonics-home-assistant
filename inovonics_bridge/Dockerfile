FROM python:3.12-slim-bookworm AS builder

# The Supervisor builds from this folder, so install the tested bridge revision.
ARG BRIDGE_REVISION=7d2a588964664956b9d90ecbad6a06b71686bee0
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/venv \
    && git clone --filter=blob:none --no-checkout https://github.com/technochris1/inovonics-home-assistant.git /bridge \
    && git -C /bridge checkout --detach "$BRIDGE_REVISION" \
    && /opt/venv/bin/pip install --no-cache-dir -r /bridge/requirements.txt '/bridge' 'pyserial==3.5'

FROM python:3.12-slim-bookworm
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY --from=builder /opt/venv /opt/venv
COPY run.py control.py panel.py debug_relay.py /opt/inovonics/
CMD ["python", "/opt/inovonics/run.py"]
