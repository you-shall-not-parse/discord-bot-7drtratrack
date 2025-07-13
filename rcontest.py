from rcon.source import Client
import os
from dotenv import load_dotenv

load_dotenv()  # load variables from .env into environment

rcon_host = os.getenv("RCON_HOST")
rcon_port = int(os.getenv("RCON_PORT"))  # convert port to int
rcon_password = os.getenv("RCON_PASSWORD")

try:
    with Client(host, port, passwd=password) as client:
        response = client.run("status")
        print("✅ Connected! Response:\n", response)
except Exception as e:
    print("❌ Failed to connect:", e)
