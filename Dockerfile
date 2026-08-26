FROM python:3.13-slim

ARG INSTALL_B1_SIDECAR=false
ARG INSTALL_B1_OPENVINO=false
ARG INSTALL_B2_DATASET_TOOLS=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples
COPY scripts ./scripts
COPY engine/proto ./engine/proto

RUN pip install --no-cache-dir -e . \
    && if [ "$INSTALL_B1_SIDECAR" = "true" ] && [ "$INSTALL_B1_OPENVINO" = "true" ]; then \
        pip install --no-cache-dir -e ".[b1-accelerated]"; \
    elif [ "$INSTALL_B1_SIDECAR" = "true" ]; then \
        pip install --no-cache-dir -e ".[b1-sidecar]"; \
    fi \
    && if [ "$INSTALL_B2_DATASET_TOOLS" = "true" ]; then pip install --no-cache-dir -e ".[b2-dataset]"; fi \
    && python scripts/generate_proto.py

CMD ["python", "scripts/p3_service.py"]
