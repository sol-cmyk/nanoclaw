# Minimal credential proxy sidecar
# No build step, no deps, just Node.js stdlib
FROM node:22-slim

RUN useradd -r -s /bin/false proxyuser

COPY proxy.mjs /app/proxy.mjs

USER proxyuser
EXPOSE 3001
CMD ["node", "/app/proxy.mjs"]
