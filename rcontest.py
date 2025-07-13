import socket

SERVER_IP = '176.57.171.44'
SERVER_PORT = 28015

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect((SERVER_IP, SERVER_PORT))
print(f"Connected to {SERVER_IP}:{SERVER_PORT}")

try:
    data = sock.recv(1024)
    print("Received raw data:", data)
except Exception as e:
    print("Error receiving data:", e)

sock.close()
