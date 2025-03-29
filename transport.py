import socket
import struct
import threading
import time
from grading import MSS, DEFAULT_TIMEOUT, MAX_NETWORK_BUFFER, WINDOW_SIZE

# Constants for simplified TCP flags
SYN_FLAG = 0x8   # Synchronization (SYN) flag 
ACK_FLAG = 0x4   # Acknowledgment (ACK) flag
FIN_FLAG = 0x2   # Finish (FIN) flag 
SACK_FLAG = 0x1  # Selective Acknowledgment (optional)

EXIT_SUCCESS = 0
EXIT_ERROR = 1

# Tuning parameter for RTT EWMA (alpha) – can be adjusted
ALPHA = 0.125  # common default

class ReadMode:
    NO_FLAG = 0
    NO_WAIT = 1
    TIMEOUT = 2

class Packet:
    def __init__(self, seq=0, ack=0, flags=0, payload=b"", win=0):
        self.seq = seq          # Sequence number of first byte in payload
        self.ack = ack          # Cumulative acknowledgment (next expected byte)
        self.flags = flags      # Control flags (SYN, ACK, FIN, SACK)
        self.payload = payload  # Data payload (bytes)
        self.win = win          # Advertised window (receiver buffer space)

    def encode(self):
        """Encode the packet header and payload into bytes (network byte order)."""
        # Pack header fields: seq (32b), ack (32b), flags (32b), win (16b), payload_len (16b)
        header = struct.pack("!IIIHH", self.seq, self.ack, self.flags, self.win, len(self.payload))
        return header + self.payload

    @staticmethod
    def decode(data):
        """Decode bytes into a Packet object (assumes fixed header format)."""
        header_size = struct.calcsize("!IIIHH")
        seq, ack, flags, win, payload_len = struct.unpack("!IIIHH", data[:header_size])
        payload = data[header_size:]
        return Packet(seq, ack, flags, payload, win=win)

