#!/bin/bash
# Start local GraphHopper routing server
# Usage: ./graphhopper/start.sh
set -e
cd "$(dirname "$0")"

echo "Starting GraphHopper on http://localhost:8991 ..."
exec java -Xmx512m -Xms256m \
  -jar graphhopper-web.jar \
  server config.yml
