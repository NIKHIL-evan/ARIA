.PHONY: dev stop

dev:
	@echo "Starting all services..."
	PYTHONPATH=. uv run python server.py & \
	PYTHONPATH=. uv run python mcp_server.py & \
	ngrok start --all --config ngrok.yml

stop:
	@pkill -f "mcp_server.py" || true
	@pkill -f "server.py" || true
	@pkill -f "ngrok" || true
	@echo "All services stopped."
