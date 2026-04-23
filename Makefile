.PHONY: mcp github stop

mcp:
	@echo "Starting MCP server..."
	PYTHONPATH=. uv run python mcp_server.py &
	ngrok http 8001 --domain=bagpipe-accustom-groove.ngrok-free.dev

github:
	@echo "Starting GitHub App server..."
	PYTHONPATH=. uv run python server.py &
	ngrok http 8000 --domain=bagpipe-accustom-groove.ngrok-free.dev

stop:
	@pkill -f "mcp_server.py" || true
	@pkill -f "server.py" || true
	@pkill -f "ngrok" || true
	@echo "Stopped."