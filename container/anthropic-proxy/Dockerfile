# Anthropic-only credential proxy (Option C)
# No build step, no deps, just Node.js stdlib
# Runtime: --read-only --cap-drop=ALL --security-opt=no-new-privileges
FROM node:22-slim

# Create non-root user with fixed UID
RUN useradd -r -u 9999 -s /bin/false proxyuser

COPY proxy.mjs /app/proxy.mjs

USER proxyuser
EXPOSE 3001
CMD ["node", "/app/proxy.mjs"]
