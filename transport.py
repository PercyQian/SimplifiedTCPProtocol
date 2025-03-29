# transport.py
import socket
import struct
import threading
import time

# 定义标志位
SYN_FLAG = 0x8     # 同步
ACK_FLAG = 0x4     # 确认
FIN_FLAG = 0x2     # 结束

DEFAULT_TIMEOUT = 3
# MSS：这里简单计算 header 大小（使用 "!IIIIH" 格式，注意可根据实际需要调整）
MSS = 1400 - struct.calcsize("!IIIIH")

class Packet:
    def __init__(self, seq=0, ack=0, flags=0, payload=b""):
        self.seq = seq
        self.ack = ack
        self.flags = flags
        self.payload = payload

    def encode(self):
        # 此处多打包一个占位字段（0），以凑够 header 大小，可根据项目要求修改
        header = struct.pack("!IIIIH", self.seq, self.ack, self.flags, len(self.payload), 0)
        return header + self.payload

    @staticmethod
    def decode(data):
        header_size = struct.calcsize("!IIIIH")
        seq, ack, flags, payload_len, _ = struct.unpack("!IIIIH", data[:header_size])
        payload = data[header_size:header_size+payload_len]
        return Packet(seq, ack, flags, payload)

class TransportSocket:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(DEFAULT_TIMEOUT)
        self.state = "CLOSED"
        self.conn = None  # (IP, port)
        self.lock = threading.Lock()
        # 用于数据传输
        self.recv_buffer = b""
        self.expected_seq = 0
        self.send_seq = 0

    def socket_init(self, sock_type, port, server_ip=None):
        """
        sock_type: "TCP_INITIATOR"（客户端）或 "TCP_LISTENER"（服务端）
        """
        if sock_type == "TCP_INITIATOR":
            self.conn = (server_ip, port)
            self.sock.bind(("", 0))  # 客户端随机绑定一个本地端口
            self.connect()
        elif sock_type == "TCP_LISTENER":
            self.sock.bind(("", port))
            self.listen()
        else:
            print("Unknown socket type")

    def connect(self):
        # 客户端发起连接：三次握手
        self.state = "SYN_SENT"
        syn_packet = Packet(seq=self.send_seq, ack=0, flags=SYN_FLAG)
        self.sock.sendto(syn_packet.encode(), self.conn)
        print("Client: Sent SYN, seq =", self.send_seq)
        try:
            data, addr = self.sock.recvfrom(2048)
            packet = Packet.decode(data)
            if (packet.flags & (SYN_FLAG | ACK_FLAG)) == (SYN_FLAG | ACK_FLAG):
                print("Client: Received SYN-ACK, seq =", packet.seq, "ack =", packet.ack)
                self.state = "ESTABLISHED"
                self.expected_seq = packet.seq + 1
                self.send_seq += 1
                # 发送 ACK 确认
                ack_packet = Packet(seq=self.send_seq, ack=self.expected_seq, flags=ACK_FLAG)
                self.sock.sendto(ack_packet.encode(), self.conn)
                print("Client: Sent ACK, connection established")
        except socket.timeout:
            print("Client: Timeout during connect, retrying...")
            self.connect()

    def listen(self):
        # 服务端等待连接：三次握手
        self.state = "LISTEN"
        print("Server: Listening for connection...")
        while True:
            data, addr = self.sock.recvfrom(2048)
            packet = Packet.decode(data)
            if packet.flags & SYN_FLAG:
                print("Server: Received SYN from", addr, "seq =", packet.seq)
                self.conn = addr
                self.expected_seq = packet.seq + 1
                # 生成一个随机初始序号，例如 100
                initial_seq = 100
                syn_ack_packet = Packet(seq=initial_seq, ack=self.expected_seq, flags=SYN_FLAG | ACK_FLAG)
                self.sock.sendto(syn_ack_packet.encode(), addr)
                print("Server: Sent SYN-ACK, seq =", initial_seq, "ack =", self.expected_seq)
                try:
                    data, addr = self.sock.recvfrom(2048)
                    ack_packet = Packet.decode(data)
                    if ack_packet.flags & ACK_FLAG and ack_packet.ack == initial_seq + 1:
                        self.state = "ESTABLISHED"
                        print("Server: Connection established")
                        self.send_seq = initial_seq + 1
                        break
                except socket.timeout:
                    print("Server: Timeout waiting for ACK, retrying...")
        return

    def send(self, data):
        if self.state != "ESTABLISHED":
            print("Connection not established!")
            return
        # 这里为了简化，假设数据长度小于 MSS，如果数据较长，需要分片发送
        with self.lock:
            packet = Packet(seq=self.send_seq, ack=0, flags=0, payload=data)
            self.sock.sendto(packet.encode(), self.conn)
            print(f"Sent data packet, seq = {self.send_seq}, length = {len(data)}")
            # 等待 ACK
            try:
                data_ack, addr = self.sock.recvfrom(2048)
                ack_packet = Packet.decode(data_ack)
                if ack_packet.flags & ACK_FLAG and ack_packet.ack == self.send_seq + len(data):
                    print(f"Received ACK for seq {self.send_seq}")
                    self.send_seq += len(data)
            except socket.timeout:
                print("Timeout waiting for ACK, retransmitting data...")
                self.send(data)

    def recv(self, buf, length):
        # 阻塞式接收，直到收到符合预期序号的数据包
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
                packet = Packet.decode(data)
                if packet.seq == self.expected_seq:
                    buf[0] = packet.payload
                    self.expected_seq += len(packet.payload)
                    # 发送 ACK
                    ack_packet = Packet(seq=0, ack=self.expected_seq, flags=ACK_FLAG)
                    self.sock.sendto(ack_packet.encode(), self.conn)
                    print(f"Received data packet, seq = {packet.seq}, sent ACK = {self.expected_seq}")
                    break
            except socket.timeout:
                continue
        return len(buf[0])

    def close(self):
        # 连接终止：发送 FIN，然后等待对方 ACK
        fin_packet = Packet(seq=self.send_seq, ack=0, flags=FIN_FLAG)
        self.sock.sendto(fin_packet.encode(), self.conn)
        print("Sent FIN, seq =", self.send_seq)
        try:
            data, addr = self.sock.recvfrom(2048)
            ack_packet = Packet.decode(data)
            if ack_packet.flags & ACK_FLAG:
                print("Received ACK for FIN, closing connection")
        except socket.timeout:
            print("Timeout waiting for FIN ACK")
        self.sock.close()
        self.state = "CLOSED"
