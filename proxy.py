#!/usr/bin/env python3
import sys
import socket
import select
import json
import uuid

# A simple container for connection state.
class Connection:
    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.buffer = b''       # Incoming data buffer.
        self.out_buffer = b''   # Data waiting to be sent.
        self.request_id = None  # Added to store request ID

# Main proxy server class.
class ProxyServer:
    def __init__(self, listen_port, backend_servers):
        self.listen_port = listen_port
        self.backend_servers = backend_servers
        self.backend_index = 0  # Round-robin counter.

        # Mapping: fd -> (type, connection object)
        # Types are 'listener', 'client', or 'backend'
        self.fd_to_conn = {}

        # This mapping lets us route backend responses back to the originating client.
        # request_id -> client Connection
        self.request_map = {}

        self.epoll = select.epoll()
        self.listen_socket = self.setup_listen_socket()

    def setup_listen_socket(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setblocking(False)
        s.bind(('', self.listen_port))
        s.listen(128)
        self.epoll.register(s.fileno(), select.EPOLLIN)
        self.fd_to_conn[s.fileno()] = ('listener', s)
        print(f"Listening on port {self.listen_port} …")
        return s

    def get_next_backend(self):
        # Simple round-robin over the backend servers list.
        backend = self.backend_servers[self.backend_index]
        self.backend_index = (self.backend_index + 1) % len(self.backend_servers)
        return backend

    def handle_new_connection(self, fd):
        # Accept the new client connection.
        client_sock, addr = self.listen_socket.accept()
        client_sock.setblocking(False)
        conn = Connection(client_sock, addr)
        self.fd_to_conn[client_sock.fileno()] = ('client', conn)
        self.epoll.register(client_sock.fileno(), select.EPOLLIN)
        print(f"Accepted connection from {addr}")

    def handle_client_read(self, fd, conn):
        try:
            data = conn.sock.recv(4096)
            if not data:
                self.close_connection(fd)
                return
            conn.buffer += data
            # Look for a complete HTTP request (ending with CRLF CRLF).
            while b"\r\n\r\n" in conn.buffer:
                request_bytes, conn.buffer = conn.buffer.split(b"\r\n\r\n", 1)
                request_bytes += b"\r\n\r\n"
                self.process_request(conn, request_bytes)
        except Exception as e:
            print(f"Error reading from client: {e}")
            self.close_connection(fd)

    def process_request(self, client_conn, request_bytes):
        # Generate a unique request identifier.
        request_id = str(uuid.uuid4())
        
        # Decode and modify the request to include an X-Request-ID header.
        try:
            request_text = request_bytes.decode()
        except UnicodeDecodeError:
            print("Failed to decode request.")
            return
        
        request_lines = request_text.split("\r\n")
        # Insert the header if it is not already present.
        if not any(line.lower().startswith("x-request-id:") for line in request_lines):
            # Place the header right after the request line.
            request_lines.insert(1, f"X-Request-ID: {request_id}")
        modified_request = "\r\n".join(request_lines).encode() + b"\r\n\r\n"

        # Select a backend server using round-robin.
        backend_info = self.get_next_backend()
        print(f"Forwarding request {request_id} to backend {backend_info}")

        # Establish a non-blocking connection to the backend.
        backend_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        backend_sock.setblocking(False)
        
        try:
            backend_sock.connect((backend_info["ip"], backend_info["port"]))
        except BlockingIOError:
            pass
        except Exception as e:
            print(f"连接后端失败: {e}")
            return

        backend_conn = Connection(backend_sock, (backend_info["ip"], backend_info["port"]))
        backend_conn.out_buffer = modified_request
        backend_conn.request_id = request_id

        self.fd_to_conn[backend_sock.fileno()] = ('backend', backend_conn)
        # 同时监听写入和错误事件
        self.epoll.register(backend_sock.fileno(), select.EPOLLOUT | select.EPOLLERR)
        self.request_map[request_id] = client_conn

    def handle_backend_write(self, fd, conn):
        try:
            # 检查连接是否成功建立
            err = conn.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if err != 0:
                print(f"后端连接失败，错误码: {err}")
                self.close_connection(fd)
                return

            if conn.out_buffer:
                sent = conn.sock.send(conn.out_buffer)
                conn.out_buffer = conn.out_buffer[sent:]
            
            if not conn.out_buffer:
                # 只监听读取事件
                self.epoll.modify(fd, select.EPOLLIN | select.EPOLLERR)
        except Exception as e:
            print(f"向后端写入数据时出错: {e}")
            self.close_connection(fd)

    def handle_backend_read(self, fd, conn):
        try:
            data = conn.sock.recv(4096)
            if not data:
                self.close_connection(fd)
                return
            
            conn.buffer += data
            
            # 处理完整的HTTP响应
            while b"\r\n\r\n" in conn.buffer:
                response_head, rest = conn.buffer.split(b"\r\n\r\n", 1)
                
                # 尝试从响应头中获取Content-Length
                content_length = 0
                for line in response_head.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        content_length = int(line.split(b":")[1].strip())
                        break
                
                # 如果响应体不完整，继续等待数据
                if len(rest) < content_length:
                    break
                
                # 提取完整的响应
                response_body = rest[:content_length]
                conn.buffer = rest[content_length:]
                complete_response = response_head + b"\r\n\r\n" + response_body
                
                try:
                    response_text = complete_response.decode()
                    request_id = None
                    for line in response_text.split("\r\n"):
                        if line.lower().startswith("x-request-id:"):
                            request_id = line.split(":", 1)[1].strip()
                            break
                    
                    if request_id and request_id in self.request_map:
                        client_conn = self.request_map[request_id]
                        client_conn.out_buffer += complete_response
                        self.epoll.modify(client_conn.sock.fileno(), select.EPOLLOUT)
                        # 只在处理完响应后删除映射
                        self.request_map.pop(request_id)
                except Exception as e:
                    print(f"处理响应时出错: {e}")
                
                # 如果没有更多数据要处理，关闭后端连接
                if not conn.buffer:
                    self.close_connection(fd)
                    break
                    
        except Exception as e:
            print(f"从后端读取数据时出错: {e}")
            self.close_connection(fd)

    def handle_client_write(self, fd, conn):
        try:
            if conn.out_buffer:
                sent = conn.sock.send(conn.out_buffer)
                conn.out_buffer = conn.out_buffer[sent:]
            if not conn.out_buffer:
                self.epoll.modify(fd, select.EPOLLIN)
        except Exception as e:
            print(f"Error writing to client: {e}")
            self.close_connection(fd)

    def close_connection(self, fd):
        try:
            self.epoll.unregister(fd)
        except Exception as e:
            print(f"Error unregistering fd {fd}: {e}")
        conn_tuple = self.fd_to_conn.pop(fd, None)
        if conn_tuple:
            conn_type, obj = conn_tuple
            if conn_type in ("client", "backend"):
                try:
                    obj.sock.close()
                except Exception as e:
                    print(f"Error closing socket: {e}")

    def run(self):
        try:
            while True:
                events = self.epoll.poll(1)
                for fd, event in events:
                    if fd not in self.fd_to_conn:
                        continue
                    conn_type, conn_obj = self.fd_to_conn[fd]
                    if conn_type == 'listener':
                        self.handle_new_connection(fd)
                    elif conn_type == 'client':
                        if event & select.EPOLLIN:
                            self.handle_client_read(fd, conn_obj)
                        elif event & select.EPOLLOUT:
                            self.handle_client_write(fd, conn_obj)
                        elif event & select.EPOLLHUP:
                            self.close_connection(fd)
                    elif conn_type == 'backend':
                        if event & select.EPOLLOUT:
                            self.handle_backend_write(fd, conn_obj)
                        elif event & select.EPOLLIN:
                            self.handle_backend_read(fd, conn_obj)
                        elif event & select.EPOLLHUP:
                            self.close_connection(fd)
        finally:
            self.epoll.close()
            self.listen_socket.close()

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 proxy.py <servers.conf> <listen_port>")
        sys.exit(1)

    conf_file = sys.argv[1]
    listen_port = int(sys.argv[2])
    with open(conf_file, "r") as f:
        config = json.load(f)
    backend_servers = config["backend_servers"]

    proxy = ProxyServer(listen_port, backend_servers)
    proxy.run()

if __name__ == '__main__':
    main()
