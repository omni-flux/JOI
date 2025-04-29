import platform
import psutil
import socket
import datetime
import time
import subprocess
from typing import Dict

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

        disk_info = []
        for partition in psutil.disk_partitions():
            if 'cdrom' in partition.opts or partition.fstype == '':
                 continue
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
            except (PermissionError, FileNotFoundError):
                continue

        # Top processes by CPU usage
        processes = []
        for proc in sorted(psutil.process_iter(['pid', 'name', 'cpu_percent']),
                           key=lambda p: p.info.get('cpu_percent') or 0, reverse=True)[:5]: # Use .get() for safety
            try:
                if proc.info['pid'] is not None and proc.info['name'] is not None:
                     processes.append({
                         "pid": proc.info['pid'],
                         "name": proc.info['name'],
                         "cpu": f"{proc.info.get('cpu_percent', 0):.1f}%" # Format CPU %
                     })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
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
        if disk_info:
            for disk in disk_info:
                 output.append(f"Drive {disk['mountpoint']} ({disk['device']}): {disk['used']} used of {disk['total']} ({disk['percent']})")
        else:
            output.append("No accessible disk partitions found.")


        output.append("\n=== TOP PROCESSES (by CPU) ===")
        if processes:
            for proc in processes:
                output.append(f"PID {proc['pid']}: {proc['name']} - CPU: {proc['cpu']}")
        else:
            output.append("No process information available.")


        return "\n".join(output)

    except Exception as e:
        return f"Error retrieving basic system information: {str(e)}"


def get_temperature_info() -> str:
    """Get temperature information for Windows systems (best effort)."""
    # WMI Method (Keep as is)
    if platform.system() == "Windows":
        try:
            ps_command = "powershell \"try { Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi | Select-Object -ExpandProperty CurrentTemperature } catch { Write-Error $_ }\""
            result = subprocess.run(ps_command, capture_output=True, text=True, check=False, shell=True) # Added shell=True for safety

            if result.returncode == 0 and result.stdout and result.stdout.strip() != "":
                try:
                    temp_kelvin = float(result.stdout.strip())
                    temp_celsius = (temp_kelvin / 10.0) - 273.15
                    return f"{temp_celsius:.1f}°C (WMI)"
                except (ValueError, TypeError):
                    pass

        except Exception as e:
             print(f"Error running WMI temperature query: {e}")


    # Fallback or other OS - attempt psutil sensors if available
    if hasattr(psutil, "sensors_temperatures"):
        try:
            temps = psutil.sensors_temperatures()
            # Look for common CPU thermal zones
            for name, entries in temps.items():
                if 'coretemp' in name.lower() or 'cpu' in name.lower() or 'k10temp' in name.lower() or 'zenpower' in name.lower():
                    for entry in entries:
                        return f"{entry.current:.1f}°C (Sensor: {name})"
        except Exception as e:
             print(f"Error reading psutil sensors: {e}")
    return "Not available"


