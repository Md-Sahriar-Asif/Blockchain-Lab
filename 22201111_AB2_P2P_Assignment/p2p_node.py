"""
p2p_node.py - Peer networking: every peer is a TCP server AND a TCP client.

* Server role : accepts incoming connections (one thread per connection).
* Client role : connects to other peers using IP + port.
* After connecting, peers exchange HELLO / HELLO_ACK, then text and files.
"""

import ipaddress
import os
import socket
import threading
import uuid

import protocol

CONNECT_TIMEOUT = 5  # seconds, for connect() and the HELLO handshake


# ---- Input validation -------------------------------------------------------

def parse_port(value):
    try:
        port = int(str(value).strip())
    except ValueError:
        raise ValueError(f"Invalid port: '{value}' (must be a number)")
    if not 1 <= port <= 65535:
        raise ValueError(f"Invalid port: {port} (must be 1-65535)")
    return port


def parse_ip(value):
    value = str(value).strip()
    if value.lower() == "localhost":
        return "127.0.0.1"
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError:
        raise ValueError(f"Invalid IP address: '{value}'")


# ---- Data classes -----------------------------------------------------------

class Peer:
    """A remote peer we are currently connected to."""

    def __init__(self, peer_id, name, sock, ip, listen_port):
        self.peer_id = peer_id
        self.name = name
        self.sock = sock
        self.ip = ip
        self.listen_port = listen_port       # the port that peer listens on
        self.send_lock = threading.Lock()    # one sender at a time per socket

    def label(self):
        return f"{self.name} [{self.peer_id}] {self.ip}:{self.listen_port}"


