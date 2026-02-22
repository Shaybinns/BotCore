# main.py
# Not used by Railway (entry point is api_server.py via gunicorn).
# Kept as a local convenience script for manual testing.

if __name__ == "__main__":
    print("BotCore API server — run via: python api_server.py")
    print("Or in production:             gunicorn -w 4 -b 0.0.0.0:8000 api_server:app")
