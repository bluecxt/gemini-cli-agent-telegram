# 🚀 Gemini CLI Agent (Telegram)

> **The ultimate autonomous engineering assistant for Telegram, powered by the latest Gemini CLI engine.**  
> Built for performance, persistence, and production-grade reliability.

---

## ✨ Key Features

- **⚡️ Native Streaming:** Real-time message chunks and tool execution feedback.
- **🔄 Session Management:** Native `/chat` command to list, switch, or start fresh sessions.
- **🛠️ YOLO Tool Execution:** Fully autonomous system operations (Search, Browse, File, Shell).
- **💾 Dual-Layer Persistence:** 
  - `/app/workspace`: Persistent long-term project storage.
  - `/root/.gemini`: Saved Google credentials and session context.
- **🛡️ Clean & Robust:** PEP 8 compliant, structured rotating logs, and Docker-first architecture.

---

## 🚀 Quick Start

### 1. Prerequisites
- [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/install/)
- Telegram Bot Token ([@BotFather](https://t.me/botfather))
- Your Telegram User ID (Authorized admin)

### 2. Configuration
Create a `.env` file in the root directory:
```env
TELEGRAM_TOKEN=your_token_here
ADMIN_ID=your_id_here
GITHUB_PERSONAL_ACCESS_TOKEN=optional_token
```

### 3. Deployment
```bash
# Launch the agent
docker-compose up -d --build

# Perform first-time Google Login
docker exec -it gemini_agent gemini
```
*Follow the URL provided by the CLI to authenticate with your Google account.*

---

## 📱 Interactive Commands

| Command | Description |
| :--- | :--- |
| **/start** | Initialize the agent and show the quick-access menu. |
| **/chat** | List all available Gemini sessions. |
| **/chat `<index>`** | Switch to a specific session (e.g., `/chat 2`). |
| **/chat new** | Start a completely fresh engineering session. |
| **/status** | Check active processes and system health. |
| **/stop** | Kill all active background tasks (Gemini, Shell, etc.). |

---

## 🛠️ Maintenance & Storage

- **Persistent Workspace:** Any file the agent creates in `/app/workspace` is saved on your host machine in the `./workspace` folder.
- **Viewing Logs:** 
  ```bash
  docker logs -f gemini_agent
  ```
- **Reset Environment:** To clear transient data and reset the container:
  ```bash
  docker-compose down && docker-compose up -d
  ```
