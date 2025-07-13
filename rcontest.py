import socket
import struct

class CRCONClient:
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password.encode('utf-8')
        self.socket = None
        self.request_id = 0

    def connect(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(5)
        self.socket.connect((self.host, self.port))
        print(f"Connected to {self.host}:{self.port}")

    def send_packet(self, request_id, packet_type, body):
        # Packet structure: size (int32), request id (int32), packet type (int32), body (string), 2 null bytes
        body_bytes = body.encode('utf-8')
        size = 4 + 4 + len(body_bytes) + 2  # id + type + body + 2 null bytes
        packet = struct.pack('<iii', size, request_id, packet_type) + body_bytes + b'\x00\x00'
        self.socket.sendall(packet)

    def receive_packet(self):
        # First 4 bytes size
        size_bytes = self.socket.recv(4)
        if len(size_bytes) < 4:
            raise ConnectionError("Failed to read packet size")

        size = struct.unpack('<i', size_bytes)[0]

        # Read the rest of the packet based on size
        data = b''
        while len(data) < size:
            chunk = self.socket.recv(size - len(data))
            if not chunk:
                raise ConnectionError("Connection closed unexpectedly")
            data += chunk

        request_id, packet_type = struct.unpack('<ii', data[:8])
        body = data[8:-2].decode('utf-8')  # exclude the 2 null bytes
        return request_id, packet_type, body

    def authenticate(self):
        self.request_id = 1
        SERVERDATA_AUTH = 3
        self.send_packet(self.request_id, SERVERDATA_AUTH, self.password.decode())
        response_id, response_type, response_body = self.receive_packet()

        if response_id == -1:
            return False
        return True

    def send_command(self, command):
        self.request_id += 1
        SERVERDATA_EXECCOMMAND = 2
        self.send_packet(self.request_id, SERVERDATA_EXECCOMMAND, command)
        _, _, body = self.receive_packet()
        return body

    def close(self):
        if self.socket:
            self.socket.close()
            self.socket = None

if __name__ == "__main__":
    host = input("Enter CRCON host (IP or domain): ")
    port = int(input("Enter CRCON port: "))
    password = input("Enter CRCON password: ")

    client = CRCONClient(host, port, password)

    try:
        client.connect()
        if client.authenticate():
            print("Authentication successful!")
            # Example command to check server status
            response = client.send_command("status")
            print("Server response:\n", response)
        else:
            print("Authentication failed: Invalid password.")
    except Exception as e:
        print("Error:", e)
    finally:
        client.close()
