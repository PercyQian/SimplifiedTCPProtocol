# TCP性能测试

本项目包含一个简化的TCP协议实现，并添加了拥塞控制算法（TCP Tahoe），可以用于生成TCP性能指标图表。

## 特性

- 实现了基本的TCP连接管理（三次握手、四次挥手）
- 实现了滑动窗口机制
- 实现了TCP Tahoe风格的拥塞控制
  - 慢启动
  - 拥塞避免
  - 快速重传

## 性能指标

测试脚本可生成以下性能指标图表：

1. 拥塞窗口(cwnd)和慢启动阈值(ssthresh)随时间的变化
2. RTT随时间的变化
3. 吞吐量随时间的变化
4. 不同阶段拥塞窗口的增长情况
5. 网络中未确认的数据量随时间的变化

## 环境要求

- Python 3.6+
- matplotlib
- numpy

## 安装依赖

```bash
pip install -r requirements.txt
```

## 运行测试

测试脚本将启动一个服务器和一个客户端，通过TCP协议传输数据，并收集性能指标。

```bash
python tcp_performance_test.py
```

## 结果

执行测试后，将生成两个图表文件：
- `tcp_performance.png`：包含RTT、吞吐量和拥塞窗口变化图表
- `tcp_statistics.png`：包含拥塞控制阶段分析和未确认数据量图表 