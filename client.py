from transport import TransportSocket, ReadMode

def client_main():
    # 初始化客户端socket
    client_socket = TransportSocket()
    client_socket.socket(sock_type="TCP_INITIATOR", port=54321, server_ip="127.0.0.1")
    
    # 发送测试数据
    test_message = "你好，服务器！".encode()
    print("客户端: 发送消息...")
    client_socket.send(test_message)
    
    # 接收服务器的文件数据
    print("客户端: 等待接收服务器文件...")
    buf = [b""]
    bytes_received = client_socket.recv(buf, 1024, flags=ReadMode.NO_FLAG)
    print(f"客户端: 收到文件数据:\n{buf[0].decode()}")
    
    # 接收随机生成的数据
    print("客户端: 等待接收随机数据...")
    buf = [b""]
    bytes_received = client_socket.recv(buf, 1024, flags=ReadMode.NO_FLAG)
    print(f"客户端: 收到随机数据，长度: {len(buf[0])} 字节")
    
    # 关闭连接
    client_socket.close()

if __name__ == "__main__":
    client_main()
