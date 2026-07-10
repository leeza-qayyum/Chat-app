import socket
import threading
import pickle
import struct
import os
import time
import cv2
import pyaudio
import numpy as np
import tkinter as tk
from tkinter import scrolledtext, simpledialog, filedialog, messagebox, ttk

# --- CONFIGURATION ---
HOST = '127.0.0.1' 
PORT = 5000
CHUNK_SIZE = 4096

# Setup Download Directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "Received_Files")
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

def pack_data(data):
    try:
        serialized = pickle.dumps(data)
        return struct.pack(">I", len(serialized)) + serialized
    except: return None

def recv_data(sock):
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

class ChatClient:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.username = ""
        self.running = True
        
        # Call State
        self.call_active = False
        self.call_target = None
        self.audio_in = None; self.audio_out = None
        self.p = pyaudio.PyAudio()
        self.cam = None
        self.call_win = None
        self.file_buffer = {}

        # GUI
        self.root = tk.Tk()
        self.root.title(f"Chat App - {DOWNLOAD_DIR}")
        self.root.geometry("800x600")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        self.setup_gui()
        self.connect()

    def setup_gui(self):
        left = tk.Frame(self.root, width=220, bg="#f0f0f0")
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        right = tk.Frame(self.root)
        right.pack(side=tk.RIGHT, expand=True, fill=tk.BOTH, padx=5, pady=5)

        tk.Label(left, text="Active Users", bg="#f0f0f0", font=("Arial", 10, "bold")).pack(anchor="w")
        self.lst_users = tk.Listbox(left, height=12, exportselection=False)
        self.lst_users.pack(fill=tk.X, pady=2)

        tk.Label(left, text="Rooms", bg="#f0f0f0", font=("Arial", 10, "bold")).pack(anchor="w", pady=(10,0))
        self.lst_rooms = tk.Listbox(left, height=8, exportselection=False)
        self.lst_rooms.pack(fill=tk.X, pady=2)

        self.ent_room = tk.Entry(left)
        self.ent_room.pack(pady=5, fill=tk.X)
        tk.Button(left, text="Join Room", command=self.join_room).pack(fill=tk.X)
        tk.Button(left, text="Leave Room", command=self.leave_room).pack(fill=tk.X, pady=2)
        
        tk.Label(left, text="Actions", bg="#f0f0f0", font=("Arial", 10, "bold")).pack(anchor="w", pady=(10,0))
        tk.Button(left, text="Video Call User", command=self.request_call, bg="#4CAF50", fg="white").pack(fill=tk.X, pady=2)
        tk.Button(left, text="Send File", command=self.send_file, bg="#2196F3", fg="white").pack(fill=tk.X, pady=2)

        self.txt_chat = scrolledtext.ScrolledText(right, state='disabled')
        self.txt_chat.pack(expand=True, fill=tk.BOTH)
        
        input_frame = tk.Frame(right)
        input_frame.pack(fill=tk.X, pady=5)
        self.ent_msg = tk.Entry(input_frame)
        self.ent_msg.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self.ent_msg.bind("<Return>", lambda e: self.send_msg())
        tk.Button(input_frame, text="Send", command=self.send_msg, width=10).pack(side=tk.RIGHT, padx=5)
        
        self.progress = ttk.Progressbar(right, orient="horizontal", length=100, mode="determinate")
        self.progress.pack(fill=tk.X, pady=2)

    def connect(self):
        try:
            self.sock.connect((HOST, PORT))
            self.username = simpledialog.askstring("Login", "Username:")
            if not self.username: 
                self.root.destroy()
                return
            
            login_packet = {"type": "login", "name": self.username}
            self.sock.sendall(pack_data(login_packet))
            
            threading.Thread(target=self.receive_loop, daemon=True).start()
            self.root.mainloop()
            
        except Exception as e:
            messagebox.showerror("Error", f"Connection failed: {e}")
            self.root.destroy()

    def log(self, msg):
        self.txt_chat.config(state='normal')
        self.txt_chat.insert(tk.END, msg + "\n")
        self.txt_chat.see(tk.END)
        self.txt_chat.config(state='disabled')

    def update_list(self, listbox, data):
        listbox.delete(0, tk.END)
        for item in data: listbox.insert(tk.END, item)

    def receive_loop(self):
        while self.running:
            try:
                packet = recv_data(self.sock)
                if not packet: break 
                
                ptype = packet.get("type")

                if ptype == "user_list":
                    self.root.after(0, self.update_list, self.lst_users, packet["data"])
                elif ptype == "room_list":
                    self.root.after(0, self.update_list, self.lst_rooms, packet["data"])
                elif ptype == "msg":
                    self.root.after(0, self.log, f"{packet['sender']}: {packet['msg']}")
                elif ptype == "private":
                    self.root.after(0, self.log, f"[Private] {packet['sender']}: {packet['msg']}")
                elif ptype == "room_msg":
                    self.root.after(0, self.log, f"[Room {packet['room']}] {packet['sender']}: {packet['msg']}")
                
                # --- CALL LOGIC ---
                elif ptype == "call_request":
                    self.root.after(0, self.handle_incoming_call, packet["sender"])
                elif ptype == "call_accept":
                    self.root.after(0, self.start_call, packet["sender"])
                elif ptype == "call_hangup":
                    # Remote hangup: Force end call
                    self.root.after(0, self.force_remote_hangup, packet["sender"])
                
                # --- MEDIA ---
                elif ptype == "call_video":
                    if self.call_active:
                        try:
                            nparr = np.frombuffer(packet["data"], np.uint8)
                            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                            cv2.imshow(f"Call: {packet['sender']}", frame)
                            cv2.waitKey(1)
                        except: pass
                elif ptype == "call_audio":
                    if self.call_active and self.audio_out:
                        self.audio_out.write(packet["data"])

                # --- FILES ---
                elif ptype == "file_meta":
                    fname = packet["filename"]
                    self.file_buffer[fname] = open(os.path.join(DOWNLOAD_DIR, fname), "wb")
                    self.root.after(0, self.log, f"Receiving file: {fname}...")
                elif ptype == "file_chunk":
                    fname = packet["filename"]
                    if fname in self.file_buffer:
                        self.file_buffer[fname].write(packet["data"])
                elif ptype == "file_done":
                    fname = packet["filename"]
                    if fname in self.file_buffer:
                        self.file_buffer[fname].close()
                        del self.file_buffer[fname]
                        self.root.after(0, self.log, f"File saved: {fname}")
            except Exception as e:
                print(f"Receive Error: {e}")
                break
        
        self.running = False
        self.root.after(0, self.on_close)

    def handle_incoming_call(self, sender):
        if messagebox.askyesno("Call", f"Incoming call from {sender}?"):
            self.sock.sendall(pack_data({"type": "call_accept", "target": sender, "sender": self.username}))
            self.start_call(sender)

    def request_call(self):
        try:
            target = self.lst_users.get(self.lst_users.curselection())
            if target == self.username: return
            self.sock.sendall(pack_data({"type": "call_request", "target": target, "sender": self.username}))
            self.log(f"Calling {target}...")
        except:
            messagebox.showwarning("Warning", "Select a user to call.")

    def start_call(self, target_user):
        if self.call_active: return
        self.call_active = True
        self.call_target = target_user
        
        self.audio_in = self.p.open(format=pyaudio.paInt16, channels=1, rate=44100, input=True, frames_per_buffer=1024)
        self.audio_out = self.p.open(format=pyaudio.paInt16, channels=1, rate=44100, output=True, frames_per_buffer=1024)
        self.cam = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        
        self.call_win = tk.Toplevel(self.root)
        self.call_win.title(f"Call: {target_user}")
        self.call_win.protocol("WM_DELETE_WINDOW", lambda: self.end_call(remote=False))
        
        tk.Button(self.call_win, text="Hang Up", command=lambda: self.end_call(remote=False), bg="red", fg="white", width=20).pack(pady=20)
        
        threading.Thread(target=self.video_stream, daemon=True).start()
        threading.Thread(target=self.audio_stream, daemon=True).start()

    def force_remote_hangup(self, sender):
        """Called when receiving a hangup packet from the server."""
        self.log(f"Call ended by {sender}")
        self.end_call(remote=True)

    def end_call(self, remote=False):
        """Robust cleanup function to close windows and free resources."""
        if not self.call_active: return
        
        # 1. Stop the loop condition immediately
        self.call_active = False 
        
        # 2. Notify other user if WE are the ones hanging up
        if not remote and self.call_target:
            try: self.sock.sendall(pack_data({"type": "call_hangup", "target": self.call_target, "sender": self.username}))
            except: pass

        # 3. Destroy GUI Window IMMEDIATELY (Main Thread)
        if self.call_win:
            try: self.call_win.destroy()
            except: pass
            self.call_win = None

        # 4. Release Hardware in Background
        def cleanup_resources():
            time.sleep(0.5) # Allow threads to exit loop naturally
            
            if self.cam and self.cam.isOpened():
                self.cam.release()
            
            try:
                if self.audio_in: self.audio_in.stop_stream(); self.audio_in.close()
                if self.audio_out: self.audio_out.stop_stream(); self.audio_out.close()
            except: pass
            
            # Close any lingering OpenCV windows
            try: cv2.destroyAllWindows()
            except: pass
            
            self.call_target = None

        threading.Thread(target=cleanup_resources, daemon=True).start()

    def video_stream(self):
        while self.call_active:
            if not self.cam.isOpened(): break
            ret, frame = self.cam.read()
            if not ret: break
            
            frame = cv2.resize(frame, (320, 240))
            _, b = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 40])
            
            try: self.sock.sendall(pack_data({"type": "call_video", "target": self.call_target, "sender": self.username, "data": b.tobytes()}))
            except: break
            time.sleep(0.05)

    def audio_stream(self):
        while self.call_active:
            try:
                data = self.audio_in.read(1024, exception_on_overflow=False)
                self.sock.sendall(pack_data({"type": "call_audio", "target": self.call_target, "sender": self.username, "data": data}))
            except: break

    def send_msg(self):
        txt = self.ent_msg.get(); self.ent_msg.delete(0, tk.END)
        if not txt: return
        user_sel = self.lst_users.curselection()
        room_sel = self.lst_rooms.curselection()
        if user_sel:
            target = self.lst_users.get(user_sel)
            if target != self.username:
                self.sock.sendall(pack_data({"type": "private", "target": target, "msg": txt}))
                self.log(f"[To {target}]: {txt}")
                return
        if room_sel:
            room = self.lst_rooms.get(room_sel)
            self.sock.sendall(pack_data({"type": "room_msg", "room": room, "msg": txt}))
            self.log(f"[Room {room}]: {txt}")
            return
        self.sock.sendall(pack_data({"type": "msg", "msg": txt}))
        self.log(f"You: {txt}")

    def send_file(self):
        path = filedialog.askopenfilename()
        if not path: return
        target = None
        mode = "broadcast"
        try: 
            user_idx = self.lst_users.curselection()
            if user_idx:
                target = self.lst_users.get(user_idx)
                mode = "private"
        except: pass
        fname = os.path.basename(path)
        size = os.path.getsize(path)
        self.sock.sendall(pack_data({"type": "file_meta", "target": target, "mode": mode, "filename": fname}))
        self.log(f"Sending {fname} ({mode})...")
        self.progress["maximum"] = size
        sent = 0
        with open(path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                self.sock.sendall(pack_data({"type": "file_chunk", "target": target, "mode": mode, "filename": fname, "data": chunk}))
                sent += len(chunk)
                self.progress["value"] = sent
                self.root.update_idletasks()
        self.sock.sendall(pack_data({"type": "file_done", "target": target, "mode": mode, "filename": fname}))
        self.progress["value"] = 0
        self.log(f"File sent.")

    def join_room(self):
        room = self.ent_room.get()
        if room: self.sock.sendall(pack_data({"type": "join_room", "room": room}))

    def leave_room(self):
        try:
            room = self.lst_rooms.get(self.lst_rooms.curselection())
            self.sock.sendall(pack_data({"type": "leave_room", "room": room}))
        except: pass

    def on_close(self):
        self.running = False
        self.end_call()
        try: self.sock.close()
        except: pass
        try: self.p.terminate()
        except: pass
        self.root.destroy()
        os._exit(0)

if __name__ == "__main__":
    ChatClient()