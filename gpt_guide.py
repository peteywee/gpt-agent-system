# Nexus Orchestrator Deployment Guide

## Table of Contents
1. [Introduction & Overview](#introduction--overview)
2. [Local Development Environment Setup](#local-development-environment-setup)
3. [DigitalOcean Droplet Provisioning](#digitalocean-droplet-provisioning)
4. [Deploying to DigitalOcean](#deploying-to-digitalocean)
5. [Nginx Reverse Proxy & SSL/TLS Setup](#nginx-reverse-proxy--ssltls-setup)
6. [Cloudflare Integration](#cloudflare-integration)
7. [Next Steps & Ongoing Management](#next-steps--ongoing-management)

---

## 1. Introduction & Overview

### Purpose of the Nexus Orchestrator
The Nexus Orchestrator (gpt-nexus) serves as the central command and coordination hub for a distributed AI agent system. It acts as the primary communication backbone, managing agent registration, health monitoring, and facilitating inter-agent communication through a robust microservices architecture.

### Microservice Architecture
Our system consists of several key components working in harmony:

**Core Services:**
- **gpt-nexus**: Central orchestrator with FastAPI backend, WebSocket support, and agent management
- **gpt-agent_comms**: Handles communication protocols between agents
- **gpt-agent_ops_execution**: Manages operational task execution
- **gpt-agent_strategy**: Handles strategic planning and decision-making
- **gpt-agent_research**: Manages research and data gathering operations

**Infrastructure Services:**
- **PostgreSQL**: Primary database for agent registration, heartbeats, and persistent data
- **Redis**: High-performance cache and pub/sub messaging system
- **Nginx**: Reverse proxy, load balancer, and SSL termination
- **Cloudflare**: CDN, DDoS protection, and free SSL certificates

### Architecture Data Flow
1. Agents register with the Nexus Orchestrator via REST API
2. Heartbeat signals maintain agent health status in PostgreSQL
3. Redis pub/sub enables real-time inter-agent communication
4. Nginx proxies external requests to internal Docker services
5. Cloudflare provides security and performance optimization

### Deployment Goal
Achieve a robust, scalable, and secure production deployment on DigitalOcean that can handle multiple concurrent agents with high availability and security best practices.

---

## 2. Local Development Environment Setup

### Prerequisites

**Required Software:**
- **Docker Desktop** (Windows/macOS) or **Docker Engine & Docker Compose** (Linux)
- **Git** for version control
- **psql** client for PostgreSQL interaction
- **curl** for API testing

**Installation Commands (Ubuntu/Debian):**
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Install Docker Compose
sudo apt install docker-compose-plugin

# Install PostgreSQL client
sudo apt install postgresql-client

# Logout and login to apply docker group changes
```

### Project Structure

Create the following directory structure:

```
nexus_project_root/
├── gpt-nexus/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
├── docker-compose.yml
└── .env
```

### Core Application Files

**gpt-nexus/Dockerfile:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

**gpt-nexus/requirements.txt:**
```txt
fastapi==0.104.1
uvicorn[standard]==0.24.0
websockets==12.0
psycopg2-binary==2.9.9
redis==5.0.1
pydantic==2.5.0
python-multipart==0.0.6
aiofiles==23.2.1
```

**gpt-nexus/main.py:**
```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import psycopg2
import redis
import json
import asyncio
from datetime import datetime, timedelta
import os
import logging
from typing import List, Dict, Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Nexus Orchestrator", version="1.0.0")

# Database configuration
DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "postgres"),
    "database": os.getenv("POSTGRES_DB", "nexus_db"),
    "user": os.getenv("POSTGRES_USER", "nexus_user"),
    "password": os.getenv("POSTGRES_PASSWORD", "nexus_password"),
    "port": int(os.getenv("POSTGRES_PORT", "5432"))
}

# Redis configuration
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# Pydantic models
class AgentRegistration(BaseModel):
    agent_id: str
    agent_type: str
    capabilities: List[str]
    metadata: Optional[Dict] = {}

class AgentHeartbeat(BaseModel):
    agent_id: str
    status: str = "active"
    last_activity: Optional[str] = None

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        
    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)
        
    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

def get_db_connection():
    """Get PostgreSQL database connection"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise HTTPException(status_code=500, detail="Database connection failed")

def get_redis_connection():
    """Get Redis connection"""
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        r.ping()  # Test connection
        return r
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        raise HTTPException(status_code=500, detail="Redis connection failed")

@app.on_event("startup")
async def startup_event():
    """Initialize database tables on startup"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Create agents table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id SERIAL PRIMARY KEY,
                agent_id VARCHAR(255) UNIQUE NOT NULL,
                agent_type VARCHAR(100) NOT NULL,
                capabilities TEXT[],
                metadata JSONB,
                status VARCHAR(50) DEFAULT 'active',
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Create index for performance
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_agents_agent_id ON agents(agent_id);
            CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
            CREATE INDEX IF NOT EXISTS idx_agents_last_heartbeat ON agents(last_heartbeat);
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Database initialized successfully")
        
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")

@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "Nexus Orchestrator is running", "version": "1.0.0"}

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Test database connection
        conn = get_db_connection()
        conn.close()
        
        # Test Redis connection
        r = get_redis_connection()
        r.ping()
        
        return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )

@app.post("/agents/register")
async def register_agent(agent: AgentRegistration):
    """Register a new agent"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Insert or update agent registration
        cur.execute("""
            INSERT INTO agents (agent_id, agent_type, capabilities, metadata, status, last_heartbeat)
            VALUES (%s, %s, %s, %s, 'active', CURRENT_TIMESTAMP)
            ON CONFLICT (agent_id) 
            DO UPDATE SET 
                agent_type = EXCLUDED.agent_type,
                capabilities = EXCLUDED.capabilities,
                metadata = EXCLUDED.metadata,
                status = 'active',
                last_heartbeat = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP;
        """, (agent.agent_id, agent.agent_type, agent.capabilities, json.dumps(agent.metadata)))
        
        conn.commit()
        cur.close()
        conn.close()
        
        # Broadcast registration to WebSocket clients
        await manager.broadcast(f"Agent {agent.agent_id} registered")
        
        logger.info(f"Agent {agent.agent_id} registered successfully")
        return {"status": "success", "message": f"Agent {agent.agent_id} registered"}
        
    except Exception as e:
        logger.error(f"Agent registration failed: {e}")
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")

@app.post("/agents/heartbeat")
async def agent_heartbeat(heartbeat: AgentHeartbeat):
    """Update agent heartbeat"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Update last heartbeat
        cur.execute("""
            UPDATE agents 
            SET last_heartbeat = CURRENT_TIMESTAMP, 
                status = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE agent_id = %s;
        """, (heartbeat.status, heartbeat.agent_id))
        
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Agent not found")
            
        conn.commit()
        cur.close()
        conn.close()
        
        return {"status": "success", "message": f"Heartbeat updated for {heartbeat.agent_id}"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Heartbeat update failed: {e}")
        raise HTTPException(status_code=500, detail=f"Heartbeat update failed: {str(e)}")

@app.get("/agents/list_active")
async def list_active_agents():
    """List all active agents"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Get agents with heartbeat within last 5 minutes
        cur.execute("""
            SELECT agent_id, agent_type, capabilities, status, last_heartbeat, metadata
            FROM agents 
            WHERE last_heartbeat > CURRENT_TIMESTAMP - INTERVAL '5 minutes'
            AND status = 'active'
            ORDER BY last_heartbeat DESC;
        """)
        
        agents = []
        for row in cur.fetchall():
            agents.append({
                "agent_id": row[0],
                "agent_type": row[1],
                "capabilities": row[2],
                "status": row[3],
                "last_heartbeat": row[4].isoformat(),
                "metadata": row[5]
            })
            
        cur.close()
        conn.close()
        
        return {"active_agents": agents, "count": len(agents)}
        
    except Exception as e:
        logger.error(f"Failed to list active agents: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list agents: {str(e)}")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time communication"""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            # Echo message back to sender and broadcast to others
            await manager.send_personal_message(f"Echo: {data}", websocket)
            await manager.broadcast(f"Broadcast: {data}")
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)
```

**docker-compose.yml:**
```yaml
version: '3.8'

services:
  gpt-nexus:
    build: ./gpt-nexus
    ports:
      - "8000:8000"
    environment:
      - POSTGRES_HOST=postgres
      - POSTGRES_DB=${POSTGRES_DB}
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - REDIS_HOST=redis
    depends_on:
      - postgres
      - redis
    volumes:
      - ./gpt-nexus:/app
    restart: unless-stopped
    networks:
      - nexus-network

  postgres:
    image: postgres:15-alpine
    environment:
      - POSTGRES_DB=${POSTGRES_DB}
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql
    ports:
      - "5432:5432"
    restart: unless-stopped
    networks:
      - nexus-network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 30s
      timeout: 10s
      retries: 3

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    restart: unless-stopped
    networks:
      - nexus-network
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 30s
      timeout: 10s
      retries: 3
    command: redis-server --appendonly yes

volumes:
  postgres_data:
  redis_data:

networks:
  nexus-network:
    driver: bridge
```

**.env File (CRITICAL SECURITY):**
```env
# PostgreSQL Configuration
POSTGRES_DB=nexus_db
POSTGRES_USER=nexus_user
POSTGRES_PASSWORD=your_secure_password_here_123!

# Never commit this file to version control!
# Add .env to your .gitignore file immediately!
```

**SECURITY WARNING**: The `.env` file contains sensitive credentials and must NEVER be committed to version control. Create a `.gitignore` file immediately:

```gitignore
.env
__pycache__/
*.pyc
*.pyo
*.log
.DS_Store
```

### Running Locally

**Step-by-Step Local Deployment:**

1. **Navigate to project directory:**
```bash
cd nexus_project_root
```

2. **Build and start services:**
```bash
docker-compose up -d --build
```

3. **Verify services are running:**
```bash
docker-compose ps
```

4. **Test the health endpoint:**
```bash
curl http://localhost:8000/health
```

5. **Test agent registration:**
```bash
curl -X POST "http://localhost:8000/agents/register" \
     -H "Content-Type: application/json" \
     -d '{
       "agent_id": "test-agent-001", 
       "agent_type": "research", 
       "capabilities": ["web_search", "data_analysis"]
     }'
```

6. **Test heartbeat:**
```bash
curl -X POST "http://localhost:8000/agents/heartbeat" \
     -H "Content-Type: application/json" \
     -d '{
       "agent_id": "test-agent-001", 
       "status": "active"
     }'
