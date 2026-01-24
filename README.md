# Workflow Orchestration Engine

This is a real backend system for running multi-step workflows with retries, failure recovery, and audit logging. Built with Python, Flask, PostgreSQL, Redis, and Docker. Comes with a visual dashboard and a one-command demo script!

---

## Quick Start

1. **Start everything:**
   ```bash
   docker compose up -d
   ```
2. **Open the dashboard:**
   - Double-click `frontend/index.html` or run:
     ```bash
     open frontend/index.html
     ```
3. **Try a workflow:**
   - Click any demo button (Joke, User, Cat, Todo, Multi-Step)
   - See the result instantly!

Or, run all tests in the terminal:
```bash
./demo.sh
```

---

## �️ How It Works (Short Version)

- You define a workflow (like a recipe)
- Add steps (call APIs, transform data, etc)
- Run it (it works in the background, retries if something fails)
- See results and logs in the dashboard

**Architecture:**
```
API (Flask) → Service Layer → Domain → PostgreSQL
                                 ↓
                              Worker (background)
                                 ↓
                               Redis (queue)
```

---

## 🔄 Workflow State Machine

```
        PENDING
          |
        start
          |
       RUNNING
      /       \
 success     failure
   |            |
COMPLETED     FAILED
                  |
      retry (if attempts < max)
                  |
              RETRYING
                  |
               RUNNING
```

- **PENDING** → **RUNNING** → **COMPLETED** (if success)
- If a step fails: **RUNNING** → **FAILED** → **RETRYING** (if retries left) → **RUNNING**

---

## 📁 Project Structure

```
workflow-orchestration-engine/
├── src/           # All backend code
├── frontend/      # Dashboard (HTML, JS, CSS)
├── tests/         # Unit and integration tests
├── migrations/    # SQL migrations
├── Dockerfile*    # Docker setup
├── docker-compose.yml
└── demo.sh        # One-command demo script
```

---

## � Questions?

Open an issue or reach out to me if you have questions or feedback!
