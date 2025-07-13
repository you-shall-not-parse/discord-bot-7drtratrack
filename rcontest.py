import socket
import struct
from getpass import getpass

SERVER_IP = '176.57.171.44'
SERVER_PORT = 28016  # Correct RCON port!
RCON_PASSWORD = getpass("Enter your RCON password: ")

def send_packet(sock, request_id, packet_type, body):
    body_bytes = body.encode('utf-8') + b'\x00'
    packet = struct.pack('<iii', request_id, packet_type, 0) + body_bytes + b'\x00'
    size = len(packet)
    sock.sendall(struct.pack('<i', size) + packet)

def receive_packet(sock):
    raw_size = sock.recv(4)
    if not raw_size:
        return None, None, None
    size = struct.unpack('<i', raw_size)[0]
    data = b''
    while len(data) < size:
        more = sock.recv(size - len(data))
        if not more:
            break
        data += more
    if len(data) < 12:
        return None, None, None
    request_id, packet_type, _ = struct.unpack('<iii', data[:12])
    body = data[12:-2].decode('utf-8', errors='ignore')
    return request_id, packet_type, body

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((SERVER_IP, SERVER_PORT))
    print(f"Connected to {SERVER_IP}:{SERVER_PORT}")

    # Authenticate
    send_packet(sock, 1, 3, RCON_PASSWORD)
    request_id, packet_type, body = receive_packet(sock)
    # Read the empty response after auth
    receive_packet(sock)
    if request_id == -1:
        print("Authentication failed.")
        sock.close()
        return
    print("Authentication successful!")

    # Send "showmap" command
    send_packet