```

7. **List active agents:**
```bash
curl http://localhost:8000/agents/list_active
```

8. **Stop services when done:**
```bash
docker-compose down
```

### Troubleshooting Common Local Issues

**Port 5432 already in use:**
```bash
sudo lsof -i :5432
sudo service postgresql stop
```

**Docker permission denied:**
```bash
sudo usermod -aG docker $USER
# Logout and login again
```

**Dockerfile parse errors:**
- Ensure no Windows line endings (CRLF)
- Verify indentation consistency
- Check for missing quotes in environment variables

---

## 3. DigitalOcean Droplet Provisioning

### Creating the Droplet

**Step-by-Step DigitalOcean Setup:**

1. **Log into DigitalOcean Dashboard**
2. **Click "Create" → "Droplets"**
3. **Configure Droplet:**
   - **Image**: Ubuntu 22.04 LTS x64
   - **Plan**: Basic plan, $12/month (2 GB RAM, 1 vCPU, 50 GB SSD)
   - **Region**: New York 3 (closest to Arlington, Virginia)
   - **VPC Network**: Default
   - **Authentication**: SSH Key (highly recommended over password)
   - **Hostname**: nexus-orchestrator-prod

4. **Add SSH Key:**
   - Generate if needed: `ssh-keygen -t ed25519 -C "your_email@example.com"`
   - Copy public key: `cat ~/.ssh/id_ed25519.pub`
   - Add to DigitalOcean

### Initial Droplet Setup

**Connect via SSH:**
```bash
ssh root@your_droplet_ip
```

**System Updates:**
```bash
apt update && apt upgrade -y
```

**Install Docker and Docker Compose:**
```bash
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Install Docker Compose
apt install docker-compose-plugin

