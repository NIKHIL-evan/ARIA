.PHONY: mcp github stop restart

DOMAIN = bagpipe-accustom-groove.ngrok-free.dev
PYTHON_EXEC = PYTHONPATH=. uv run python

mcp: stop
	@echo "🚀 Starting MCP Server on Port 8001..."
	$(PYTHON_EXEC) mcp_server.py > mcp.log 2>&1 &
	sleep 2
	ngrok.exe http 8001 --domain=$(DOMAIN)

github: stop
	@echo "🚀 Starting GitHub App Server on Port 8000..."
	$(PYTHON_EXEC) server.py > github.log 2>&1 &
	sleep 2
	ngrok.exe http 8000 --domain=$(DOMAIN)

restart: stop mcp

stop:
	@echo "🛑 Stopping all servers and ngrok..."
	@fuser -k 8000/tcp 8001/tcp 2>/dev/null || true
	@taskkill.exe /F /IM ngrok.exe /T 2>/dev/null || true
	@echo "✅ Cleaned up."