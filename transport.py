import socket
import struct
import threading
import time  
from grading import MSS, DEFAULT_TIMEOUT, WINDOW_INITIAL_WINDOW_SIZE, WINDOW_INITIAL_SSTHRESH, MAX_NETWORK_BUFFER

# Constants for simplified TCP
SYN_FLAG = 0x8   # Synchronization flag 
ACK_FLAG = 0x4   # Acknowledgment flag
FIN_FLAG = 0x2   # Finish flag 
SACK_FLAG = 0x1  # Selective Acknowledgment flag 

# Alpha值用于RTT平滑计算
RTT_ALPHA = 0.125  # RTT计算的平滑因子
RTO_BETA = 2.0     # RTO计算的系数

EXIT_SUCCESS = 0
EXIT_ERROR = 1

class ReadMode:
    NO_FLAG = 0
    NO_WAIT = 1
    TIMEOUT = 2

class Packet:
    def __init__(self, seq=0, ack=0, flags=0, window=0, sack_left=0, sack_right=0, payload=b""):
        self.seq = seq
        self.ack = ack
        self.flags = flags
        self.window = window  # 添加窗口大小字段
        self.sack_left = sack_left  # 选择性确认左边界
        self.sack_right = sack_right  # 选择性确认右边界
        self.payload = payload

    def encode(self):
        # 编码数据包头部和数据为字节
        header = struct.pack("!IIIHII", self.seq, self.ack, self.flags, 
                            self.window, self.sack_left, self.sack_right)
        return header + self.payload

    @staticmethod
    def decode(data):
        # 将字节解码为Packet对象
        header_size = struct.calcsize("!IIIHII")
        if len(data) < header_size:
            return None
        seq, ack, flags, window, sack_left, sack_right = struct.unpack("!IIIHII", data[:header_size])
        payload = data[header_size:]
        return Packet(seq, ack, flags, window, sack_left, sack_right, payload)

# 添加TCP状态机状态
class State:
    CLOSED = 0
    LISTEN = 1
    SYN_SENT = 2
    SYN_RECEIVED = 3
    ESTABLISHED = 4
    FIN_SENT = 5
    TIME_WAIT = 6