# Create non-root user (recommended)
adduser nexus
usermod -aG sudo nexus
usermod -aG docker nexus

# Switch to non-root user
su - nexus
```

### Firewall Configuration (UFW)

**Understanding UFW's Role:**
UFW (Uncomplicated Firewall) provides essential security by blocking unauthorized access while allowing legitimate traffic. We'll configure it to allow SSH from your personal IP and HTTP/HTTPS from Cloudflare's IP ranges only.

**Configure UFW Rules:**

```bash
# Reset UFW to defaults
sudo ufw --force reset

# Set default policies
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH from your personal IP (replace with your actual IP)
sudo ufw allow from YOUR_PERSONAL_IP to any port 22

# Allow HTTP and HTTPS from Cloudflare IP ranges
# Cloudflare IPv4 ranges (update these periodically)
sudo ufw allow from 173.245.48.0/20 to any port 80
sudo ufw allow from 103.21.244.0/22 to any port 80
sudo ufw allow from 103.22.200.0/22 to any port 80
sudo ufw allow from 103.31.4.0/22 to any port 80
sudo ufw allow from 141.101.64.0/18 to any port 80
sudo ufw allow from 108.162.192.0/18 to any port 80
sudo ufw allow from 190.93.240.0/20 to any port 80
sudo ufw allow from 188.114.96.0/20 to any port 80
sudo ufw allow from 197.234.240.0/22 to any port 80
sudo ufw allow from 198.41.128.0/17 to any port 80
sudo ufw allow from 162.158.0.0/15 to any port 80
sudo ufw allow from 104.16.0.0/13 to any port 80
sudo ufw allow from 104.24.0.0/14 to any port 80
sudo ufw allow from 172.64.0.0/13 to any port 80
sudo ufw allow from 131.0.72.0/22 to any port 80

