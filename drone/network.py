"""Startup-only Wi-Fi setup/discovery. No flight commands or credential logging."""

from dataclasses import dataclass
import ipaddress
import re
import socket
import time

from . import config as cfg


class NetworkSetupError(Exception):
    pass


def validate_host(host):
    try:
        ip = ipaddress.IPv4Address(host.strip())
    except (ValueError, AttributeError):
        raise NetworkSetupError("Adresse IPv4 invalide pour le drone.") from None
    if ip.is_unspecified or ip.is_multicast or ip.is_loopback or int(ip) == 0xFFFFFFFF:
        raise NetworkSetupError("Indiquer une adresse IPv4 unicast du drone.")
    return str(ip)


@dataclass(frozen=True)
class ConnectionOptions:
    mode: str = "ap"
    host: str = cfg.TELLO_AP_HOST
    probe_video: bool = False

    def __post_init__(self):
        if self.mode not in ("ap", "sta"):
            raise NetworkSetupError("Choisir AP ou STA.")
        object.__setattr__(self, "host", validate_host(self.host))


def validate_credentials(ssid, password):
    # The SDK documents two space-delimited parameters, with no escaping rule.
    # Reject ambiguous commands rather than silently sending truncated secrets.
    try:
        ssid_size = len(ssid.encode("utf-8")) if isinstance(ssid, str) else 0
        password_size = len(password.encode("utf-8")) if isinstance(password, str) else 0
    except UnicodeEncodeError:
        raise NetworkSetupError("Identifiants Wi-Fi : caractères UTF-8 invalides.") from None
    if not 1 <= ssid_size <= 32:
        raise NetworkSetupError("Le SSID doit contenir entre 1 et 32 octets UTF-8.")
    if not 8 <= password_size <= 63:
        raise NetworkSetupError("Utiliser un mot de passe Wi-Fi de 8 à 63 octets.")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in ssid + password):
        raise NetworkSetupError("Espaces et caractères de contrôle non pris en charge dans SSID/mot de passe : "
                                "la commande SDK ne définit pas d'échappement.")


def _reply(sock, host, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sock.settimeout(max(0.01, deadline - time.monotonic()))
        data, address = sock.recvfrom(4096)
        if address == (host, 8889):
            return data.decode("utf-8", errors="replace").strip().lower()
    raise socket.timeout()


def provision_sta(ssid, password, ap_host=cfg.TELLO_AP_HOST, timeout=3):
    """Send ap exactly once using a dedicated socket, outside djitellopy logs.

    False means acknowledgement lost/absent, NOT that setup succeeded or failed.
    Call before constructing Tello (its global receiver owns UDP 8889).
    Errors are deliberately constant: devices can echo the credential command.
    """
    validate_credentials(ssid, password)
    host = validate_host(ap_host)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("", 8889))
            sock.sendto(b"command", (host, 8889))
            if _reply(sock, host, timeout) != "ok":
                raise NetworkSetupError("Le drone refuse le mode SDK. Configuration Wi-Fi non envoyée.")
            sock.sendto(f"ap {ssid} {password}".encode("utf-8"), (host, 8889))
            try:
                response = _reply(sock, host, timeout)
            except socket.timeout:
                return False
            if response == "ok" or response.startswith("ok,") or response.startswith("ok "):
                return True
            raise NetworkSetupError("Le drone a refusé la configuration STA. Vérifier le réseau et le modèle EDU.")
    except NetworkSetupError:
        raise
    except socket.timeout:
        raise NetworkSetupError("Aucune réponse du drone en AP. Rejoindre son Wi-Fi TELLO avant de configurer STA.") from None
    except OSError:
        raise NetworkSetupError("Configuration réseau impossible. Fermer les autres applications Tello "
                                "et vérifier l'accès UDP 8889 et le Wi-Fi AP.") from None


def suggested_subnet():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))  # routing lookup only, no datagram
            address = sock.getsockname()[0]
        return str(ipaddress.ip_network(address + "/24", strict=False))
    except (OSError, ValueError):
        return "192.168.1.0/24"


def discover_drones(cidr, timeout=4):
    """Explicit bounded LAN scan: command + sdk? only; no flight or Wi-Fi queries."""
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        raise NetworkSetupError("Sous-réseau invalide, exemple : 192.168.1.0/24.") from None
    if (network.version != 4 or not network.is_private or network.prefixlen < 24
            or network.is_loopback or network.is_link_local or network.is_multicast):
        raise NetworkSetupError("La découverte est limitée à un sous-réseau IPv4 privé de 256 adresses maximum.")
    found, pending, awaiting_sdk = {}, set(), set()
    hosts = iter(str(ip) for ip in network.hosts())
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("", 8889))
            sock.settimeout(0.05)
            deadline = time.monotonic() + timeout
            exhausted = False
            while time.monotonic() < deadline:
                if not exhausted:
                    for _ in range(16):
                        host = next(hosts, None)
                        if host is None:
                            exhausted = True
                            break
                        pending.add(host)
                        sock.sendto(b"command", (host, 8889))
                try:
                    data, (host, port) = sock.recvfrom(1024)
                except (socket.timeout, ConnectionResetError):
                    # Windows can report ICMP "port unreachable" for an ordinary
                    # LAN host here. It is not a failure of the whole discovery.
                    continue
                if port != 8889 or host not in pending:
                    continue
                response = data.decode("ascii", errors="replace").strip().lower()
                if response == "ok":
                    awaiting_sdk.add(host)
                    sock.sendto(b"sdk?", (host, 8889))
                elif host in awaiting_sdk and re.fullmatch(r"[23](?:\d{1,3}|\.\d+)", response):
                    found[host] = response
            return sorted(found.items(), key=lambda item: ipaddress.ip_address(item[0]))
    except OSError:
        raise NetworkSetupError("Découverte impossible : port UDP 8889 occupé ou réseau indisponible.") from None
