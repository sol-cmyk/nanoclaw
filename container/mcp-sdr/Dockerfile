# MCP SDR sidecar — TCP server wrapping FastMCP subprocess
# Runtime: --read-only --cap-drop=ALL --security-opt=no-new-privileges --tmpfs /tmp
FROM python:3.12-slim

# Install MCP server dependencies (no dev deps)
RUN pip install --no-cache-dir mcp pydantic httpx

# Create non-root user with fixed UID
RUN useradd -r -u 9999 -s /bin/false mcpuser

# Copy MCP server code
COPY mcp-server/ /app/mcp-server/

# Copy TCP server wrapper
COPY tcp_server.py /app/tcp_server.py

USER mcpuser
EXPOSE 9000
CMD ["python3", "/app/tcp_server.py"]