# Repeat same ranges for port 443
sudo ufw allow from 173.245.48.0/20 to any port 443
sudo ufw allow from 103.21.244.0/22 to any port 443
sudo ufw allow from 103.22.200.0/22 to any port 443
sudo ufw allow from 103.31.4.0/22 to any port 443
sudo ufw allow from 141.101.64.0/18 to any port 443
sudo ufw allow from 108.162.192.0/18 to any port 443
sudo ufw allow from 190.93.240.0/20 to any port 443
sudo ufw allow from 188.114.96.0/20 to any port 443
sudo ufw allow from 197.234.240.0/22 to any port 443
sudo ufw allow from 198.41.128.0/17 to any port 443
sudo ufw allow from 162.158.0.0/15 to any port 443
sudo ufw allow from 104.16.0.0/13 to any port 443
sudo ufw allow from 104.24.0.0/14 to any port 443
sudo ufw allow from 172.64.0.0/13 to any port 443
sudo ufw allow from 131.0.72.0/22 to any port 443

# Enable UFW
sudo ufw enable

# Check status
sudo ufw status verbose
```

**IMPORTANT**: Ports 8000 (FastAPI), 6379 (Redis), and 5432 (PostgreSQL) are intentionally NOT exposed publicly. They remain accessible only within the Docker network and to localhost via Nginx proxy.

**Cloudflare IP Updates:**
Cloudflare IP ranges change periodically. Create a script to update them:

```bash
# Create update script
cat > ~/update_cloudflare_ips.sh << 'EOF'
#!/bin/bash
# Update Cloudflare IP ranges in UFW

