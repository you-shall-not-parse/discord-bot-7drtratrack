import socket
import struct

SERVER_IP = '176.57.171.44'  # Your server IP here
SERVER_PORT = 28016          # Your server port here
RCON_PASSWORD = 'bedcc53'  # Your RCON password here

# Helper to receive exactly n bytes
def receive_all(sock, n):
    data = b''
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data += packet
    return data

def send_rcon_packet(sock, request_id, packet_type, payload):
    payload_bytes = payload.encode('utf8') + b'\x00'
    size = 4 + 4 + len(payload_bytes) + 1  # request_id + type + payload + 2 null bytes total
    packet = struct.pack('<i', size)
    packet += struct.pack('<i', request_id)
    packet += struct.pack('<i', packet_type)
    packet += payload_bytes
    packet += b'\x00'  # extra null terminator
    sock.sendall(packet)

def receive_rcon_response(sock, expected_request_id=None):
    raw_size = receive_all(sock, 4)
    if not raw_size:
        return None
    size = struct.unpack('<i', raw_size)[0]
    
    # Sanity check on size
    if size <= 0 or size > 4096:
        print(f"Warning: suspicious packet size: {size}")
        return None
    
    data = receive_all(sock, size)
    if not data or len(data) < 8:
        return None
    
    request_id, response_type = struct.unpack('<ii', data[:8])
    payload_raw = data[8:]
    
    # Strip trailing null bytes from payload
    payload = payload_raw.rstrip(b'\x00').decode('utf8', errors='ignore')
    
    # If waiting for specific request_id, recurse until found
    if expected_request_id is not None and request_id != expected_request_id:
        return receive_rcon_response(sock, expected_request_id)
    
    return request_id, response_type, payload

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((SERVER_IP, SERVER_PORT))
    print(f"Connected to {SERVER_IP}:{SERVER_PORT}")

    # Authenticate (packet type 3 = SERVERDATA_AUTH)
    send_rcon_packet(sock, 1, 3, RCON_PASSWORD)
    auth_response = receive_rcon_response(sock, expected_request_id=1)
    if auth_response is None:
        print("Failed to receive auth response")
        sock.close()
        return
    request_id, response_type, payload = auth_response
    if request_id == -1:
        print("Auth failed: invalid password")
        sock.close()
        return
    print("Authentication successful")

    # Query current map (packet type 2 = SERVERDATA_EXECCOMMAND)
    send_rcon_packet(sock, 2, 2, "currentmap")
    map_response = receive_rcon_response(sock, expected_request_id=2)
    if map_response is None:
        print("Failed to receive map response")
        sock.close()
        return
    request_id, response_type, payload = map_response
    print(f"Current map: {payload}")

    sock.close()

if __name__ == "__main__":
    main()
