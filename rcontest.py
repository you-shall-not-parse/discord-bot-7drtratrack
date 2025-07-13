import socket
import struct

SERVER_IP = '176.57.171.44'
SERVER_PORT = 28016
RCON_PASSWORD = 'bedcc53'

def send_packet(sock, request_id, packet_type, body):
    body_bytes = body.encode('utf-8') + b'\x00'
    size = 4 + 4 + len(body_bytes)  # request_id + packet_type + body + null terminator
    packet = struct.pack('<i', size)
    packet += struct.pack('<i', request_id)
    packet += struct.pack('<i', packet_type)
    packet += body_bytes
    sock.sendall(packet)

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
    if len(data) < 8:
        return None, None, None
    request_id, response_type = struct.unpack('<ii', data[:8])
    body = data[8:-1].decode('utf-8', errors='ignore')
    return request_id, response_type, body

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((SERVER_IP, SERVER_PORT))
    print(f"Connected to {SERVER_IP}:{SERVER_PORT}")

    # Authenticate (packet_type 3 = SERVERDATA_AUTH)
    send_packet(sock, 1, 3, RCON_PASSWORD)
    request_id, response_type, body = receive_packet(sock)
    if request_id == -1:
        print("Authentication failed.")
        sock.close()
        return
    print("Authentication successful!")

    # Query current map (packet_type 2 = SERVERDATA_EXECCOMMAND)
    send_packet(sock, 2, 2, "showmap")
    request_id, response_type, body = receive_packet(sock)
    print(f"Current map: {body}")

    sock.close()

if __name__ == "__main__":
    main()