# Get current Cloudflare IPs
curl -s https://www.cloudflare.com/ips-v4 > /tmp/cf_ips_v4.txt

# Remove old Cloudflare rules (you may need to adjust this)
# sudo ufw delete allow from 173.245.48.0/20

# Add new rules (implement as needed)
while read ip; do
    sudo ufw allow from $ip to any port 80
    sudo ufw allow from $ip to any port 443
done < /tmp/cf_ips_v4.txt

sudo ufw reload
EOF

chmod +x ~/update_cloudflare_ips.sh
```

---

## 4. Deploying to DigitalOcean

### Transferring Project Files

**Using SCP to copy project:**
```bash
# From your local machine
scp -r nexus_project_root/ nexus@your_droplet_ip:~/
```

**Alternative using rsync (more efficient for updates):**
```bash
rsync -avz --progress nexus_project_root/ nexus@your_droplet_ip:~/nexus_project_root/
```

### Running Docker Compose on Droplet

**Navigate and start services:**
```bash
# SSH into droplet
ssh nexus@your_droplet_ip

# Navigate to project
cd ~/nexus_project_root

# Start services
docker-compose up -d --build

# Verify containers are running
docker ps

# Check logs if needed
docker-compose logs -f gpt-nexus
```

### PostgreSQL Schema Initialization

The schema is automatically initialized on startup, but you can verify manually:

**Connect to PostgreSQL container:**
```bash
# Access PostgreSQL shell
docker exec -it nexus_project_root_postgres_1 psql -U nexus_user -d nexus_db
```

**Verify table creation:**
```sql
-- List tables
\dt

-- Describe agents table
\d agents

-- Check if table has data
SELECT COUNT(*) FROM agents;

