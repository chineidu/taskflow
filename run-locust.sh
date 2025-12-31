#!/bin/bash

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

BASE_URL="${BASE_URL:-http://localhost:8000/api/v1}"
HOST="${HOST:-http://localhost:8000/api/v1}"

echo -e "${YELLOW}TaskFlow Load Testing with Locust${NC}\n"

# Check if API is running
echo "Checking if API is running at $HOST..."
if ! curl -s "$BASE_URL/health" > /dev/null 2>&1; then
    echo -e "${RED}❌ API is not responding at $HOST${NC}"
    echo "Please start the API first:"
    echo "  docker-compose up -d"
    exit 1
fi

echo -e "${GREEN}✓ API is running${NC}\n"

case "${1:-interactive}" in
    interactive)
        echo -e "${BLUE}Starting Locust in interactive mode...${NC}"
        echo "Open http://localhost:8089 in your browser"
        echo ""
        locust -f locustfile.py -H "$HOST"
        ;;
    
    headless)
        # Headless mode with predefined load
        USERS="${2:-50}"
        SPAWN_RATE="${3:-5}"
        DURATION="${4:-5m}"
        
        echo -e "${BLUE}Starting headless load test${NC}"
        echo "Users: $USERS | Spawn Rate: $SPAWN_RATE/sec | Duration: $DURATION"
        echo ""
        locust -f locustfile.py -H "$HOST" \
            --users "$USERS" \
            --spawn-rate "$SPAWN_RATE" \
            --run-time "$DURATION" \
            --headless
        ;;
    
    smoke)
        echo -e "${BLUE}Running smoke test (quick validation)${NC}"
        echo ""
        locust -f locustfile.py -H "$HOST" \
            --users 5 \
            --spawn-rate 2 \
            --run-time 30s \
            --headless
        ;;
    
    spike)
        echo -e "${BLUE}Running spike test${NC}"
        echo "Starting with 10 users, spiking to 100..."
        echo ""
        locust -f locustfile.py -H "$HOST" \
            --users 100 \
            --spawn-rate 20 \
            --run-time 5m \
            --headless
        ;;
    
    stress)
        echo -e "${RED}Running stress test (aggressive)${NC}"
        echo "Ramping up to 500 users..."
        echo ""
        locust -f locustfile.py -H "$HOST" \
            --users 500 \
            --spawn-rate 50 \
            --run-time 10m \
            --headless
        ;;
    
    *)
        echo "Usage: ./run-locust.sh [mode] [options]"
        echo ""
        echo "Modes:"
        echo "  interactive [port]      - Web UI mode (default, opens http://localhost:8089)"
        echo "  headless [users] [rate] [duration] - Run without UI (default: 50 users, 5/sec, 5min)"
        echo "  smoke                   - Quick 30-second smoke test"
        echo "  spike                   - Spike test (10 → 100 users)"
        echo "  stress                  - Stress test (up to 500 users, 10min)"
        echo ""
        echo "Examples:"
        echo "  ./run-locust.sh interactive"
        echo "  ./run-locust.sh headless 100 10 10m"
        echo "  HOST=http://api.example.com ./run-locust.sh smoke"
        exit 1
        ;;
esac