class P2PNode:
    def __init__(self, name, port, on_event=None, on_peers_changed=None,
                 download_dir="downloads"):
        name = (name or "").strip()
        if not name:
            raise ValueError("Peer name cannot be empty")
        self.name = name
        self.port = parse_port(port)
        self.peer_id = uuid.uuid4().hex[:8]
        self.download_dir = download_dir
        self.on_event = on_event or (lambda text: None)
        self.on_peers_changed = on_peers_changed or (lambda: None)

        self.server_socket = None
        self.running = False
        self.peers = {}                      # peer_id -> Peer
        self.lock = threading.Lock()         # protects self.peers

    # ---- Helpers ------------------------------------------------------------

    def _log(self, text):
        self.on_event(text)

    def get_peers(self):
        with self.lock:
            return list(self.peers.values())

    def _get_peer(self, peer_id):
        with self.lock:
            peer = self.peers.get(peer_id)
        if peer is None:
            raise ValueError("Peer is not connected")
        return peer

    def _register(self, peer):
        """Add a peer unless that peer id is already connected."""
        with self.lock:
            if peer.peer_id in self.peers:
                return False
            self.peers[peer.peer_id] = peer
        self.on_peers_changed()
        return True

    def _remove_peer(self, peer, reason):
        with self.lock:
            removed = self.peers.get(peer.peer_id) is peer
            if removed:
                del self.peers[peer.peer_id]
        self._close_socket(peer.sock)
        if removed and self.running:
            self._log(f"[SYSTEM] {peer.name} disconnected ({reason})")
            self.on_peers_changed()

    @staticmethod
    def _close_socket(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    # ---- Server role --------------------------------------------------------

    def start(self):
        """Bind, listen and start accepting connections in a background thread."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", self.port))
            sock.listen()
        except OSError as e:
            sock.close()
            raise OSError(f"Cannot listen on port {self.port}: {e}")
        self.server_socket = sock
        self.running = True
        os.makedirs(self.download_dir, exist_ok=True)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self._log(f"[SYSTEM] Peer started: {self.name} [{self.peer_id}] on port {self.port}")

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
            except OSError:
                break  # server socket closed by stop()
            # One thread per incoming connection
            threading.Thread(target=self._handle_incoming, args=(conn, addr),
                             daemon=True).start()

    def _handle_incoming(self, conn, addr):
        """Handshake with a peer that connected to us, then receive from it."""
        try:
            conn.settimeout(CONNECT_TIMEOUT)
            hello = protocol.recv_message(conn)
            if hello.get("type") != protocol.HELLO:
                raise ValueError("Expected HELLO message")
            peer_id = str(hello["peer_id"])
            peer_name = str(hello["peer_name"])
            listen_port = int(hello["port"])

            if peer_id == self.peer_id:
                protocol.send_message(conn, protocol.make_error("Cannot connect to yourself"))
                conn.close()
                return
            with self.lock:
                already = peer_id in self.peers
            if already:
                protocol.send_message(conn, protocol.make_error("Already connected"))
                conn.close()
                return

            protocol.send_message(
                conn, protocol.make_hello_ack(self.peer_id, self.name, self.port))
            conn.settimeout(None)
            peer = Peer(peer_id, peer_name, conn, addr[0], listen_port)
        except (OSError, ValueError, KeyError, TypeError) as e:
            self._close_socket(conn)
            if self.running:
                self._log(f"[ERROR] Incoming connection failed: {e}")
            return

        if not self._register(peer):
            self._close_socket(conn)
            return
        self._log(f"[SYSTEM] Connected to {peer.name}")
        self._receive_loop(peer)

    # ---- Client role --------------------------------------------------------

    def connect(self, ip, port):
        """Connect to another peer. Raises an exception with a readable message on failure."""
        if not self.running:
            raise RuntimeError("Start your peer first")
        ip = parse_ip(ip)
        port = parse_port(port)

        sock = socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT)
        try:
            protocol.send_message(
                sock, protocol.make_hello(self.peer_id, self.name, self.port))
            reply = protocol.recv_message(sock)   # waits for HELLO_ACK
            if reply.get("type") == protocol.ERROR:
                raise ConnectionError(reply.get("message", "Connection refused by peer"))
            if reply.get("type") != protocol.HELLO_ACK:
                raise ConnectionError("Unexpected reply (expected HELLO_ACK)")
            sock.settimeout(None)
            peer = Peer(str(reply["peer_id"]), str(reply["peer_name"]), sock,
                        ip, int(reply["port"]))
        except Exception:
            self._close_socket(sock)
            raise

        if not self._register(peer):
            self._close_socket(sock)
            raise ConnectionError("Already connected")
        self._log(f"[SYSTEM] Connected to {peer.name} ({ip}:{port})")
        threading.Thread(target=self._receive_loop, args=(peer,), daemon=True).start()
        return peer

    # ---- Receiving ----------------------------------------------------------

    def _receive_loop(self, peer):
        reason = "connection closed"
        try:
            while self.running:
                msg = protocol.recv_message(peer.sock)
                kind = msg.get("type")
                if kind == protocol.TEXT:
                    self._log(f"{peer.name} -> You: {msg.get('message', '')}")
                elif kind == protocol.FILE:
                    self._receive_file(peer, msg)
                else:
                    self._log(f"[ERROR] Unknown message type from {peer.name}: {kind}")
        except (OSError, ValueError) as e:   # ConnectionError, bad JSON, bad length ...
            reason = str(e)
        finally:
            self._remove_peer(peer, reason)

    def _unique_path(self, filename):
        path = os.path.join(self.download_dir, filename)
        base, ext = os.path.splitext(filename)
        n = 1
        while os.path.exists(path):          # never overwrite an existing file
            path = os.path.join(self.download_dir, f"{base}_{n}{ext}")
            n += 1
        return path

    def _receive_file(self, peer, meta):
        filesize = meta.get("filesize")
        if isinstance(filesize, bool) or not isinstance(filesize, int) or filesize < 0:
            raise ValueError("Invalid file size in file metadata")
        # keep only the base name so a peer cannot write outside downloads/
        filename = os.path.basename(str(meta.get("filename", "")).replace("\\", "/"))
        if not filename:
            filename = "received_file"
        path = self._unique_path(filename)

        remaining = filesize                 # file size tells us when the file ends
        try:
            with open(path, "wb") as f:
                while remaining > 0:
                    chunk = peer.sock.recv(min(protocol.CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ConnectionError("Peer disconnected during file transfer")
                    f.write(chunk)
                    remaining -= len(chunk)
        except Exception:
            if os.path.exists(path):
                os.remove(path)              # delete incomplete file
            raise
        self._log(f"{peer.name} -> You: File received: {os.path.basename(path)}")

    # ---- Sending ------------------------------------------------------------

    def send_text(self, peer_id, text):
        peer = self._get_peer(peer_id)
        try:
            with peer.send_lock:
                protocol.send_message(
                    peer.sock, protocol.make_text(self.peer_id, self.name, text))
        except OSError as e:
            self._remove_peer(peer, str(e))
            raise ConnectionError(f"Send failed: {e}")
        self._log(f"You -> {peer.name}: {text}")

    def send_file(self, peer_id, path):
        if not path or not os.path.isfile(path):
            raise FileNotFoundError(f"File does not exist: {path}")
        peer = self._get_peer(peer_id)
        filesize = os.path.getsize(path)
        filename = os.path.basename(path)

        with open(path, "rb") as f:
            try:
                with peer.send_lock:
                    # 1) metadata, 2) raw bytes in chunks
                    protocol.send_message(peer.sock, protocol.make_file(
                        self.peer_id, self.name, filename, filesize))
                    while True:
                        chunk = f.read(protocol.CHUNK_SIZE)
                        if not chunk:
                            break
                        peer.sock.sendall(chunk)
            except OSError as e:
                self._remove_peer(peer, str(e))
                raise ConnectionError(f"Send failed: {e}")
        self._log(f"You -> {peer.name}: File sent: {filename}")

    # ---- Shutdown -----------------------------------------------------------

    def stop(self):
        was_running = self.running
        self.running = False
        if self.server_socket:
            self._close_socket(self.server_socket)
            self.server_socket = None
        for peer in self.get_peers():
            self._close_socket(peer.sock)
        with self.lock:
            self.peers.clear()
        if was_running:
            self.on_peers_changed()