-- Exit psql
\q
```

**Manual table creation (if needed):**
```sql
CREATE TABLE IF NOT EXISTS agents (
    id SERIAL PRIMARY KEY,
    agent_id VARCHAR(255) UNIQUE NOT NULL,
    agent_type VARCHAR(100) NOT NULL,
    capabilities TEXT[],
    metadata JSONB,
    status VARCHAR(50) DEFAULT 'active',
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agents_agent_id ON agents(agent_id);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_agents_last_heartbeat ON agents(last_heartbeat);
```

---

## 5. Nginx Reverse Proxy & SSL/TLS Setup

### Purpose of Nginx

Nginx serves multiple critical functions in our architecture:
- **Port Management**: Handles standard HTTP (80) and HTTPS (443) ports
- **Reverse Proxy**: Routes external requests to internal Docker services on port 8000
- **SSL Termination**: Handles SSL/TLS encryption and decryption
- **WebSocket Support**: Proxies WebSocket connections with proper headers
- **Performance**: Serves static files and caches responses efficiently

### Nginx Installation

```bash
# Install Nginx
sudo apt update
sudo apt install nginx

# Start and enable Nginx
sudo systemctl start nginx
sudo systemctl enable nginx

# Check status
sudo systemctl status nginx
```

### Nginx Configuration

**Remove default configuration:**
```bash
sudo rm /etc/nginx/sites-enabled/default
```

**Create new server block for topselfservicepros.com:**
```bash
sudo nano /etc/nginx/sites-available/topselfservicepros.com
```

**Nginx server block configuration:**
```nginx
# HTTP server block - redirects to HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name topselfservicepros.com www.topselfservicepros.com;
    
    # Redirect all HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

# HTTPS server block
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name topselfservicepros.com www.topselfservicepros.com;

    # SSL Configuration (will be updated with Cloudflare Origin Certificate)
    ssl_certificate /etc/ssl/certs/cloudflare_origin.pem;
    ssl_certificate_key /etc/ssl/private/cloudflare_origin.key;
    
    # SSL Security Settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-AES128-SHA256:ECDHE-RSA-AES256-SHA384;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # Security Headers
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload";

    # Proxy settings for FastAPI application
    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # WebSocket specific configuration
    location /ws {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket specific timeouts
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
    }

    # Health check endpoint (optional direct access)
    location /health {
        proxy_pass http://localhost:8000/health;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Logging
    access_log /var/log/nginx/topselfservicepros.access.log;
    error_log /var/log/nginx/topselfservicepros.error.log;
}
```

**Enable the site:**
```bash
sudo ln -s /etc/nginx/sites-available/topselfservicepros.com /etc/nginx/sites-enabled/

# Test configuration
sudo nginx -t

# If test passes, restart Nginx
sudo systemctl restart nginx
```

---

## 6. Cloudflare Integration

### Purpose of Cloudflare

Cloudflare provides multiple benefits for our deployment:
- **Free SSL Certificates**: Eliminates the need for Let's Encrypt management
- **DDoS Protection**: Automatic protection against distributed attacks
- **Performance Optimization**: Global CDN reduces latency
- **Security Features**: Web Application Firewall (WAF) and bot protection
- **Analytics**: Detailed traffic and performance insights

### Cloudflare Setup Steps

#### 1. Add Domain to Cloudflare

1. **Sign up/Login to Cloudflare**
2. **Click "Add a Site"**
3. **Enter domain**: `topselfservicepros.com`
4. **Select Free Plan**
5. **Continue with setup**

#### 2. Update Nameservers

Cloudflare will provide nameservers like:
- `clark.ns.cloudflare.com`
- `ruth.ns.cloudflare.com`

**Update at your domain registrar:**
1. Log into your domain registrar (GoDaddy, Namecheap, etc.)
2. Navigate to DNS management
3. Replace existing nameservers with Cloudflare's
4. Save changes (propagation takes 24-48 hours)

#### 3. Configure DNS Records

In Cloudflare DNS settings, create:

**A Records (set to orange cloud/proxied):**
- `@` (root domain) → Your Droplet IP
- `www` → Your Droplet IP

**IMPORTANT**: Ensure the cloud icon is **orange** (proxied) not gray (DNS only).

#### 4. SSL/TLS Configuration

**Set SSL/TLS Mode:**
1. Go to **SSL/TLS** tab in Cloudflare
2. Select **"Full (strict)"** mode
3. This ensures end-to-end encryption between Cloudflare and your server

#### 5. Generate Cloudflare Origin Certificate

**Why Origin Certificates?**
Origin certificates provide SSL encryption between Cloudflare and your server while being automatically trusted by Cloudflare.

**Generate Certificate:**
1. Go to **SSL/TLS** → **Origin Server**
2. Click **"Create Certificate"**
3. **Configuration:**
   - Private key type: RSA (2048)
   - Hostnames: `*.topselfservicepros.com`, `topselfservicepros.com`
   - Certificate validity: 15 years
4. **Click "Create"**

**Install Certificate on Server:**

Copy the certificate content and create files:

```bash
# Create certificate file
sudo nano /etc/ssl/certs/cloudflare_origin.pem
# Paste the certificate content (-----BEGIN CERTIFICATE-----...-----END CERTIFICATE-----)

# Create private key file
sudo nano /etc/ssl/private/cloudflare_origin.key
# Paste the private key content (-----BEGIN PRIVATE KEY-----...-----END PRIVATE KEY-----)

# Set secure permissions
sudo chmod 644 /etc/ssl/certs/cloudflare_origin.pem
sudo chmod 600 /etc/ssl/private/cloudflare_origin.key
sudo chown root:root /etc/ssl/certs/cloudflare_origin.pem
sudo chown root:root /etc/ssl/private/cloudflare_origin.key
```

#### 6. Update Nginx for SSL

The Nginx configuration provided earlier already includes the SSL certificate paths. Test and restart:

```bash
# Test Nginx configuration
sudo nginx -t

# If successful, restart Nginx
sudo systemctl restart nginx

# Check Nginx status
sudo systemctl status nginx
```

#### 7. Verify HTTPS Setup

**Test endpoints:**
```bash
# Test from server
curl -k https://localhost/health

# Test externally (after DNS propagation)
curl https://topselfservicepros.com/health
```

### Additional Cloudflare Security Settings

**Recommended security enhancements:**

1. **Security Level**: Set to "Medium" or "High"
2. **Browser Integrity Check**: Enable
3. **Hotlink Protection**: Enable
4. **Bot Fight Mode**: Enable (Free plan)
5. **Always Use HTTPS**: Enable

**Page Rules (optional):**
- Cache Level: Standard
- Browser Cache TTL: 4 hours
- Security Level: Medium

---

## 7. Next Steps & Ongoing Management

### Testing the Public Endpoint

Once DNS propagation is complete (24-48 hours), test your deployment:

**API Endpoints:**
```bash
# Health check
curl https://topselfservicepros.com/health

# Register test agent
curl -X POST "https://topselfservicepros.com/agents/register" \
     -H "Content-Type: application/json" \
     -d '{
       "agent_id": "prod-test-001", 
       "agent_type": "research", 
       "capabilities": ["web_search", "data_analysis"]
     }'