def get_network_info() -> str:
    """Get detailed network information."""
    try:
        interfaces = []
        all_addrs = psutil.net_if_addrs()
        for name, addrs in all_addrs.items():
            iface_info = {"name": name, "ipv4": [], "ipv6": []}
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    iface_info["ipv4"].append({
                        "ip": addr.address,
                        "netmask": addr.netmask,
                        "broadcast": getattr(addr, 'broadcast', None)
                    })
                elif addr.family == socket.AF_INET6:
                    ip6 = addr.address
                    if '%' in ip6:
                         ip6 = ip6.split('%', 1)[0]
                    iface_info["ipv6"].append({"ip": ip6})

            if iface_info["ipv4"] or iface_info["ipv6"]:
                interfaces.append(iface_info)


        # Network stats (Keep as is)
        net_io = psutil.net_io_counters()
        net_stats = {
            "bytes_sent": format_bytes(net_io.bytes_sent),
            "bytes_recv": format_bytes(net_io.bytes_recv),
            "packets_sent": f"{net_io.packets_sent:,}",
            "packets_recv": f"{net_io.packets_recv:,}"
        }

        connections = []
        try:
             net_conn_list = psutil.net_connections(kind='tcp4')
        except PermissionError:
             net_conn_list = []
             connections.append({"error": "Permission denied accessing network connections."})

        # Filter and limit connections
        established_connections = [
            conn for conn in net_conn_list if conn.status == psutil.CONN_ESTABLISHED and conn.raddr
        ]
        for conn in established_connections[:5]:
             try:
                 proc_name = "N/A"
                 if conn.pid:
                     try:
                         proc = psutil.Process(conn.pid)
                         proc_name = proc.name()
                     except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                         proc_name = f"(PID: {conn.pid})"
                 connections.append({
                     "local": f"{conn.laddr.ip}:{conn.laddr.port}",
                     "remote": f"{conn.raddr.ip}:{conn.raddr.port}",
                     "process": proc_name
                 })
             except Exception:
                 continue

        output = []
        output.append("=== NETWORK INTERFACES ===")
        if interfaces:
             for iface in interfaces:
                 output.append(f"Interface: {iface['name']}")
                 for ip4 in iface['ipv4']:
                     output.append(f"  IPv4: {ip4['ip']} (Mask: {ip4['netmask']})")
                     # Output Broadcast only if it exists and is different from IP
                     if ip4['broadcast'] and ip4['broadcast'] != ip4['ip']:
                         output.append(f"        Broadcast: {ip4['broadcast']}")
                 for ip6 in iface['ipv6']:
                     output.append(f"  IPv6: {ip6['ip']}")
        else:
            output.append("No network interfaces with IP addresses found.")
        output.append("\n=== NETWORK STATISTICS ===")
        output.append(f"Bytes Sent: {net_stats['bytes_sent']}")
        output.append(f"Bytes Received: {net_stats['bytes_recv']}")
        output.append(f"Packets Sent: {net_stats['packets_sent']}")
        output.append(f"Packets Received: {net_stats['packets_recv']}")

        output.append("\n=== ACTIVE TCP CONNECTIONS (Max 5) ===")
        if connections:
            if "error" in connections[0]:
                 output.append(connections[0]["error"])
            else:
                for conn in connections:
                    output.append(f"{conn['local']} <--> {conn['remote']} (Process: {conn['process']})")
        elif not net_conn_list and "error" not in connections:
            output.append("No active established TCP connections found.")
        elif not connections and net_conn_list is not None:
            output.append("Could not retrieve active connections (Permissions?).")
        return "\n".join(output)
    except Exception as e:
        return f"Error retrieving network information: {str(e)}"

def get_uptime() -> str:
    """Get system uptime in a human-readable format."""
    try:
        uptime_seconds = time.time() - psutil.boot_time()
    except Exception:
        return "N/A"
    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days >= 1:
        parts.append(f"{int(days)}d")
    if hours >= 1 or days >=1:
        parts.append(f"{int(hours)}h")
    if minutes >= 1 or hours >= 1 or days >= 1:
        parts.append(f"{int(minutes)}m")
    if not parts:
         parts.append(f"{int(seconds)}s")

    return " ".join(parts) if parts else "0s"

def format_bytes(bytes_value: int) -> str:
    """Format bytes to human-readable format (IEC standard)."""
    if bytes_value < 0: return "N/A"
    if bytes_value == 0: return "0 B"
    power = 1024
    n = 0
    power_labels = {0 : 'B', 1: 'KiB', 2: 'MiB', 3: 'GiB', 4: 'TiB', 5: 'PiB'}
    while bytes_value >= power and n < len(power_labels) -1 :
        bytes_value /= power
        n += 1
    precision = 1 if n > 0 else 0
    return f"{bytes_value:.{precision}f} {power_labels[n]}"


def system_info(args: Dict[str, str]) -> str:
    """
    Gets system information based on the 'param' key in the args dictionary.

    Args:
        args (Dict[str, str]): A dictionary containing the arguments.
                               Expected key: 'param' with value 'basic' or 'network'.
                               Defaults to 'basic' if 'param' is missing or invalid.

    Returns:
        str: Formatted system information ('basic' or 'network').
    """
    param = args.get("param", "basic").strip().lower()

    if param == "network":
        return get_network_info()
    elif param == "basic":
         return get_basic_info()
    else:
         return f"Error: Invalid parameter '{args.get('param')}' for system_info. Use 'basic' or 'network'."
