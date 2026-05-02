"""
Stand-alone dashboard server.

Use this when you just want to view the existing data/dashboard.html
without re-running the agent.

    python serve.py

Opens http://127.0.0.1:8765/dashboard.html in your browser.
Override the port with DASHBOARD_PORT=9000 python serve.py
"""

from agent import serve_dashboard_forever

if __name__ == "__main__":
    serve_dashboard_forever()