# Send heartbeat
curl -X POST "https://topselfservicepros.com/agents/heartbeat" \
     -H "Content-Type: application/json" \
     -d '{
       "agent_id": "prod-test-001", 
       "status": "active"
     }'

# List active agents
curl https://topselfservicepros.com/agents/list_active
```

**WebSocket Testing:**
```javascript
// Test WebSocket connection
const ws = new WebSocket('wss://topselfservicepros.com/ws');
ws.onopen = () => console.log('Connected');
ws.onmessage = (event) => console.log('Message:', event.data);
ws.send('{"test": "message"}');
```

### Dynamic Personal IP Management

Your personal IP address may change, affecting SSH access. Solutions:

**Option 1: Dynamic DNS Script**
```bash
# Create script to update UFW with new IP
cat > ~/update_ssh_access.sh << 'EOF'
#!/bin/bash
NEW_IP=$(curl -s https://ipv4.icanhazip.com)
OLD_IP_RULE=$(sudo ufw status numbered | grep "22" | head -1 | grep -o '[0-9.]\+/[0-9]\+')

if [ "$NEW_IP" != "${OLD_IP_RULE%/*}" ]; then
    sudo ufw delete allow from $OLD_IP_RULE to any port 22
    sudo ufw allow from $NEW_IP to any port 22
    echo "SSH access updated for IP: $NEW_IP"
fi
EOF

chmod +x ~/update_ssh_access.sh
```

**Option 2: Cloudflare Tunnel for SSH**
Consider using Cloudflare Tunnel (cloudflared) for secure SSH access without exposing port 22.

### Logging and Monitoring

**Container Logs:**
```bash
# View real-time logs
docker-compose logs -f

# View specific service logs
docker-compose logs gpt-nexus
docker-compose logs postgres
docker-compose logs redis
```

**System Monitoring:**
```bash
# System resources
htop
df -h
free -h

# Docker resource usage
docker stats
```

**Future Monitoring Stack:**
- **Prometheus**: Metrics collection
- **Grafana**: Visualization dashboards
- **Loki**: Log aggregation
- **Alertmanager**: Alert notifications

### Inter-Agent Communication (Redis Pub/Sub)

The next major development phase involves implementing Redis pub/sub for real-time agent communication:

**Redis Pub/Sub Implementation:**
```python
# Example for future implementation
import redis
import asyncio

async def agent_communication_handler():
    r = redis.Redis(host='localhost', port=6379, decode_responses=True)
    pubsub = r.pubsub()
    pubsub.subscribe('agent_commands', 'agent_responses')
    
    for message in pubsub.listen():
        if message['type'] == 'message':
            # Process inter-agent communication
            await handle_agent_message(message['data'])
```

### Version Control with Git

**Initialize Git repository:**
```bash
cd nexus_project_root
git init
git add .
git commit -m "Initial Nexus Orchestrator deployment"

# Add remote repository
git remote add origin https://github.com/yourusername/nexus-orchestrator.git
git push -u origin main
```

**Git Workflow for Updates:**
```bash
# Make changes locally
git add .
git commit -m "Updated configuration"
git push

# Deploy updates to production
ssh nexus@your_droplet_ip
cd ~/nexus_project_root
git pull
docker-compose up -d --build
```

### Backup Strategy

**Database Backups:**
```bash
# Create backup script
cat > ~/backup_database.sh << 'EOF'
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/home/nexus/backups"

mkdir -p $BACKUP_DIR

# PostgreSQL backup
docker exec nexus_project_root_postgres_1 pg_dump -U nexus_user nexus_db > $BACKUP_DIR/postgres_backup_$DATE.sql

# Redis backup
docker exec nexus_project_root_redis_1 redis-cli BGSAVE
docker cp nexus_project_root_redis_1:/data/dump.rdb $BACKUP_DIR/redis_backup_$DATE.rdb

# Compress backups
tar -czf $BACKUP_DIR/nexus_backup_$DATE.tar.gz -C $BACKUP_DIR postgres_backup_$DATE.sql redis_backup_$DATE.rdb

# Clean up individual files
rm $BACKUP_DIR/postgres_backup_$DATE.sql $BACKUP_DIR/redis_backup_$DATE.rdb

# Keep only last 7 days of backups
find $BACKUP_DIR -name "nexus_backup_*.tar.gz" -mtime +7 -delete

echo "Backup completed: nexus_backup_$DATE.tar.gz"
EOF

chmod +x ~/backup_database.sh

# Add to crontab for daily backups
(crontab -l 2>/dev/null; echo "0 2 * * * /home/nexus/backup_database.sh") | crontab -
```

**DigitalOcean Droplet Snapshots:**
- Enable automatic snapshots in DigitalOcean dashboard
- Take manual snapshots before major updates
- Store critical backups off-site (AWS S3, Google Drive, etc.)

### Performance Optimization

**Future Optimizations:**
- Implement connection pooling for PostgreSQL
- Add Redis clustering for high availability
- Configure Nginx caching for static content
- Implement rate limiting and request throttling
- Add horizontal scaling with Docker Swarm or Kubernetes

### Security Hardening

**Additional Security Measures:**
- Implement API key authentication
- Add request logging and intrusion detection
- Regular security updates and patches
- SSL certificate monitoring and renewal
- Penetration testing and vulnerability assessments

---

## Conclusion

You now have a robust, production-ready deployment of the Nexus Orchestrator system. This guide provides the foundation for:

✅ **Secure Production Environment**: UFW firewall, SSL/TLS encryption, Cloudflare protection  
✅ **Scalable Architecture**: Docker containerization, database persistence, Redis caching  
✅ **Operational Efficiency**: Automated deployments, monitoring, backup strategies  
✅ **Development Workflow**: Local testing, version control, CI/CD readiness  

The system is now ready for agent registration, real-time communication, and expansion with additional microservices. Focus on implementing inter-agent communication features and scaling as your operational requirements grow.

Remember to maintain regular backups, monitor system performance, and keep security configurations updated as your deployment evolves.

**Success Metrics:**
- ✅ All services running and healthy
- ✅ HTTPS endpoint accessible globally
- ✅ WebSocket connections functioning
- ✅ Database operations performing correctly
- ✅ Security measures active and effective

Your Nexus Orchestrator is operational and ready for Top Shelf Service LLC's IT infrastructure needs!