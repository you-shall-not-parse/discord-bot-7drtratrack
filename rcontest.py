import socket
import struct

def send_rcon_command(sock, command, request_id, rcon_type=2):
    # Build packet: size(4) + id(4) + type(4) + command + 2 null bytes
    payload = command.encode('utf8') + b'\x00\x00'
    size = 4 + 4 + len(payload)  # id + type + payload
    packet = struct.pack('<iii', size, request_id, rcon_type) + payload
    sock.send(packet)

def receive_rcon_response(sock):
    # Receive size (4 bytes)
    raw_size = sock.recv(4)
    if not raw_size:
        return None
    size = struct.unpack('<i', raw_size)[0]
    data = sock.recv(size)
    # Unpack response: id(4), type(4), string payload, 2 null bytes
    request_id, response_type = struct.unpack('<ii', data[:8])
    response = data[8:-2].decode('utf8')
    return response

def main():
    host = '176.57.171.44'
    port = 28016
    password = 'YOUR_RCON_PASSWORD'  # Replace with your RCON password

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))

    # Authenticate
    request_id = 1
    send_rcon_command(sock, password, request_id, rcon_type=3)  # 3 is auth
    response = receive_rcon_response(sock)
    if response == '':
        print("Authentication succeeded")
    else:
        print(f"Authentication failed: {response}")
        sock.close()
        return

    # Send get map command
    request_id += 1
    send_rcon_command(sock, 'get g_mapname', request_id)
    response = receive_rcon_response(sock)
    print("Current map:", response)

    sock.close()

if __name__ == '__main__':
    main()
