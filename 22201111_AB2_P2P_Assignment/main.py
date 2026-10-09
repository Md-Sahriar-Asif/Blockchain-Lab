"""
main.py - Tkinter user interface for the P2P assignment.

Run with:  python main.py
"""

import os
import queue
import socket
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

from p2p_node import P2PNode, parse_port

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")


def local_ip():
    """Best-effort LAN IP, shown so other computers know where to connect."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class App:
    def __init__(self, root):
        self.root = root
        root.title("UAP P2P Network")
        root.geometry("900x600")

        self.node = None
        self.events = queue.Queue()      # worker threads -> GUI thread
        self.listed_peer_ids = []        # peer ids in the same order as the listbox

        self.name_var = tk.StringVar(value="Alice")
        self.port_var = tk.StringVar(value="5000")
        self.ip_var = tk.StringVar(value="127.0.0.1")
        self.rport_var = tk.StringVar(value="5001")
        self.text_var = tk.StringVar()
        self.info_var = tk.StringVar(value="Peer not started")
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.poll_events)

    # ---- UI layout ----------------------------------------------------------

    def _build_ui(self):
        pad = {"padx": 6, "pady": 3}

        my = ttk.LabelFrame(self.root, text="My Peer")
        my.pack(fill="x", padx=8, pady=4)
        ttk.Label(my, text="Name:").grid(row=0, column=0, **pad)
        self.name_entry = ttk.Entry(my, textvariable=self.name_var, width=16)
        self.name_entry.grid(row=0, column=1, **pad)
        ttk.Label(my, text="Port:").grid(row=0, column=2, **pad)
        self.port_entry = ttk.Entry(my, textvariable=self.port_var, width=8)
        self.port_entry.grid(row=0, column=3, **pad)
        self.start_btn = ttk.Button(my, text="Start Peer", command=self.start_peer)
        self.start_btn.grid(row=0, column=4, **pad)
        self.stop_btn = ttk.Button(my, text="Stop", command=self.stop_peer, state="disabled")
        self.stop_btn.grid(row=0, column=5, **pad)
        ttk.Label(my, textvariable=self.info_var).grid(row=1, column=0, columnspan=6,
                                                       sticky="w", **pad)

        con = ttk.LabelFrame(self.root, text="Connect to Another Peer")
        con.pack(fill="x", padx=8, pady=4)
        ttk.Label(con, text="IP:").grid(row=0, column=0, **pad)
        ttk.Entry(con, textvariable=self.ip_var, width=16).grid(row=0, column=1, **pad)
        ttk.Label(con, text="Port:").grid(row=0, column=2, **pad)
        ttk.Entry(con, textvariable=self.rport_var, width=8).grid(row=0, column=3, **pad)
        ttk.Button(con, text="Connect", command=self.connect_peer).grid(row=0, column=4, **pad)

        middle = ttk.Frame(self.root)
        middle.pack(fill="both", expand=True, padx=8, pady=4)
        peers_frame = ttk.LabelFrame(middle, text="Connected Peers")
        peers_frame.pack(side="left", fill="y")
        self.peer_list = tk.Listbox(peers_frame, width=34, exportselection=False)
        self.peer_list.pack(fill="both", expand=True, padx=4, pady=4)
        log_frame = ttk.LabelFrame(middle, text="Messages / Events")
        log_frame.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.log = scrolledtext.ScrolledText(log_frame, state="disabled", wrap="word",
                                             font=("Courier", 9))
        self.log.pack(fill="both", expand=True, padx=4, pady=4)

        send = ttk.LabelFrame(self.root, text="Send Text")
        send.pack(fill="x", padx=8, pady=4)
        entry = ttk.Entry(send, textvariable=self.text_var)
        entry.pack(side="left", fill="x", expand=True, padx=6, pady=4)
        entry.bind("<Return>", lambda e: self.send_text())
        ttk.Button(send, text="Send", command=self.send_text).pack(side="right", padx=6)

        files = ttk.LabelFrame(self.root, text="Send File")
        files.pack(fill="x", padx=8, pady=4)
        ttk.Label(files, text="Text, image, audio, video, PDF, ZIP, etc.").pack(
            side="left", padx=6, pady=4)
        ttk.Button(files, text="Choose File & Send", command=self.send_file).pack(
            side="right", padx=6)

        ttk.Label(self.root, textvariable=self.status_var, relief="sunken",
                  anchor="w").pack(fill="x", side="bottom")

    # ---- Thread-safe event handling ----------------------------------------

    def post_event(self, text):            # called from network threads
        self.events.put(("log", text))

    def post_peers_changed(self):          # called from network threads
        self.events.put(("peers", None))

    def poll_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.append_log(value)
                elif kind == "peers":
                    self.refresh_peers()
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.status_var.set(text)

    def refresh_peers(self):
        selected = self.selected_peer_id()
        peers = self.node.get_peers() if self.node else []
        self.listed_peer_ids = [p.peer_id for p in peers]
        self.peer_list.delete(0, "end")
        for i, p in enumerate(peers):
            self.peer_list.insert("end", p.label())
            if p.peer_id == selected:
                self.peer_list.selection_set(i)

    def selected_peer_id(self):
        sel = self.peer_list.curselection()
        if not sel or sel[0] >= len(self.listed_peer_ids):
            return None
        return self.listed_peer_ids[sel[0]]

    def run_in_thread(self, func):
        """Run network work off the GUI thread; errors are shown in the log."""
        def runner():
            try:
                func()
            except Exception as e:
                self.post_event(f"[ERROR] {e}")
        threading.Thread(target=runner, daemon=True).start()

    # ---- Button actions -----------------------------------------------------

    def start_peer(self):
        if self.node:
            return
        try:
            port = parse_port(self.port_var.get())
            node = P2PNode(self.name_var.get(), port, self.post_event,
                           self.post_peers_changed, DOWNLOAD_DIR)
            node.start()
        except (ValueError, OSError) as e:
            self.append_log(f"[ERROR] {e}")
            return
        self.node = node
        self.info_var.set(f"{node.name} | ID: {node.peer_id} | "
                          f"IP: {local_ip()} | Port: {node.port}")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.name_entry.configure(state="disabled")
        self.port_entry.configure(state="disabled")

    def stop_peer(self):
        if not self.node:
            return
        self.node.stop()
        self.node = None
        self.refresh_peers()
        self.info_var.set("Peer not started")
        self.append_log("[SYSTEM] Peer stopped")
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.name_entry.configure(state="normal")
        self.port_entry.configure(state="normal")

    def connect_peer(self):
        if not self.node:
            self.append_log("[ERROR] Start your peer first")
            return
        node, ip, port = self.node, self.ip_var.get(), self.rport_var.get()

        def work():
            try:
                node.connect(ip, port)
            except Exception as e:
                raise RuntimeError(f"Connection failed: {e}")
        self.run_in_thread(work)

    def send_text(self):
        if not self.node:
            self.append_log("[ERROR] Start your peer first")
            return
        peer_id = self.selected_peer_id()
        if peer_id is None:
            self.append_log("[ERROR] Select a connected peer first")
            return
        text = self.text_var.get().strip()
        if not text:
            return
        self.text_var.set("")
        node = self.node
        self.run_in_thread(lambda: node.send_text(peer_id, text))

    def send_file(self):
        if not self.node:
            self.append_log("[ERROR] Start your peer first")
            return
        peer_id = self.selected_peer_id()
        if peer_id is None:
            self.append_log("[ERROR] Select a connected peer first")
            return
        path = filedialog.askopenfilename(title="Choose a file to send")
        if not path:
            return
        node = self.node
        self.append_log(f"[SYSTEM] Sending {os.path.basename(path)} ...")
        self.run_in_thread(lambda: node.send_file(peer_id, path))

    def on_close(self):
        if self.node:
            self.node.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
