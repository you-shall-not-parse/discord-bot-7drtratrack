import socket

STATS_IP = '176.57.171.44'    # Replace with your stats server IP
STATS_PORT = 28025            # Replace with your stats port (not RCON port!)

def get_player_stats(player_name):
    # Example protocol: send player name, receive stats as JSON/text
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((STATS_IP, STATS_PORT))
        sock.sendall(player_name.encode('utf-8'))
        response = sock.recv(4096)
        print(f"Stats for {player_name}:")
        print(response.decode('utf-8'))

if __name__ == "__main__":
    player = input("Enter player name: ")
    get_player_stats(player)
