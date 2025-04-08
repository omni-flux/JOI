import platform
import psutil
import socket
import datetime
import time
import subprocess


def get_basic_info() -> str:
    """Get basic system information including OS, CPU, memory, and disk usage."""
    try:
        # System info
        system_info = {
            "os": f"{platform.system()} {platform.release()} ({platform.version()})",
            "hostname": socket.gethostname(),
            "uptime": get_uptime(),
            "datetime": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        # CPU info with temperature
        cpu_info = {
            "cpu": platform.processor(),
            "cores": psutil.cpu_count(logical=False),
            "threads": psutil.cpu_count(logical=True),
            "usage": f"{psutil.cpu_percent()}%",
            "temperature": get_temperature_info()
        }

        # Memory info
        mem = psutil.virtual_memory()
        memory_info = {
            "total": format_bytes(mem.total),
            "available": format_bytes(mem.available),
            "used": format_bytes(mem.used),
            "percent": f"{mem.percent}%"
        }

        # Disk info
        disk_info = []
        for partition in psutil.disk_partitions():
            if platform.system() == "Windows" and "cdrom" in partition.opts:
                continue  # Skip CD/DVD drives
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                disk_info.append({
                    "device": partition.device,
                    "mountpoint": partition.mountpoint,
                    "total": format_bytes(usage.total),
                    "used": format_bytes(usage.used),
                    "free": format_bytes(usage.free),
                    "percent": f"{usage.percent}%"
                })
            except PermissionError:
                continue

        # Top processes by CPU usage
        processes = []
        for proc in sorted(psutil.process_iter(['pid', 'name', 'cpu_percent']),
                           key=lambda p: p.info['cpu_percent'] or 0, reverse=True)[:5]:
            try:
                processes.append({
                    "pid": proc.info['pid'],
                    "name": proc.info['name'],
                    "cpu": f"{proc.info['cpu_percent']}%"
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Format the output
        output = []
        output.append("=== SYSTEM INFO ===")
        output.append(f"OS: {system_info['os']}")
        output.append(f"Hostname: {system_info['hostname']}")
        output.append(f"Uptime: {system_info['uptime']}")
        output.append(f"Date/Time: {system_info['datetime']}")

        output.append("\n=== CPU INFO ===")
        output.append(f"Processor: {cpu_info['cpu']}")
        output.append(f"Cores: {cpu_info['cores']} physical, {cpu_info['threads']} logical")
        output.append(f"Usage: {cpu_info['usage']}")
        output.append(f"Temperature: {cpu_info['temperature']}")

        output.append("\n=== MEMORY INFO ===")
        output.append(f"Total: {memory_info['total']}")
        output.append(f"Used: {memory_info['used']} ({memory_info['percent']})")
        output.append(f"Available: {memory_info['available']}")

        output.append("\n=== DISK INFO ===")
        for disk in disk_info:
            output.append(f"Drive {disk['mountpoint']}: {disk['used']} used of {disk['total']} ({disk['percent']})")

        output.append("\n=== TOP PROCESSES ===")
        for proc in processes:
            output.append(f"PID {proc['pid']}: {proc['name']} - CPU: {proc['cpu']}")

        return "\n".join(output)

    except Exception as e:
        return f"Error retrieving system information: {str(e)}"


def get_temperature_info() -> str:
    """Get temperature information for Windows systems."""
    try:
        # Use WMI via PowerShell - most reliable method on Windows
        ps_command = "powershell \"Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi | Select-Object -ExpandProperty CurrentTemperature\""
        result = subprocess.run(ps_command, capture_output=True, text=True, check=False)

        if result.returncode == 0 and result.stdout.strip():
            try:
                temp_kelvin = float(result.stdout.strip())
                temp_celsius = (temp_kelvin / 10.0) - 273.15
                return f"{temp_celsius:.1f}°C"
            except (ValueError, TypeError):
                pass

        # If WMI method fails, use a simple estimation based on CPU usage
        cpu_usage = psutil.cpu_percent(interval=1)
        estimated_temp = 40 + (cpu_usage * 0.5)
        return f"~{estimated_temp:.1f}°C (estimated)"

    except Exception:
        return "Not available"


def get_network_info() -> str:
    """Get detailed network information."""
    try:
        # Network interfaces
        interfaces = []
        for name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET:  # IPv4
                    interfaces.append({
                        "name": name,
                        "ip": addr.address,
                        "netmask": addr.netmask,
                        "broadcast": getattr(addr, 'broadcast', None)
                    })

        # Network stats
        net_io = psutil.net_io_counters()
        net_stats = {
            "bytes_sent": format_bytes(net_io.bytes_sent),
            "bytes_recv": format_bytes(net_io.bytes_recv),
            "packets_sent": net_io.packets_sent,
            "packets_recv": net_io.packets_recv
        }

        # Active connections
        connections = []
        for conn in psutil.net_connections()[:5]:  # Limit to 5 connections
            if conn.status == 'ESTABLISHED':
                try:
                    connections.append({
                        "local": f"{conn.laddr.ip}:{conn.laddr.port}",
                        "remote": f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "None",
                        "status": conn.status,
                        "pid": conn.pid
                    })
                except (AttributeError, IndexError):
                    continue

        # Format the output
        output = []
        output.append("=== NETWORK INTERFACES ===")
        for iface in interfaces:
            output.append(f"Interface: {iface['name']}")
            output.append(f"  IP Address: {iface['ip']}")
            output.append(f"  Netmask: {iface['netmask']}")
            if iface['broadcast']:
                output.append(f"  Broadcast: {iface['broadcast']}")

        output.append("\n=== NETWORK STATISTICS ===")
        output.append(f"Bytes Sent: {net_stats['bytes_sent']}")
        output.append(f"Bytes Received: {net_stats['bytes_recv']}")
        output.append(f"Packets Sent: {net_stats['packets_sent']}")
        output.append(f"Packets Received: {net_stats['packets_recv']}")

        output.append("\n=== ACTIVE CONNECTIONS ===")
        if connections:
            for conn in connections:
                proc_name = "Unknown"
                if conn['pid']:
                    try:
                        proc_name = psutil.Process(conn['pid']).name()
                    except psutil.NoSuchProcess:
                        pass
                output.append(
                    f"Local: {conn['local']} → Remote: {conn['remote']} ({conn['status']}) - Process: {proc_name}")
        else:
            output.append("No active connections found")

        return "\n".join(output)

    except Exception as e:
        return f"Error retrieving network information: {str(e)}"


def get_uptime() -> str:
    """Get system uptime in a human-readable format."""
    uptime_seconds = time.time() - psutil.boot_time()

    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    if days > 0:
        return f"{int(days)}d {int(hours)}h {int(minutes)}m"
    elif hours > 0:
        return f"{int(hours)}h {int(minutes)}m"
    else:
        return f"{int(minutes)}m {int(seconds)}s"


def format_bytes(bytes_value: int) -> str:
    """Format bytes to human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024
    return f"{bytes_value:.2f} PB"


def system_info(param: str = "basic") -> str:
    """
    Get system information based on the parameter.

    Parameters:
        param (str): 'basic' for system, CPU, memory, disk info
                    'network' for network interfaces and connections

    Returns:
        str: Formatted system information
    """
    param = param.strip().lower()

    if param == "network":
        return get_network_info()
    else:  # Default to basic
        return get_basic_info()