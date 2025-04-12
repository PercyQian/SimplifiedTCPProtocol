#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import numpy as np
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
import random
import time

# Use standard fonts
plt.rcParams["font.family"] = "DejaVu Sans"

def generate_sample_data():
    """Generate sample data for TCP performance graphs"""
    # Create time vector (30 seconds)
    timestamps = np.linspace(0, 30, 300)
    
    # Initial values
    initial_cwnd = 1382  # Initial congestion window (bytes)
    initial_ssthresh = 88448  # Initial slow start threshold (bytes)
    mss = 1382  # Maximum segment size
    
    # Generate congestion window data with slow start and congestion avoidance phases
    cwnd = []
    ssthresh = []
    current_cwnd = initial_cwnd
    current_ssthresh = initial_ssthresh
    
    # Create congestion events
    congestion_events = [50, 150, 220]  # Indices where congestion occurs
    
    for i in range(len(timestamps)):
        if i in congestion_events:
            # Congestion event: halve ssthresh, reset cwnd
            current_ssthresh = max(2 * mss, current_cwnd // 2)
            current_cwnd = initial_cwnd
        else:
            if current_cwnd < current_ssthresh:
                # Slow start: exponential growth
                current_cwnd = min(current_cwnd * 2, current_ssthresh)
            else:
                # Congestion avoidance: linear growth
                current_cwnd += mss * mss / current_cwnd
        
        cwnd.append(current_cwnd)
        ssthresh.append(current_ssthresh)
    
    # Generate RTT data
    base_rtt = 0.05  # 50ms base RTT
    rtt = []
    
    for i in range(len(timestamps)):
        # Add some variance to RTT
        if i in congestion_events or i-1 in congestion_events:
            # Higher RTT during congestion
            current_rtt = base_rtt + random.uniform(0.1, 0.3)
        else:
            current_rtt = base_rtt + random.uniform(-0.01, 0.05)
        
        rtt.append(max(0.01, current_rtt))  # Ensure RTT is positive
    
    # Generate throughput data
    throughput = []
    throughput_times = timestamps[1:]  # Skip first timestamp for throughput calculation
    
    for i in range(1, len(timestamps)):
        # Throughput is roughly proportional to cwnd/RTT but with some variance
        current_throughput = cwnd[i] / rtt[i] * (0.8 + 0.4 * random.random())
        
        # Reduced throughput during congestion
        if i in congestion_events or i-1 in congestion_events or i-2 in congestion_events:
            current_throughput *= 0.3
            
        throughput.append(current_throughput)
    
    # Create bytes sent and acknowledged
    bytes_sent = []
    bytes_acked = []
    
    total_sent = 0
    total_acked = 0
    
    for i in range(len(timestamps)):
        # Calculate how much data is sent each time step
        if i > 0:
            time_delta = timestamps[i] - timestamps[i-1]
            sent_delta = cwnd[i] * time_delta / rtt[i]
            total_sent += sent_delta
            
            # Acknowledgments lag behind sends
            if i > 5:
                ack_delta = cwnd[i-5] * time_delta / rtt[i-5]
                total_acked += ack_delta
            else:
                total_acked = 0
        
        bytes_sent.append(total_sent)
        bytes_acked.append(total_acked)
    
    # Pack data into a dictionary similar to what the TCP implementation would provide
    perf_data = {
        "timestamp": timestamps,
        "cwnd": cwnd,
        "ssthresh": ssthresh,
        "rtt": rtt,
        "bytes_sent": bytes_sent,
        "bytes_acked": bytes_acked
    }
    
    return perf_data, throughput_times, throughput

def plot_performance_graphs(perf_data, throughput_times, throughput_values):
    """Plot TCP performance graphs"""
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
    plt.plot(perf_data["timestamp"], perf_data["rtt"], marker='.', markersize=3, linestyle='-')
    plt.xlabel('Time (seconds)')
    plt.ylabel('RTT (seconds)')
    plt.title('Round Trip Time (RTT) over Time')
    plt.grid(True)
    
    # 3. Throughput over time
    plt.subplot(3, 1, 3)
    # Use moving average to smooth throughput graph
    window_size = 10
    throughput_values_smooth = np.convolve(throughput_values, np.ones(window_size)/window_size, mode='valid')
    throughput_times_smooth = throughput_times[window_size-1:]
    plt.plot(throughput_times_smooth, throughput_values_smooth, label='Smoothed Throughput')
    
    plt.plot(throughput_times, throughput_values, alpha=0.3, label='Raw Throughput')
    plt.xlabel('Time (seconds)')
    plt.ylabel('Throughput (bytes/sec)')
    plt.title('Throughput over Time')
    plt.grid(True)
    plt.legend()
    
    plt.tight_layout()
    plt.savefig('tcp_performance_sample.png', dpi=300)
    print("Performance graphs saved to tcp_performance_sample.png")
    
    # Generate additional statistics graphs
    generate_additional_graphs(perf_data)

def generate_additional_graphs(perf_data):
    """Generate additional performance statistics graphs"""
    fig = plt.figure(figsize=(15, 10))
    
    # 1. Comparison of cwnd growth in slow start and congestion avoidance phases
    plt.subplot(2, 1, 1)
    # Find data points for slow start and congestion avoidance phases
    slow_start_indices = []
    cong_avoid_indices = []
    
    # Detect phase change points to distinguish different phases
    for i in range(len(perf_data["cwnd"])):
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
    plt.savefig('tcp_statistics_sample.png', dpi=300)
    print("Additional statistics graphs saved to tcp_statistics_sample.png")

def main():
    """Generate sample TCP performance graphs with simulated data"""
    print("Generating sample TCP performance graphs...")
    
    # Generate simulated data
    perf_data, throughput_times, throughput_values = generate_sample_data()
    
    # Plot performance graphs
    plot_performance_graphs(perf_data, throughput_times, throughput_values)
    
    print("Sample TCP performance graphs generated successfully!")

if __name__ == "__main__":
    main() 