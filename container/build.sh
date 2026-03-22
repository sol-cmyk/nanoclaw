#!/bin/bash
# Build all NanoClaw container images

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TAG="${1:-latest}"
CONTAINER_RUNTIME="${CONTAINER_RUNTIME:-docker}"

# --- Agent container ---
echo "Building NanoClaw agent container image..."
echo "Image: nanoclaw-agent:${TAG}"
${CONTAINER_RUNTIME} build -t "nanoclaw-agent:${TAG}" .

# --- MCP SDR sidecar ---
if [ -d "mcp-sdr" ]; then
  echo ""
  echo "Building MCP SDR sidecar image..."
  echo "Image: nanoclaw-mcp-sdr:${TAG}"
  ${CONTAINER_RUNTIME} build -t "nanoclaw-mcp-sdr:${TAG}" mcp-sdr/
fi

echo ""
echo "Build complete!"
echo "Images: nanoclaw-agent:${TAG}, nanoclaw-mcp-sdr:${TAG}"
echo ""
echo "Test with:"
echo "  echo '{\"prompt\":\"What is 2+2?\",\"groupFolder\":\"test\",\"chatJid\":\"test@g.us\",\"isMain\":false}' | ${CONTAINER_RUNTIME} run -i nanoclaw-agent:${TAG}"