class TransportSocket:
    def __init__(self):
        self.sock_fd = None
        # Synchronization primitives
        self.recv_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.wait_cond = threading.Condition(self.recv_lock)
        # Thread management
        self.death_lock = threading.Lock()
        self.dying = False
        self.thread = None
        # Transmission and reception state
        self.window = {
            "last_ack": 0,            # Next byte expected from peer (recv side)
            "next_seq_expected": 0,   # Last acknowledged byte from our sent data (send side)
            "recv_buf": b"",          # Receive buffer for in-order data
            "recv_len": 0,            # Number of bytes in receive buffer
            "next_seq_to_send": 0     # Sequence number for the next new byte we send
        }
        self.peer_adv_wnd = MAX_NETWORK_BUFFER  # Peer’s advertised window (flow control)
        self.state = "CLOSED"                   # Connection state (FSM)
        self.sock_type = None
        self.conn = None       # Peer address (IP, port)
        self.my_port = None
        # RTT estimation variables
        self.estimated_rtt = 0.0                # Smoothed RTT (seconds)
        self.timeout_interval = DEFAULT_TIMEOUT # Current retransmission timeout (seconds)
        # Buffer for unacknowledged segments (seq -> segment info)
        self.inflight = {}      # Tracks sent but not yet ACKed segments

    def socket(self, sock_type, port, server_ip=None):
        """
        Create and initialize the socket, set its type, and start the backend thread.
        For TCP_INITIATOR, perform active connection establishment (3-way handshake).
        For TCP_LISTENER, prepare for passive open.
        """
        self.sock_fd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_type = sock_type

        if sock_type == "TCP_INITIATOR":
            # Active open: initiate connection to server
            self.conn = (server_ip, port)
            self.sock_fd.bind(("", 0))  # bind to an ephemeral local port
        elif sock_type == "TCP_LISTENER":
            # Passive open: listen on the given port for incoming connection
            self.sock_fd.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock_fd.bind(("", port))
        else:
            print("Unknown socket type")
            return EXIT_ERROR

        # Set a 1-second socket timeout for periodic checks in backend thread
        self.sock_fd.settimeout(1.0)
        self.my_port = self.sock_fd.getsockname()[1]

        # Start the backend thread to handle incoming packets
        self.thread = threading.Thread(target=self.backend, daemon=True)
        self.thread.start()

        # For active open, perform 3-way handshake
        if sock_type == "TCP_INITIATOR":
            self.state = "SYN_SENT"
            initial_seq = self.window["next_seq_to_send"]
            syn_pkt = Packet(seq=initial_seq, ack=0, flags=SYN_FLAG, payload=b"",
                             win=MAX_NETWORK_BUFFER - self.window["recv_len"])
            print("Client: Sending SYN (seq=%d)" % initial_seq)
            self.sock_fd.sendto(syn_pkt.encode(), self.conn)
            self.window["next_seq_to_send"] += 1  # Consume SYN sequence number
            self.timeout_interval = DEFAULT_TIMEOUT
            attempts = 0
            max_retries = 5
            while self.state != "ESTABLISHED" and attempts < max_retries:
                with self.recv_lock:
                    start_time = time.time()
                    self.wait_cond.wait(timeout=self.timeout_interval)
                    if self.state == "ESTABLISHED":
                        break
                    elapsed = time.time() - start_time
                if elapsed >= self.timeout_interval:
                    attempts += 1
                    print("Client: SYN timeout, retransmitting SYN...")
                    self.timeout_interval = min(self.timeout_interval * 2, 60)  # exponential backoff
                    syn_pkt.seq = initial_seq
                    syn_pkt.ack = 0
                    syn_pkt.win = MAX_NETWORK_BUFFER
                    self.sock_fd.sendto(syn_pkt.encode(), self.conn)
            if self.state != "ESTABLISHED":
                print("Client: Connection establishment failed.")
                return EXIT_ERROR
            else:
                print("Client: Connection established.")
        else:
            # Passive open: waiting for SYN from client.
            self.state = "LISTEN"

        return EXIT_SUCCESS

    def close(self):
        """
        Close the socket gracefully, performing connection termination (FIN handshake).
        """
        if self.state == "ESTABLISHED":
            with self.send_lock:
                fin_seq = self.window["next_seq_to_send"]
                fin_pkt = Packet(seq=fin_seq, ack=self.window["last_ack"], flags=FIN_FLAG, payload=b"",
                                 win=MAX_NETWORK_BUFFER - self.window["recv_len"])
                print("Sending FIN (seq=%d)" % fin_seq)
                try:
                    self.sock_fd.sendto(fin_pkt.encode(), self.conn)
                except Exception as e:
                    print("FIN send error:", e)
                self.window["next_seq_to_send"] += 1
                self.state = "FIN_SENT"
                self.timeout_interval = self.estimated_rtt * 2 if self.estimated_rtt > 0 else DEFAULT_TIMEOUT
                attempts = 0
                max_retries = 5
                while self.state != "TIME_WAIT" and attempts < max_retries:
                    with self.recv_lock:
                        start_time = time.time()
                        self.wait_cond.wait(timeout=self.timeout_interval)
                        if self.state == "TIME_WAIT":
                            break
                        elapsed = time.time() - start_time
                    if elapsed >= self.timeout_interval:
                        attempts += 1
                        print("FIN timeout, retransmitting FIN...")
                        self.timeout_interval = min(self.timeout_interval * 2, 60)
                        fin_pkt.seq = fin_seq
                        fin_pkt.ack = self.window["last_ack"]
                        fin_pkt.win = MAX_NETWORK_BUFFER - self.window["recv_len"]
                        try:
                            self.sock_fd.sendto(fin_pkt.encode(), self.conn)
                        except Exception:
                            pass
                if self.state != "TIME_WAIT":
                    print("Connection termination not acknowledged after retries, closing anyway.")
        self.death_lock.acquire()
        try:
            self.dying = True
        finally:
            self.death_lock.release()
        if self.thread:
            self.thread.join()
        if self.sock_fd:
            self.sock_fd.close()
        else:
            print("Error: Null socket")
            return EXIT_ERROR
        print("Socket closed.")
        return EXIT_SUCCESS

    def send(self, data):
        """
        Send data reliably to the peer using a sliding window protocol.
        """
        if not self.conn:
            raise ValueError("Connection not established.")
        with self.send_lock:
            self.send_segment(data)

    def recv(self, buf, length, flags):
        """
        Retrieve data from the receive buffer.
        """
        read_len = 0
        if length < 0:
            print("ERROR: Negative length")
            return EXIT_ERROR
        if flags == ReadMode.NO_FLAG:
            with self.wait_cond:
                while self.window["recv_len"] == 0:
                    self.wait_cond.wait()
        self.recv_lock.acquire()
        try:
            if flags in [ReadMode.NO_WAIT, ReadMode.NO_FLAG]:
                if self.window["recv_len"] > 0:
                    read_len = min(self.window["recv_len"], length)
                    buf[0] = self.window["recv_buf"][:read_len]
                    if read_len < self.window["recv_len"]:
                        self.window["recv_buf"] = self.window["recv_buf"][read_len:]
                        self.window["recv_len"] -= read_len
                    else:
                        self.window["recv_buf"] = b""
                        self.window["recv_len"] = 0
            else:
                print("ERROR: Unknown or unimplemented flag.")
                read_len = EXIT_ERROR
        finally:
            self.recv_lock.release()
        return read_len

    def send_segment(self, data):
        """
        Send 'data' in segments using a sliding window and wait for ACKs.
        Implements retransmission with adaptive timeout.
        """
        offset = 0
        total_len = len(data)
        base_seq = self.window["next_seq_expected"]
        end_seq = base_seq + total_len

        while self.window["next_seq_expected"] < end_seq:
            # Send new segments if window space permits
            while offset < total_len and (self.window["next_seq_to_send"] - self.window["next_seq_expected"] < self.peer_adv_wnd):
                payload_len = min(MSS, total_len - offset,
                                  self.peer_adv_wnd - (self.window["next_seq_to_send"] - self.window["next_seq_expected"]))
                if payload_len <= 0:
                    break
                seq_no = self.window["next_seq_to_send"]
                chunk = data[offset : offset + payload_len]
                with self.recv_lock:
                    ack_no = self.window["last_ack"]
                    recv_win = MAX_NETWORK_BUFFER - self.window["recv_len"]
                segment = Packet(seq=seq_no, ack=ack_no, flags=0, payload=chunk, win=recv_win)
                print(f"Sending segment (seq={seq_no}, len={payload_len})")
                self.sock_fd.sendto(segment.encode(), self.conn)
                send_time = time.time()
                self.inflight[seq_no] = {"payload": chunk, "send_time": send_time, "trans": 1, "rtt_valid": True}
                self.window["next_seq_to_send"] += payload_len
                offset += payload_len

            with self.recv_lock:
                start_wait = time.time()
                self.wait_cond.wait(timeout=self.timeout_interval)
                elapsed = time.time() - start_wait
                if self.window["next_seq_expected"] >= end_seq:
                    break
                timeout_occurred = (elapsed >= self.timeout_interval)
            if timeout_occurred:
                # Check if connection is terminating; if yes, break out.
                if self.state in ("TIME_WAIT", "CLOSED"):
                    print("Connection terminating; stopping retransmissions.")
                    break
                base = self.window["next_seq_expected"]
                if base in self.inflight:
                    seg_info = self.inflight[base]
                    segment = Packet(seq=base, ack=self.window["last_ack"], flags=0,
                                     payload=seg_info["payload"],
                                     win=MAX_NETWORK_BUFFER - self.window["recv_len"])
                    print(f"Timeout: Retransmitting segment (seq={base})")
                    self.sock_fd.sendto(segment.encode(), self.conn)
                    seg_info["trans"] += 1
                    seg_info["rtt_valid"] = False
                    seg_info["send_time"] = time.time()
                    self.timeout_interval = min(self.timeout_interval * 2, 60)
                continue

            # Process ACKs and update RTT estimates
            base = self.window["next_seq_expected"]
            acked_seqs = [seq for seq in list(self.inflight.keys()) if seq < base]
            for seq in sorted(acked_seqs):
                seg_info = self.inflight.pop(seq)
                if seg_info["rtt_valid"]:
                    sample_rtt = time.time() - seg_info["send_time"]
                    if self.estimated_rtt == 0:
                        self.estimated_rtt = sample_rtt
                    else:
                        self.estimated_rtt = (1 - ALPHA) * self.estimated_rtt + ALPHA * sample_rtt
                    self.timeout_interval = 2 * self.estimated_rtt
        if self.window["next_seq_expected"] >= end_seq:
            print("All data acknowledged by peer.")

    def backend(self):
        """
        Backend loop to handle incoming packets and send appropriate responses.
        """
        while not self.dying:
            try:
                data, addr = self.sock_fd.recvfrom(2048)
            except socket.timeout:
                continue
            except Exception as e:
                if not self.dying:
                    print(f"Error in backend: {e}")
                continue

            packet = Packet.decode(data)
            if self.conn is None:
                self.conn = addr

            # Connection management: process SYN and FIN packets
            if packet.flags & SYN_FLAG:
                if self.sock_type == "TCP_LISTENER" and self.state == "LISTEN":
                    client_isn = packet.seq
                    server_isn = self.window["next_seq_to_send"]
                    synack_pkt = Packet(seq=server_isn, ack=client_isn + 1, flags=SYN_FLAG | ACK_FLAG, payload=b"",
                                        win=MAX_NETWORK_BUFFER - self.window["recv_len"])
                    print("Server: Received SYN, sending SYN-ACK")
                    self.sock_fd.sendto(synack_pkt.encode(), addr)
                    self.state = "ESTABLISHED"
                    self.window["next_seq_to_send"] += 1
                    self.window["last_ack"] = client_isn + 1
                    continue
                elif self.sock_type == "TCP_INITIATOR" and self.state == "SYN_SENT" and (packet.flags & ACK_FLAG):
                    server_isn = packet.seq
                    print("Client: Received SYN-ACK (seq=%d, ack=%d)" % (packet.seq, packet.ack))
                    ack_pkt = Packet(seq=self.window["next_seq_to_send"], ack=server_isn + 1, flags=ACK_FLAG, payload=b"",
                                     win=MAX_NETWORK_BUFFER - self.window["recv_len"])
                    self.sock_fd.sendto(ack_pkt.encode(), addr)
                    self.window["last_ack"] = server_isn + 1
                    if packet.ack > self.window["next_seq_expected"]:
                        self.window["next_seq_expected"] = packet.ack
                    self.state = "ESTABLISHED"
                    with self.wait_cond:
                        self.wait_cond.notify_all()
                    continue

            if packet.flags & FIN_FLAG:
                print("Received FIN (seq=%d)" % packet.seq)
                fin_ack_pkt = Packet(seq=0, ack=packet.seq + 1, flags=ACK_FLAG, payload=b"",
                                     win=MAX_NETWORK_BUFFER - self.window["recv_len"])
                self.sock_fd.sendto(fin_ack_pkt.encode(), addr)
                # Transition state and cancel pending retransmissions
                self.state = "TIME_WAIT"
                with self.wait_cond:
                    self.wait_cond.notify_all()
                with self.recv_lock:
                    self.inflight.clear()
                continue

            # ACK processing
            if (packet.flags & ACK_FLAG) != 0:
                if self.state == "FIN_SENT" and packet.ack >= self.window["next_seq_to_send"]:
                    print("Received ACK for FIN.")
                    self.state = "TIME_WAIT"
                    with self.wait_cond:
                        self.wait_cond.notify_all()
                with self.recv_lock:
                    if packet.ack > self.window["next_seq_expected"]:
                        self.window["next_seq_expected"] = packet.ack
                    self.peer_adv_wnd = packet.win if hasattr(packet, "win") else MAX_NETWORK_BUFFER
                    self.wait_cond.notify_all()
                if len(packet.payload) == 0:
                    continue

            # Data packet processing
            if packet.seq == self.window["last_ack"]:
                if self.window["recv_len"] + len(packet.payload) > MAX_NETWORK_BUFFER:
                    print("Receive buffer full, dropping segment seq=%d" % packet.seq)
                    continue
                with self.recv_lock:
                    self.window["recv_buf"] += packet.payload
                    self.window["recv_len"] += len(packet.payload)
                with self.wait_cond:
                    self.wait_cond.notify_all()
                print(f"Received segment (seq={packet.seq}, {len(packet.payload)} bytes) - in order")
                ack_val = packet.seq + len(packet.payload)
                ack_pkt = Packet(seq=0, ack=ack_val, flags=ACK_FLAG, payload=b"",
                                 win=MAX_NETWORK_BUFFER - self.window["recv_len"])
                self.sock_fd.sendto(ack_pkt.encode(), addr)
                self.window["last_ack"] = ack_val
            else:
                print(f"Out-of-order packet: seq={packet.seq}, expected={self.window['last_ack']}")

