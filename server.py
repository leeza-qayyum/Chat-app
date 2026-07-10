import socket
import threading
import struct
import pickle

def send_data(sock, data):
    """Prefixes data with a 4-byte length header."""
    try:
        serialized = pickle.dumps(data)
        # Pack length (4 bytes big-endian) + data
        sock.sendall(struct.pack(">I", len(serialized)) + serialized)
    except Exception as e:
        print(f"Send Error: {e}")

def recv_data(sock):
    """Reads exactly 4 bytes length, then the payload."""
    try:
        header = sock.recv(4)
        if not header: return None
        msg_len = struct.unpack(">I", header)[0]
        
        chunks = []
        bytes_recd = 0
        while bytes_recd < msg_len:
            # Buffer size 4096 or remaining
            chunk = sock.recv(min(msg_len - bytes_recd, 4096))
            if not chunk: break
            chunks.append(chunk)
            bytes_recd += len(chunk)
        return pickle.loads(b"".join(chunks))
    except:
        return None


HOST = "0.0.0.0"
PORT = 5000

clients = {}      # socket -> username
rooms = {}        # room_name -> set(sockets)
lock = threading.Lock()

def pack_data(data):
    """Prefix data with 4-byte length header"""
    try:
        serialized = pickle.dumps(data)
        return struct.pack(">I", len(serialized)) + serialized
    except: return None

def recv_data(sock):
    """Read 4-byte length, then read payload"""
    try:
        header = b""
        while len(header) < 4:
            p = sock.recv(4 - len(header))
            if not p: return None
            header += p
        
        msg_len = struct.unpack(">I", header)[0]
        
        data = b""
        while len(data) < msg_len:
            p = sock.recv(min(msg_len - len(data), 4096))
            if not p: return None
            data += p
        return pickle.loads(data)
    except: return None

def broadcast(packet, exclude=None):
    with lock:
        for sock in list(clients.keys()):
            if sock != exclude:
                try: sock.sendall(pack_data(packet))
                except: remove_client(sock)

def send_private(target_name, packet):
    with lock:
        target_sock = next((s for s, n in clients.items() if n == target_name), None)
    if target_sock:
        try: target_sock.sendall(pack_data(packet))
        except: remove_client(target_sock)

def remove_client(sock):
    name = None
    with lock:
        if sock in clients:
            name = clients.pop(sock)
            print(f"[DISCONNECT] {name}")
            for r in list(rooms.keys()):
                rooms[r].discard(sock)
                if not rooms[r]: del rooms[r]
            
    if name:
        broadcast({"type": "user_list", "data": list(clients.values())})
        broadcast({"type": "room_list", "data": list(rooms.keys())})
        
    try: sock.close()
    except: pass

def handle_client(sock):
    name = ""
    try:
        # 1. STRICT HANDSHAKE: Expect a 'login' packet first
        packet = recv_data(sock)
        
        if not packet or packet.get("type") != "login":
            print(f"[AUTH FAILED] {sock.getpeername()} did not send login packet.")
            return # Close connection

        name = packet.get("name")
        with lock: clients[sock] = name
        print(f"[CONNECTED] {name}")
        
        # Send initial lists
        broadcast({"type": "user_list", "data": list(clients.values())})
        sock.sendall(pack_data({"type": "room_list", "data": list(rooms.keys())}))

        # 2. Main Loop
        while True:
            packet = recv_data(sock)
            if not packet: break
            
            ptype = packet.get("type")

            if ptype == "msg":
                broadcast({"type": "msg", "sender": name, "msg": packet["msg"]}, exclude=sock)
            
            elif ptype == "private":
                send_private(packet["target"], {"type": "private", "sender": name, "msg": packet["msg"]})
            
            elif ptype == "room_msg":
                room = packet["room"]
                with lock:
                    if room in rooms:
                        for s in rooms[room]:
                            if s != sock:
                                try: s.sendall(pack_data({"type": "room_msg", "room": room, "sender": name, "msg": packet["msg"]}))
                                except: pass

            elif ptype == "join_room":
                room = packet["room"]
                with lock:
                    if room not in rooms: rooms[room] = set()
                    rooms[room].add(sock)
                broadcast({"type": "room_list", "data": list(rooms.keys())})

            elif ptype == "leave_room":
                room = packet["room"]
                with lock:
                    if room in rooms:
                        rooms[room].discard(sock)
                        if not rooms[room]: del rooms[room]
                broadcast({"type": "room_list", "data": list(rooms.keys())})

            elif ptype in ["file_meta", "file_chunk", "file_done", "call_request", "call_accept", "call_video", "call_audio", "call_hangup"]:
                if packet.get("target"):
                    send_private(packet["target"], packet)
                else:
                    broadcast(packet, exclude=sock)

    except Exception as e:
        print(f"[ERROR] {name}: {e}")
    finally:
        remove_client(sock)

def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen()
    print(f"Server listening on {PORT}...")
    while True:
        sock, addr = server.accept()
        threading.Thread(target=handle_client, args=(sock,), daemon=True).start()

if __name__ == "__main__":
    start_server()