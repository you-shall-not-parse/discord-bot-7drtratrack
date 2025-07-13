from rcon.source import Client

host = "176.57.171.44"
port = 28016
password = "bedcc53"

try:
    with Client(host, port, passwd=password) as client:
        response = client.run("status")
        print("✅ Connected! Response:\n", response)
except Exception as e:
    print("❌ Failed to connect:", e)
