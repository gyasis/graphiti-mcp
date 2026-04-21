# Auto-Start FalkorDB Setup Guide

This guide helps you configure FalkorDB to automatically start when your computer boots.

## Changes Made

✅ Updated `docker-compose.yml` with `restart: always` policy
✅ Updated `docker/docker-compose-falkordb.yml` with `restart: always` policy

This ensures FalkorDB will automatically restart:
- After system reboots
- After Docker daemon restarts
- Even if the container is manually stopped (it will restart)

## Setup Instructions

### Option 1: Docker Desktop (Recommended for WSL2)

1. **Install Docker Desktop for Windows** (if not already installed)
   - Download from: https://www.docker.com/products/docker-desktop/
   - Enable WSL2 integration in Docker Desktop settings

2. **Configure Docker Desktop to start on boot:**
   - Open Docker Desktop
   - Go to Settings → General
   - Check "Start Docker Desktop when you log in"

3. **Start FalkorDB:**
   ```bash
   cd /home/gyasisutton/dev/tools/graphiti-mcp
   docker compose up -d
   ```

4. **Verify it's running:**
   ```bash
   docker ps
   ```
   You should see `falkordb-graphiti` in the list.

### Option 2: Docker in WSL2 (Native)

1. **Install Docker in WSL2:**
   ```bash
   curl -fsSL https://get.docker.com -o get-docker.sh
   sudo sh get-docker.sh
   sudo usermod -aG docker $USER
   ```

2. **Enable Docker service to start on boot:**
   ```bash
   sudo systemctl enable docker
   sudo systemctl start docker
   ```

3. **Start FalkorDB:**
   ```bash
   cd /home/gyasisutton/dev/tools/graphiti-mcp
   docker compose up -d
   ```

### Option 3: Systemd Service (Alternative)

If you want more control, create a systemd service:

1. **Create service file:**
   ```bash
   sudo nano /etc/systemd/system/falkordb-graphiti.service
   ```

2. **Add this content:**
   ```ini
   [Unit]
   Description=FalkorDB for Graphiti MCP
   Requires=docker.service
   After=docker.service

   [Service]
   Type=oneshot
   RemainAfterExit=yes
   WorkingDirectory=/home/gyasisutton/dev/tools/graphiti-mcp
   ExecStart=/usr/bin/docker compose up -d
   ExecStop=/usr/bin/docker compose down
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   ```

3. **Enable and start:**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable falkordb-graphiti.service
   sudo systemctl start falkordb-graphiti.service
   ```

## Verify Auto-Start

1. **Reboot your computer**
2. **After reboot, check if FalkorDB is running:**
   ```bash
   docker ps | grep falkordb
   ```

   You should see the container running.

3. **Test the connection:**
   ```bash
   docker exec falkordb-graphiti redis-cli PING
   ```
   Should return: `PONG`

## Troubleshooting

### Container not starting after reboot

1. **Check Docker is running:**
   ```bash
   docker ps
   ```

2. **Check container status:**
   ```bash
   docker ps -a | grep falkordb
   ```

3. **View logs:**
   ```bash
   docker logs falkordb-graphiti
   ```

4. **Manually start if needed:**
   ```bash
   cd /home/gyasisutton/dev/tools/graphiti-mcp
   docker compose up -d
   ```

### Data Persistence

Your data is stored in a Docker volume (`falkordb_data`), so it persists across container restarts. The volume is configured in `docker-compose.yml`.

## Current Configuration

- **Restart Policy:** `always` (will restart automatically)
- **Data Volume:** `falkordb_data` (persists data)
- **Port:** `6379` (standard Redis/FalkorDB port)
- **Persistence:** AOF (Append Only File) enabled for data durability

