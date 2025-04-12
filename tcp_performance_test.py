#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import random
import string
import threading
import matplotlib.pyplot as plt
from matplotlib.ticker import FormatStrFormatter
import numpy as np
from transport import TransportSocket, ReadMode

# 设置中文字体支持
plt.rcParams['font.sans-serif'] = ['SimHei']  # 用来正常显示中文标签
plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号

def generate_random_data(size):
    """生成指定大小的随机数据"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=size)).encode()

def create_test_file(filename, size_mb):
    """创建指定大小的测试文件"""
    size_bytes = size_mb * 1024 * 1024
    chunk_size = 1024 * 1024  # 1MB块

    with open(filename, 'wb') as f:
        remaining = size_bytes
        while remaining > 0:
            chunk = min(remaining, chunk_size)
            f.write(generate_random_data(chunk))
            remaining -= chunk
    
    print(f"已创建测试文件: {filename} ({size_mb}MB)")

def server_thread(ready_event, done_event):
    """服务器线程，接收并响应客户端请求"""
    server_socket = TransportSocket()
    server_socket.socket(sock_type="TCP_LISTENER", port=54321)
    
    print("服务器: 等待客户端连接...")
    ready_event.set()  # 通知主线程服务器已经准备好了
    
    # 接收客户端的请求数据
    buf = [b""]
    server_socket.recv(buf, 1024, flags=ReadMode.NO_FLAG)
    print(f"服务器: 收到客户端请求数据:\n{buf[0].decode()}")
    
    # 发送测试文件
    test_file = "server_test_file.dat"
    if not os.path.exists(test_file):
        create_test_file(test_file, 10)  # 创建10MB的测试文件
    
    with open(test_file, "rb") as f:
        file_data = f.read()
        print(f"服务器: 发送文件 '{test_file}' 到客户端...")
        server_socket.send(file_data)
    
    # 发送大量的随机数据以测试拥塞控制
    data_size = 5 * 1024 * 1024  # 5MB
    random_data = generate_random_data(data_size)
    print(f"服务器: 发送 {data_size/1024/1024:.2f}MB 随机数据...")
    server_socket.send(random_data)
    
    # 等待客户端处理完成
    time.sleep(2)
    
    # 关闭连接
    server_socket.close()
    print("服务器: 连接已关闭")
    done_event.set()  # 通知主线程服务器已经完成

def client_thread(ready_event, done_event):
    """客户端线程，发送请求并接收响应"""
    # 等待服务器准备就绪
    ready_event.wait()
    time.sleep(1)  # 给服务器一些时间来完全初始化
    
    client_socket = TransportSocket()
    client_socket.socket(sock_type="TCP_INITIATOR", port=54321, server_ip="127.0.0.1")
    
    # 发送请求
    request = b"REQUEST: Send test data"  # 修改为纯ASCII字符的字节字符串
    print("客户端: 发送请求...")
    client_socket.send(request)
    
    # 接收文件数据
    print("客户端: 开始接收数据...")
    total_received = 0
    
    while True:
        buf = [b""]
        bytes_read = client_socket.recv(buf, 8192, flags=ReadMode.NO_WAIT)
        if bytes_read <= 0:
            break
        
        total_received += bytes_read
        print(f"客户端: 已接收 {total_received/1024/1024:.2f}MB 数据")
        
        # 人为引入延迟来模拟网络延迟和拥塞
        if random.random() < 0.1:  # 10%概率引入额外延迟
            delay = random.uniform(0.05, 0.2)  # 50-200ms的延迟
            time.sleep(delay)
    
    # 获取性能数据
    perf_data = client_socket.get_performance_data()
    throughput_times, throughput_values = client_socket.calculate_throughput()
    
    # 关闭连接
    client_socket.close()
    print("客户端: 连接已关闭，总共接收 {:.2f}MB 数据".format(total_received/1024/1024))
    
    # 绘制性能图表
    plot_performance_graphs(perf_data, throughput_times, throughput_values)
    
    done_event.set()  # 通知主线程客户端已经完成

def plot_performance_graphs(perf_data, throughput_times, throughput_values):
    """绘制TCP性能图表"""
    plt.figure(figsize=(15, 15))
    
    # 1. 拥塞窗口(cwnd)和慢启动阈值(ssthresh)随时间的变化
    plt.subplot(3, 1, 1)
    plt.step(perf_data["timestamp"], perf_data["cwnd"], label='拥塞窗口 (cwnd)', where='post')
    plt.step(perf_data["timestamp"], perf_data["ssthresh"], label='慢启动阈值 (ssthresh)', linestyle='--', where='post')
    plt.xlabel('时间 (秒)')
    plt.ylabel('字节')
    plt.title('拥塞窗口和慢启动阈值随时间的变化')
    plt.grid(True)
    plt.legend()
    
    # 2. RTT随时间的变化
    plt.subplot(3, 1, 2)
    valid_rtt_indices = [i for i, rtt in enumerate(perf_data["rtt"]) if rtt > 0]
    valid_timestamps = [perf_data["timestamp"][i] for i in valid_rtt_indices]
    valid_rtts = [perf_data["rtt"][i] for i in valid_rtt_indices]
    
    if valid_rtts:
        plt.plot(valid_timestamps, valid_rtts, marker='o', markersize=3, linestyle='-')
        plt.xlabel('时间 (秒)')
        plt.ylabel('RTT (秒)')
        plt.title('RTT随时间的变化')
        plt.grid(True)
    else:
        plt.text(0.5, 0.5, "没有有效的RTT数据", horizontalalignment='center', verticalalignment='center')
    
    # 3. 吞吐量随时间的变化
    plt.subplot(3, 1, 3)
    if throughput_times and throughput_values:
        # 使用移动平均来平滑吞吐量图表
        window_size = min(5, len(throughput_values))
        if window_size > 1:
            throughput_values_smooth = np.convolve(throughput_values, np.ones(window_size)/window_size, mode='valid')
            throughput_times_smooth = throughput_times[window_size-1:]
            plt.plot(throughput_times_smooth, throughput_values_smooth, label='平滑吞吐量')
        
        plt.plot(throughput_times, throughput_values, alpha=0.3, label='原始吞吐量')
        plt.xlabel('时间 (秒)')
        plt.ylabel('吞吐量 (字节/秒)')
        plt.title('吞吐量随时间的变化')
        plt.grid(True)
        plt.legend()
    else:
        plt.text(0.5, 0.5, "没有有效的吞吐量数据", horizontalalignment='center', verticalalignment='center')
    
    plt.tight_layout()
    plt.savefig('tcp_performance.png', dpi=300)
    print("性能图表已保存到 tcp_performance.png")
    
    # 生成额外的统计图表
    generate_additional_graphs(perf_data)

def generate_additional_graphs(perf_data):
    """生成额外的性能统计图表"""
    fig = plt.figure(figsize=(15, 10))
    
    # 1. 慢启动和拥塞避免阶段的cwnd增长对比
    plt.subplot(2, 1, 1)
    # 找出慢启动和拥塞避免阶段的数据点
    slow_start_indices = []
    cong_avoid_indices = []
    
    # 检测拥塞状态的变化点来区分不同阶段
    for i in range(1, len(perf_data["cwnd"])):
        # 拥塞窗口增长快速且小于ssthresh的阶段视为慢启动
        if perf_data["cwnd"][i] < perf_data["ssthresh"][i]:
            slow_start_indices.append(i)
        else:
            cong_avoid_indices.append(i)
    
    # 绘制不同阶段的cwnd曲线
    if slow_start_indices:
        slow_start_times = [perf_data["timestamp"][i] for i in slow_start_indices]
        slow_start_cwnd = [perf_data["cwnd"][i] for i in slow_start_indices]
        plt.scatter(slow_start_times, slow_start_cwnd, c='r', marker='o', label='慢启动阶段', alpha=0.5)
    
    if cong_avoid_indices:
        cong_avoid_times = [perf_data["timestamp"][i] for i in cong_avoid_indices]
        cong_avoid_cwnd = [perf_data["cwnd"][i] for i in cong_avoid_indices]
        plt.scatter(cong_avoid_times, cong_avoid_cwnd, c='b', marker='x', label='拥塞避免阶段', alpha=0.5)
    
    plt.xlabel('时间 (秒)')
    plt.ylabel('拥塞窗口 (字节)')
    plt.title('不同阶段拥塞窗口的增长情况')
    plt.grid(True)
    plt.legend()
    
    # 2. 发送和确认字节的差异（表示网络中未确认的数据量）
    plt.subplot(2, 1, 2)
    in_flight_data = []
    for i in range(len(perf_data["timestamp"])):
        in_flight = perf_data["bytes_sent"][i] - perf_data["bytes_acked"][i]
        in_flight_data.append(in_flight)
    
    plt.plot(perf_data["timestamp"], in_flight_data)
    plt.xlabel('时间 (秒)')
    plt.ylabel('未确认数据量 (字节)')
    plt.title('网络中未确认的数据量随时间的变化')
    plt.grid(True)
    
    plt.tight_layout()
    plt.savefig('tcp_statistics.png', dpi=300)
    print("额外统计图表已保存到 tcp_statistics.png")

def main():
    """主函数，启动服务器和客户端线程进行TCP性能测试"""
    print("开始TCP性能测试...")
    
    # 用于线程同步的事件
    server_ready = threading.Event()
    server_done = threading.Event()
    client_done = threading.Event()
    
    # 创建并启动服务器线程
    server = threading.Thread(target=server_thread, args=(server_ready, server_done))
    server.daemon = True
    server.start()
    
    # 创建并启动客户端线程
    client = threading.Thread(target=client_thread, args=(server_ready, client_done))
    client.daemon = True
    client.start()
    
    # 等待两个线程完成
    client.join()
    server.join(timeout=5)  # 给服务器一个超时，避免无限期等待
    
    print("TCP性能测试完成!")

if __name__ == "__main__":
    main() 