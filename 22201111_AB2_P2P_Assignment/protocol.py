"""
protocol.py - Application-level protocol for the P2P assignment.

Every message on the wire is framed as:

    [4-byte big-endian length][JSON payload (UTF-8)]

File data is sent right after a "file" message as raw bytes
(exactly `filesize` bytes, no framing).
"""

import json
import struct

HEADER_SIZE = 4                 # 4-byte length prefix
MAX_MESSAGE_SIZE = 1024 * 1024  # a JSON message larger than 1 MB is invalid
CHUNK_SIZE = 64 * 1024          # file chunk size (64 KB)

# Message types
HELLO = "hello"
HELLO_ACK = "hello_ack"
TEXT = "text"
FILE = "file"
ERROR = "error"   # used only to refuse a connection during the handshake


def send_message(sock, message):
    """Encode a dict as JSON and send it as [4-byte length][payload]."""
    payload = json.dumps(message).encode("utf-8")
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def recv_exact(sock, size):
    """Read exactly `size` bytes (recv() may return fewer bytes than asked)."""
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Peer disconnected")
        data.extend(chunk)
    return bytes(data)


def recv_message(sock):
    """Read one framed JSON message and return it as a dict."""
    header = recv_exact(sock, HEADER_SIZE)
    (length,) = struct.unpack("!I", header)
    if length == 0 or length > MAX_MESSAGE_SIZE:
        raise ValueError(f"Invalid message length: {length}")
    message = json.loads(recv_exact(sock, length).decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("Message is not a JSON object")
    return message


# ---- Message builders -------------------------------------------------------

def make_hello(peer_id, peer_name, port):
    return {"type": HELLO, "peer_id": peer_id, "peer_name": peer_name, "port": port}


def make_hello_ack(peer_id, peer_name, port):
    return {"type": HELLO_ACK, "peer_id": peer_id, "peer_name": peer_name, "port": port}


def make_text(sender_id, sender_name, message):
    return {"type": TEXT, "sender_id": sender_id, "sender_name": sender_name,
            "message": message}


def make_file(sender_id, sender_name, filename, filesize):
    return {"type": FILE, "sender_id": sender_id, "sender_name": sender_name,
            "filename": filename, "filesize": filesize}


def make_error(message):
    return {"type": ERROR, "message": message}
