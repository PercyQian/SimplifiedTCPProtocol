# TCP Performance Testing

This project contains a simplified TCP protocol implementation with congestion control algorithms (TCP Tahoe) that can be used to generate TCP performance metric graphs.

## Features

- Basic TCP connection management (3-way handshake, 4-way connection termination)
- Sliding window mechanism
- TCP Tahoe style congestion control:
  - Slow Start
  - Congestion Avoidance
  - Fast Retransmit

## Performance Metrics

The test script generates the following performance metric graphs:

1. Congestion window (cwnd) and slow start threshold (ssthresh) over time
2. Round Trip Time (RTT) over time
3. Throughput over time
4. Congestion window growth in different phases
5. Unacknowledged data in network over time

## Requirements

- Python 3.6+
- matplotlib
- numpy

## Installing Dependencies

```bash
pip install -r requirements.txt
```

## Running the Test

The test script starts a server and a client, transmits data using the TCP protocol, and collects performance metrics.

```bash
python tcp_performance_test.py
```

### Sample Graphs

If you're having issues running the actual TCP test or just want to see example graphs, you can use the sample graph generator:

```bash
python generate_sample_graphs.py
```

This will create sample TCP performance graphs using simulated data that demonstrates the key behaviors of TCP Tahoe congestion control, including:
- Slow start phase (exponential cwnd growth)
- Congestion avoidance phase (linear cwnd growth)
- Timeout and fast retransmit events
- The effect of congestion events on RTT and throughput

## Results

After running the test, two pairs of graph files will be generated:
- `tcp_performance.png` and `tcp_performance_sample.png`: Contains RTT, throughput, and congestion window graphs
- `tcp_statistics.png` and `tcp_statistics_sample.png`: Contains congestion control phase analysis and unacknowledged data graphs 