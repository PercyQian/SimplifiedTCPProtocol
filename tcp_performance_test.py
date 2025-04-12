#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import random
import string
import threading
import numpy as np

# Set matplotlib to use non-interactive backend before importing pyplot
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
from matplotlib.ticker import FormatStrFormatter

from transport import TransportSocket, ReadMode

# Use standard fonts that work across platforms
plt.rcParams["font.family"] = "DejaVu Sans"

def generate_random_data(size):
    """Generate random data of specified size"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=size)).encode()

def create_test_file(filename, size_mb):
    """Create a test file of specified size in MB"""
    size_bytes = size_mb * 1024 * 1024
    chunk_size = 1024 * 1024  # 1MB chunks

    with open(filename, 'wb') as f:
        remaining = size_bytes
        while remaining > 0:
            chunk = min(remaining, chunk_size)
            f.write(generate_random_data(chunk))
            remaining -= chunk
    
    print(f"Test file created: {filename} ({size_mb}MB)")

def server_thread(ready_event, done_event):
    """Server thread that receives and responds to client requests"""
    server_socket = TransportSocket()
    server_socket.socket(sock_type="TCP_LISTENER", port=54321)
    
    print("Server: Waiting for client connection...")
    ready_event.set()  # Notify main thread that server is ready
    
    # Receive request data from client
    buf = [b""]
    server_socket.recv(buf, 1024, flags=ReadMode.NO_FLAG)
    print(f"Server: Received request from client:\n{buf[0].decode()}")
    
    # Send test file to client
    test_file = "server_test_file.dat"
    if not os.path.exists(test_file):
        create_test_file(test_file, 10)  # Create a 10MB test file
    
    with open(test_file, "rb") as f:
        file_data = f.read()
        print(f"Server: Sending file '{test_file}' to client...")
        server_socket.send(file_data)
    
    # Send a large amount of random data to test congestion control
    data_size = 5 * 1024 * 1024  # 5MB
    random_data = generate_random_data(data_size)
    print(f"Server: Sending {data_size/1024/1024:.2f}MB of random data...")
    server_socket.send(random_data)
    
    # Wait for client to process data
    time.sleep(2)
    
    # Close connection
    server_socket.close()
    print("Server: Connection closed")
    done_event.set()  # Notify main thread that server is done

def client_thread(ready_event, done_event):
    """Client thread that sends requests and receives responses"""
    # Wait for server to be ready
    ready_event.wait()
    time.sleep(1)  # Give server some time to fully initialize
    
    client_socket = TransportSocket()
    client_socket.socket(sock_type="TCP_INITIATOR", port=54321, server_ip="127.0.0.1")
    
    # Send request
    request = b"REQUEST: Send test data"
    print("Client: Sending request...")
    client_socket.send(request)
    
    # Receive file data
    print("Client: Starting to receive data...")
    total_received = 0
    max_retries = 3
    retries = 0
    
    while retries < max_retries:
        try:
            empty_count = 0
            while empty_count < 5:  # Try a few times before considering connection done
                buf = [b""]
                bytes_read = client_socket.recv(buf, 8192, flags=ReadMode.NO_WAIT)
                if bytes_read <= 0:
                    empty_count += 1
                    time.sleep(0.1)  # Short delay before trying again
                    continue
                    
                empty_count = 0  # Reset empty count when we receive data
                total_received += bytes_read
                print(f"Client: Received {total_received/1024/1024:.2f}MB of data")
                
                # Artificially introduce delay to simulate network latency and congestion
                if random.random() < 0.1:  # 10% chance to introduce additional delay
                    delay = random.uniform(0.05, 0.2)  # 50-200ms delay
                    time.sleep(delay)
            
            # If we've received some data, consider it a success
            if total_received > 0:
                break
                
            retries += 1
            print(f"Client: Retry {retries}/{max_retries} - No data received")
            
        except Exception as e:
            retries += 1
            print(f"Client: Error during data reception: {e}")
            print(f"Client: Retry {retries}/{max_retries}")
            time.sleep(1)
    
    # Get performance data
    perf_data = client_socket.get_performance_data()
    throughput_times, throughput_values = client_socket.calculate_throughput()
    
    # Close connection
    client_socket.close()
    print(f"Client: Connection closed, total received {total_received/1024/1024:.2f}MB of data")
    
    # Only generate graphs if we have some valid data
    if perf_data["timestamp"] and len(perf_data["timestamp"]) > 2:
        # Plot performance graphs
        plot_performance_graphs(perf_data, throughput_times, throughput_values)
    else:
        print("Client: Not enough performance data collected. Skipping graph generation.")
    
    done_event.set()  # Notify main thread that client is done

def plot_performance_graphs(perf_data, throughput_times, throughput_values):
    """Plot TCP performance graphs"""
    # Check if we have data to plot
    if not perf_data["timestamp"]:
        print("Warning: No performance data collected. Cannot generate graphs.")
        return
        
    plt.figure(figsize=(15, 15))
    
    # 1. Congestion window (cwnd) and slow start threshold (ssthresh) over time
    plt.subplot(3, 1, 1)
    plt.step(perf_data["timestamp"], perf_data["cwnd"], label='Congestion Window (cwnd)', where='post')
    plt.step(perf_data["timestamp"], perf_data["ssthresh"], label='Slow Start Threshold (ssthresh)', linestyle='--', where='post')
    plt.xlabel('Time (seconds)')
    plt.ylabel('Bytes')
    plt.title('Congestion Window and Slow Start Threshold over Time')
    plt.grid(True)
    plt.legend()
    
    # 2. RTT over time
    plt.subplot(3, 1, 2)
    valid_rtt_indices = [i for i, rtt in enumerate(perf_data["rtt"]) if rtt > 0]
    valid_timestamps = [perf_data["timestamp"][i] for i in valid_rtt_indices]
    valid_rtts = [perf_data["rtt"][i] for i in valid_rtt_indices]
    
    if valid_rtts:
        plt.plot(valid_timestamps, valid_rtts, marker='o', markersize=3, linestyle='-')
        plt.xlabel('Time (seconds)')
        plt.ylabel('RTT (seconds)')
        plt.title('Round Trip Time (RTT) over Time')
        plt.grid(True)
    else:
        plt.text(0.5, 0.5, "No valid RTT data available", horizontalalignment='center', verticalalignment='center')
    
    # 3. Throughput over time
    plt.subplot(3, 1, 3)
    if throughput_times and throughput_values:
        # Use moving average to smooth throughput graph
        window_size = min(5, len(throughput_values))
        if window_size > 1:
            throughput_values_smooth = np.convolve(throughput_values, np.ones(window_size)/window_size, mode='valid')
            throughput_times_smooth = throughput_times[window_size-1:]
            plt.plot(throughput_times_smooth, throughput_values_smooth, label='Smoothed Throughput')
        
        plt.plot(throughput_times, throughput_values, alpha=0.3, label='Raw Throughput')
        plt.xlabel('Time (seconds)')
        plt.ylabel('Throughput (bytes/sec)')
        plt.title('Throughput over Time')
        plt.grid(True)
        plt.legend()
    else:
        plt.text(0.5, 0.5, "No valid throughput data available", horizontalalignment='center', verticalalignment='center')
    
    plt.tight_layout()
    plt.savefig('tcp_performance.png', dpi=300)
    print("Performance graphs saved to tcp_performance.png")
    
    # Generate additional statistics graphs
    generate_additional_graphs(perf_data)

def generate_additional_graphs(perf_data):
    """Generate additional performance statistics graphs"""
    # Check if we have data to plot
    if not perf_data["timestamp"]:
        print("Warning: No performance data collected. Cannot generate additional graphs.")
        return
        
    fig = plt.figure(figsize=(15, 10))
    
    # 1. Comparison of cwnd growth in slow start and congestion avoidance phases
    plt.subplot(2, 1, 1)
    # Find data points for slow start and congestion avoidance phases
    slow_start_indices = []
    cong_avoid_indices = []
    
    # Detect phase change points to distinguish different phases
    for i in range(1, len(perf_data["cwnd"])):
        # Consider congestion window growth fast and below ssthresh as slow start
        if perf_data["cwnd"][i] < perf_data["ssthresh"][i]:
            slow_start_indices.append(i)
        else:
            cong_avoid_indices.append(i)
    
    # Plot cwnd curves for different phases
    if slow_start_indices:
        slow_start_times = [perf_data["timestamp"][i] for i in slow_start_indices]
        slow_start_cwnd = [perf_data["cwnd"][i] for i in slow_start_indices]
        plt.scatter(slow_start_times, slow_start_cwnd, c='r', marker='o', label='Slow Start Phase', alpha=0.5)
    
    if cong_avoid_indices:
        cong_avoid_times = [perf_data["timestamp"][i] for i in cong_avoid_indices]
        cong_avoid_cwnd = [perf_data["cwnd"][i] for i in cong_avoid_indices]
        plt.scatter(cong_avoid_times, cong_avoid_cwnd, c='b', marker='x', label='Congestion Avoidance Phase', alpha=0.5)
    
    plt.xlabel('Time (seconds)')
    plt.ylabel('Congestion Window (bytes)')
    plt.title('Congestion Window Growth in Different Phases')
    plt.grid(True)
    plt.legend()
    
    # 2. Difference between sent and acknowledged bytes (unacknowledged data in network)
    plt.subplot(2, 1, 2)
    in_flight_data = []
    for i in range(len(perf_data["timestamp"])):
        in_flight = perf_data["bytes_sent"][i] - perf_data["bytes_acked"][i]
        in_flight_data.append(in_flight)
    
    plt.plot(perf_data["timestamp"], in_flight_data)
    plt.xlabel('Time (seconds)')
    plt.ylabel('Unacknowledged Data (bytes)')
    plt.title('Unacknowledged Data in Network over Time')
    plt.grid(True)
    
    plt.tight_layout()
    plt.savefig('tcp_statistics.png', dpi=300)
    print("Additional statistics graphs saved to tcp_statistics.png")

def main():
    """Main function to start the server and client threads for TCP performance testing"""
    print("Starting TCP performance test...")
    
    # Events for thread synchronization
    server_ready = threading.Event()
    server_done = threading.Event()
    client_done = threading.Event()
    
    # Create and start server thread
    server = threading.Thread(target=server_thread, args=(server_ready, server_done))
    server.daemon = True
    server.start()
    
    # Create and start client thread
    client = threading.Thread(target=client_thread, args=(server_ready, client_done))
    client.daemon = True
    client.start()
    
    # Wait for both threads to complete
    client.join()
    server.join(timeout=5)  # Give server a timeout to avoid waiting indefinitely
    
    print("TCP performance test completed!")

if __name__ == "__main__":
    main() 