class TransportSocket:
    def __init__(self):
        self.sock_fd = None

        # Locks and condition
        self.recv_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.wait_cond = threading.Condition(self.recv_lock)

        self.death_lock = threading.Lock()
        self.dying = False
        self.thread = None

        # 添加连接状态
        self.state = State.CLOSED
        
        # 添加RTT估计相关变量
        self.srtt = None  # 平滑化的RTT
        self.rttvar = None  # RTT变异性
        self.rto = DEFAULT_TIMEOUT  # 重传超时时间
        
        # 流量控制相关变量
        self.cwnd = WINDOW_INITIAL_WINDOW_SIZE  # 拥塞窗口大小
        self.ssthresh = WINDOW_INITIAL_SSTHRESH  # 慢启动阈值
        self.flight_size = 0  # 已发送但未确认的数据量
        self.duplicate_acks = 0  # 重复ACK计数
        
        # 发送和接收缓冲区
        self.send_buffer = b""
        self.recv_buffer = b""
        self.unacked_segments = {}  # 未确认的段 {seq: (time_sent, data, retries)}
        
        self.window = {
            "last_ack": 0,            # 我们期望从对等方接收的下一个序列号
            "next_seq_expected": 0,   # 我们已经收到的最高确认号
            "recv_buf": b"",          # 接收数据缓冲区
            "recv_len": 0,            # recv_buf中的字节数
            "next_seq_to_send": 0,    # 我们发送的下一个包的序列号
            "adv_window": MAX_NETWORK_BUFFER,  # 广告窗口大小
        }
        self.sock_type = None
        self.conn = None
        self.my_port = None

    def socket(self, sock_type, port, server_ip=None):
        """
        Create and initialize the socket, setting its type and starting the backend thread.
        """
        self.sock_fd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_type = sock_type

        if sock_type == "TCP_INITIATOR":
            self.conn = (server_ip, port)
            self.sock_fd.bind(("", 0))  # Bind to any available local port
        elif sock_type == "TCP_LISTENER":
            self.sock_fd.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock_fd.bind(("", port))
        else:
            print("Unknown socket type")
            return EXIT_ERROR

        # 1-second timeout so we can periodically check `self.dying`
        self.sock_fd.settimeout(1.0)

        self.my_port = self.sock_fd.getsockname()[1]

        # Start the backend thread
        self.thread = threading.Thread(target=self.backend, daemon=True)
        self.thread.start()
        return EXIT_SUCCESS

    def close(self):
        """
        Close the socket and stop the backend thread.
        """
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

        return EXIT_SUCCESS

    def send(self, data):
        """
        Send data reliably to the peer (stop-and-wait style).
        """
        if not self.conn:
            raise ValueError("Connection not established.")
        with self.send_lock:
            self.send_segment(data)

    def recv(self, buf, length, flags):
        """
        Retrieve received data from the buffer, with optional blocking behavior.

        :param buf: Buffer to store received data (list of bytes or bytearray).
        :param length: Maximum length of data to read
        :param flags: ReadMode flag to control blocking behavior
        :return: Number of bytes read
        """
        read_len = 0

        if length < 0:
            print("ERROR: Negative length")
            return EXIT_ERROR

        # If blocking read, wait until there's data in buffer
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

                    # Remove data from the buffer
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
        使用滑动窗口机制发送数据
        """
        offset = 0
        total_len = len(data)
        
        # 将数据添加到发送缓冲区
        self.send_buffer += data
        
        # 循环发送符合窗口大小的数据
        self.send_from_buffer()
        
        # 等待所有数据被确认
        with self.recv_lock:
            while len(self.send_buffer) > 0 or self.flight_size > 0:
                self.wait_cond.wait(timeout=0.1)
            
        return EXIT_SUCCESS

    def send_from_buffer(self):
        """
        从发送缓冲区发送数据，遵循滑动窗口限制
        """
        # 计算可用窗口
        available_window = min(self.cwnd, self.window["adv_window"]) - self.flight_size
        
        if available_window <= 0 or not self.send_buffer:
            return
        
        # 确定要发送的数据量
        bytes_to_send = min(available_window, len(self.send_buffer), MSS)
        
        if bytes_to_send <= 0:
            return
        
        # 获取当前序列号
        seq_no = self.window["next_seq_to_send"]
        data_to_send = self.send_buffer[:bytes_to_send]
        
        # 创建数据包
        segment = Packet(
            seq=seq_no,
            ack=self.window["last_ack"],
            flags=0,
            window=self.window["adv_window"],
            payload=data_to_send
        )
        
        print(f"发送段 (seq={seq_no}, 长度={bytes_to_send})")
        
        # 发送数据包
        self.sock_fd.sendto(segment.encode(), self.conn)
        
        # 更新状态
        self.send_buffer = self.send_buffer[bytes_to_send:]
        self.window["next_seq_to_send"] += bytes_to_send
        self.flight_size += bytes_to_send
        
        # 记录发送时间用于RTT计算和重传
        self.unacked_segments[seq_no] = (time.time(), data_to_send, 0)
        
        # 继续发送如果窗口允许且还有数据
        self.send_from_buffer()

    def wait_for_ack(self, ack_goal):
        """
        Wait for 'next_seq_expected' to reach or exceed 'ack_goal' within DEFAULT_TIMEOUT.
        Return True if ack arrived in time; False on timeout.
        """
        with self.recv_lock:
            start = time.time()
            while self.window["next_seq_expected"] < ack_goal:
                elapsed = time.time() - start
                remaining = DEFAULT_TIMEOUT - elapsed
                if remaining <= 0:
                    return False

                self.wait_cond.wait(timeout=remaining)

            return True

    def backend(self):
        """
        后台循环，处理接收数据和发送确认
        """
        if self.sock_type == "TCP_LISTENER":
            self.state = State.LISTEN
        
        while not self.dying:
            try:
                # 处理重传
                self.check_retransmissions()
                
                # 接收数据
                try:
                    data, addr = self.sock_fd.recvfrom(2048)
                    packet = Packet.decode(data)
                    
                    if packet is None:
                        continue
                        
                    # 如果没有设置对等方，建立连接（对于监听者）
                    if self.conn is None:
                        self.conn = addr
                        
                    # 基于当前状态处理数据包
                    self.handle_packet(packet, addr)
                    
                except socket.timeout:
                    continue
                
            except Exception as e:
                if not self.dying:
                    print(f"后台出错: {e}")

    def connect(self, server_ip, port):
        """
        与服务器建立连接 (TCP三次握手)
        """
        if self.state != State.CLOSED:
            print("错误: 无法连接，套接字不处于CLOSED状态")
            return EXIT_ERROR
        
        # 初始化为随机序列号
        import random
        self.window["next_seq_to_send"] = random.randint(0, 2**32 - 1)
        
        # 发送SYN包
        syn_packet = Packet(
            seq=self.window["next_seq_to_send"], 
            ack=0, 
            flags=SYN_FLAG,
            window=self.window["adv_window"]
        )
        
        self.conn = (server_ip, port)
        self.state = State.SYN_SENT
        
        # 发送SYN包并等待SYN-ACK
        retry_count = 0
        while retry_count < 3:  # 最多重试3次
            print(f"发送SYN (seq={self.window['next_seq_to_send']})")
            self.sock_fd.sendto(syn_packet.encode(), self.conn)
            
            # 等待SYN-ACK
            with self.recv_lock:
                success = self.wait_cond.wait(timeout=self.rto)
                
                if self.state == State.ESTABLISHED:
                    print("连接已建立")
                    return EXIT_SUCCESS
                
        retry_count += 1
        
        print("连接超时")
        self.state = State.CLOSED
        return EXIT_ERROR

    def accept(self):
        """
        接受来自客户端的连接
        """
        if self.state != State.LISTEN:
            print("错误: 无法接受连接，套接字不处于LISTEN状态")
            return EXIT_ERROR
        
        # 已在backend线程中处理SYN并发送SYN-ACK
        # 等待进入ESTABLISHED状态
        with self.recv_lock:
            while self.state != State.ESTABLISHED and not self.dying:
                self.wait_cond.wait(timeout=1.0)
            
        if self.state == State.ESTABLISHED:
            return EXIT_SUCCESS
        else:
            return EXIT_ERROR

    def close(self):
        """
        关闭连接 (TCP四次挥手)
        """
        # 如果尚未建立连接，直接关闭
        if self.state in [State.CLOSED, State.LISTEN]:
            return super().close()
        
        # 如果已经建立连接，发送FIN
        if self.state == State.ESTABLISHED:
            fin_packet = Packet(
                seq=self.window["next_seq_to_send"], 
                ack=self.window["last_ack"],
                flags=FIN_FLAG,
                window=self.window["adv_window"]
            )
            
            print(f"发送FIN (seq={self.window['next_seq_to_send']})")
            self.sock_fd.sendto(fin_packet.encode(), self.conn)
            self.state = State.FIN_SENT
            
            # 增加序列号，FIN占用一个序列号
            self.window["next_seq_to_send"] += 1
            
            # 等待FIN-ACK或对方的FIN
            with self.recv_lock:
                self.wait_cond.wait(timeout=self.rto)
        
        # 在TIME_WAIT状态等待2MSL时间
        if self.state == State.TIME_WAIT:
            time.sleep(2 * self.rto)  # 简化的2MSL等待
        
        self.state = State.CLOSED
        return super().close()

    def update_rtt(self, measured_rtt):
        """
        使用EWMA算法更新RTT估计
        """
        if self.srtt is None:
            # 首次RTT测量
            self.srtt = measured_rtt
            self.rttvar = measured_rtt / 2
        else:
            # 更新SRTT和RTTVAR
            self.rttvar = (1 - RTT_ALPHA) * self.rttvar + RTT_ALPHA * abs(self.srtt - measured_rtt)
            self.srtt = (1 - RTT_ALPHA) * self.srtt + RTT_ALPHA * measured_rtt
            
        # 更新RTO
        self.rto = self.srtt + max(0.01, RTO_BETA * self.rttvar)
        # 限制RTO范围
        self.rto = min(max(self.rto, 0.2), 60.0)
        
        print(f"更新RTT: 测量值={measured_rtt:.3f}s, SRTT={self.srtt:.3f}s, RTO={self.rto:.3f}s")

    def check_retransmissions(self):
        """
        检查并重传超时的包
        """
        now = time.time()
        segments_to_retransmit = []
        
        # 查找所有超时的包
        for seq, (time_sent, data, retries) in list(self.unacked_segments.items()):
            if now - time_sent > self.rto:
                if retries >= 5:  # 最多重试5次
                    print(f"段 {seq} 重传失败过多，放弃")
                    self.unacked_segments.pop(seq)
                else:
                    segments_to_retransmit.append((seq, data))
                    self.unacked_segments[seq] = (now, data, retries + 1)
                    
        # 重传超时的包
        for seq, data in segments_to_retransmit:
            print(f"重传段 (seq={seq}, 大小={len(data)})")
            
            # 创建一个新的包用于重传
            retrans_packet = Packet(
                seq=seq,
                ack=self.window["last_ack"],
                flags=0,
                window=self.window["adv_window"],
                payload=data
            )
            
            # 发送重传包
            self.sock_fd.sendto(retrans_packet.encode(), self.conn)

    def handle_packet(self, packet, addr):
        """
        根据当前状态处理接收到的数据包
        """
        # 处理SYN包
        if packet.flags & SYN_FLAG:
            self.handle_syn(packet, addr)
            return
        
        # 处理FIN包
        if packet.flags & FIN_FLAG:
            self.handle_fin(packet, addr)
            return
        
        # 处理ACK包
        if packet.flags & ACK_FLAG:
            self.handle_ack(packet)
        
        # 处理数据包
        if packet.payload and self.state == State.ESTABLISHED:
            self.handle_data(packet, addr)
        
    def handle_syn(self, packet, addr):
        """
        处理SYN包
        """
        # 监听者收到SYN，响应SYN-ACK
        if self.state == State.LISTEN:
            self.conn = addr
            self.window["last_ack"] = packet.seq + 1
            
            # 初始化自己的序列号
            import random
            self.window["next_seq_to_send"] = random.randint(0, 2**32 - 1)
            
            # 发送SYN-ACK
            syn_ack_packet = Packet(
                seq=self.window["next_seq_to_send"],
                ack=self.window["last_ack"],
                flags=SYN_FLAG | ACK_FLAG,
                window=self.window["adv_window"]
            )
            
            print(f"收到SYN (seq={packet.seq})，发送SYN-ACK")
            self.sock_fd.sendto(syn_ack_packet.encode(), addr)
            
            # 更新状态
            self.state = State.SYN_RECEIVED
            self.window["next_seq_to_send"] += 1  # SYN占用一个序列号
            
        # SYN发送者收到SYN-ACK，发送ACK
        elif self.state == State.SYN_SENT and (packet.flags & ACK_FLAG):
            self.window["last_ack"] = packet.seq + 1
            self.window["next_seq_expected"] = packet.ack
            
            # 发送ACK确认
            ack_packet = Packet(
                seq=self.window["next_seq_to_send"],
                ack=self.window["last_ack"],
                flags=ACK_FLAG,
                window=self.window["adv_window"]
            )
            
            print(f"收到SYN-ACK，发送ACK")
            self.sock_fd.sendto(ack_packet.encode(), addr)
            
            # 更新状态
            self.state = State.ESTABLISHED
            
            # 通知等待的线程
            with self.wait_cond:
                self.wait_cond.notify_all()

