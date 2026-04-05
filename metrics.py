import psutil
from database import get_connection

METRICS_RETENTION = 2880  # 24 hours at 30s intervals


def collect_metrics() -> dict:
    """Collect current system metrics using psutil."""

    # CPU
    cpu_percent = psutil.cpu_percent(interval=1)

    # Memory
    mem = psutil.virtual_memory()
    memory_percent   = mem.percent
    memory_used_mb   = round(mem.used / 1024 / 1024, 1)
    memory_total_mb  = round(mem.total / 1024 / 1024, 1)

    # Disk (root partition)
    disk = psutil.disk_usage("/")
    disk_percent  = disk.percent
    disk_used_gb  = round(disk.used / 1024 / 1024 / 1024, 2)
    disk_total_gb = round(disk.total / 1024 / 1024 / 1024, 2)

    # Network I/O
    net = psutil.net_io_counters()
    net_bytes_sent_mb  = round(net.bytes_sent / 1024 / 1024, 2)
    net_bytes_recv_mb  = round(net.bytes_recv / 1024 / 1024, 2)
    net_packets_sent   = net.packets_sent
    net_packets_recv   = net.packets_recv

    return {
        "cpu_percent":       cpu_percent,
        "memory_percent":    memory_percent,
        "memory_used_mb":    memory_used_mb,
        "memory_total_mb":   memory_total_mb,
        "disk_percent":      disk_percent,
        "disk_used_gb":      disk_used_gb,
        "disk_total_gb":     disk_total_gb,
        "net_bytes_sent_mb": net_bytes_sent_mb,
        "net_bytes_recv_mb": net_bytes_recv_mb,
        "net_packets_sent":  net_packets_sent,
        "net_packets_recv":  net_packets_recv,
    }


def save_metrics(m: dict):
    """Save a metrics snapshot and trim old records."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO system_metrics (
            cpu_percent, memory_percent, memory_used_mb, memory_total_mb,
            disk_percent, disk_used_gb, disk_total_gb,
            net_bytes_sent_mb, net_bytes_recv_mb,
            net_packets_sent, net_packets_recv
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            m["cpu_percent"],
            m["memory_percent"],
            m["memory_used_mb"],
            m["memory_total_mb"],
            m["disk_percent"],
            m["disk_used_gb"],
            m["disk_total_gb"],
            m["net_bytes_sent_mb"],
            m["net_bytes_recv_mb"],
            m["net_packets_sent"],
            m["net_packets_recv"],
        ),
    )
    # Retention: keep only last 24 hours of data
    conn.execute(
        """
        DELETE FROM system_metrics
        WHERE id NOT IN (
            SELECT id FROM system_metrics
            ORDER BY id DESC LIMIT ?
        )
        """,
        (METRICS_RETENTION,),
    )
    conn.commit()
    conn.close()


def run_metrics_collection():
    """Called by the scheduler every 30 seconds."""
    m = collect_metrics()
    save_metrics(m)
    from automation import run_automation_rules
    run_automation_rules(m)