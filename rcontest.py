import os
from dotenv import load_dotenv

load_dotenv()

host = os.getenv("RCON_HOST")
port = int(os.getenv("RCON_PORT"))
password = os.getenv("RCON_PASSWORD")

from rcon.source import Client

try:
    with Client(host, port, passwd=password) as client:
        response = client.run("status")
        print("✅ Connected! Server response:")
        print(response)
except Exception as e:
    print(f"❌ Failed to connect: {e}")
