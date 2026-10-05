FROM node:22-bookworm-slim AS web
WORKDIR /build
COPY platform-web /build/platform-web
COPY scripts/platform/build_web.mjs /build/scripts/platform/build_web.mjs
RUN npm install --prefix /deps --no-audit --no-fund react@19.2.0 react-dom@19.2.0 esbuild@0.28.0
ENV AETHER_WEB_DEPENDENCIES=/deps AETHER_WEB_DIST=/web AETHER_PUBLIC_PATH=/agent
RUN node scripts/platform/build_web.mjs

FROM python:3.13-slim
WORKDIR /app
COPY deploy/platform/requirements-cloud.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY src/aether_platform /app/src/aether_platform
COPY src/aether_agent_memory /app/src/aether_agent_memory
COPY scripts/platform/run_cloud.py /app/run_cloud.py
COPY --from=web /web /app/web
ENV PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 19010
CMD ["python", "/app/run_cloud.py", "--config", "/config/private.json"]
