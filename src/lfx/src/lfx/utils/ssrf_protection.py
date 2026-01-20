"""模块名称：SSRF 防护校验

模块目的：阻止对内网/环回/云元数据等敏感地址的请求。
主要功能：
- 校验 URL scheme 与 hostname
- 解析 DNS 并检查 IP 是否落入阻断范围
- 允许通过白名单放行
使用场景：对外 URL 请求组件的安全校验。
关键组件：`validate_url_for_ssrf`、`is_ip_blocked`、`get_allowed_hosts`
设计背景：防止 SSRF 访问内部资源或元数据端点。
注意事项：当前默认 warn-only，下一主版本计划改为强制阻断；应禁用重定向以避免绕过。
"""

import functools
import ipaddress
import socket
from urllib.parse import urlparse

from lfx.logging import logger
from lfx.services.deps import get_settings_service


class SSRFProtectionError(ValueError):
    """URL 被 SSRF 防护阻断时抛出。"""


@functools.cache
def get_blocked_ip_ranges() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """获取阻断 IP 段列表（首次访问时惰性初始化）。

    性能：避免模块导入时创建大量 `ip_network` 对象。
    """
    return [
        # `IPv4` 网段
        ipaddress.ip_network("0.0.0.0/8"),  # 当前网络（仅作源地址有效）
        ipaddress.ip_network("10.0.0.0/8"),  # 私有网段（RFC 1918）
        ipaddress.ip_network("100.64.0.0/10"),  # `CGNAT`（RFC 6598）
        ipaddress.ip_network("127.0.0.0/8"),  # 回环
        ipaddress.ip_network("169.254.0.0/16"),  # 本地链路/AWS 元数据
        ipaddress.ip_network("172.16.0.0/12"),  # 私有网段（RFC 1918）
        ipaddress.ip_network("192.0.0.0/24"),  # `IETF` 协议分配
        ipaddress.ip_network("192.0.2.0/24"),  # 文档地址（TEST-NET-1）
        ipaddress.ip_network("192.168.0.0/16"),  # 私有网段（RFC 1918）
        ipaddress.ip_network("198.18.0.0/15"),  # 基准测试
        ipaddress.ip_network("198.51.100.0/24"),  # 文档地址（TEST-NET-2）
        ipaddress.ip_network("203.0.113.0/24"),  # 文档地址（TEST-NET-3）
        ipaddress.ip_network("224.0.0.0/4"),  # 组播
        ipaddress.ip_network("240.0.0.0/4"),  # 保留
        ipaddress.ip_network("255.255.255.255/32"),  # 广播
        # `IPv6` 网段
        ipaddress.ip_network("::1/128"),  # 回环
        ipaddress.ip_network("::/128"),  # 未指定地址
        ipaddress.ip_network("::ffff:0:0/96"),  # `IPv4` 映射 `IPv6`
        ipaddress.ip_network("100::/64"),  # 丢弃前缀
        ipaddress.ip_network("2001::/23"),  # `IETF` 协议分配
        ipaddress.ip_network("2001:db8::/32"),  # 文档地址
        ipaddress.ip_network("fc00::/7"),  # `ULA`
        ipaddress.ip_network("fe80::/10"),  # 本地链路
        ipaddress.ip_network("ff00::/8"),  # 组播
    ]


def is_ssrf_protection_enabled() -> bool:
    """从配置读取 SSRF 防护开关。"""
    return get_settings_service().settings.ssrf_protection_enabled


def get_allowed_hosts() -> list[str]:
    """获取允许的主机名/CIDR 白名单列表。"""
    allowed_hosts = get_settings_service().settings.ssrf_allowed_hosts
    if not allowed_hosts:
        return []
    # 注意：配置已是 list[str]，这里只做清洗。
    return [host.strip() for host in allowed_hosts if host and host.strip()]


def is_host_allowed(hostname: str, ip: str | None = None) -> bool:
    """判断主机名或 IP 是否在白名单中。"""
    allowed_hosts = get_allowed_hosts()
    if not allowed_hosts:
        return False

    # 主机名精确匹配
    if hostname in allowed_hosts:
        return True

    # 通配符域名匹配（*.example.com）
    for allowed in allowed_hosts:
        if allowed.startswith("*."):
            domain_suffix = allowed[1:]  # 去掉 '*'
            if hostname.endswith(domain_suffix) or hostname == domain_suffix[1:]:
                return True

    # 如果提供 `IP`，则尝试 `CIDR` 或精确 `IP` 匹配
    if ip:
        try:
            ip_obj = ipaddress.ip_address(ip)

            # 精确 IP
            if ip in allowed_hosts:
                return True

            # `CIDR` 范围
            for allowed in allowed_hosts:
                try:
                    if "/" in allowed:
                        network = ipaddress.ip_network(allowed, strict=False)
                        if ip_obj in network:
                            return True
                except (ValueError, ipaddress.AddressValueError):
                    continue

        except (ValueError, ipaddress.AddressValueError):
            pass

    return False


def is_ip_blocked(ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """判断 IP 是否落入阻断网段。"""
    try:
        ip_obj = ipaddress.ip_address(ip) if isinstance(ip, str) else ip

        # 与所有阻断网段做包含判断
        return any(ip_obj in blocked_range for blocked_range in get_blocked_ip_ranges())
    except (ValueError, ipaddress.AddressValueError):
        # 安全：无法解析的 IP 视为阻断
        return True


def resolve_hostname(hostname: str) -> list[str]:
    """解析主机名到 IP 列表。"""
    try:
        # 同时获取 IPv4/IPv6
        addr_info = socket.getaddrinfo(hostname, None)

        # 去重后的 IP 列表
        ips = []
        for info in addr_info:
            ip = info[4][0]
            # 去除 IPv6 zone id
            if "%" in ip:
                ip = ip.split("%")[0]
            if ip not in ips:
                ips.append(ip)

        if not ips:
            msg = f"Unable to resolve hostname: {hostname}"
            raise SSRFProtectionError(msg)
    except socket.gaierror as e:
        msg = f"DNS resolution failed for {hostname}: {e}"
        raise SSRFProtectionError(msg) from e
    except Exception as e:
        msg = f"Error resolving hostname {hostname}: {e}"
        raise SSRFProtectionError(msg) from e

    return ips


def _validate_url_scheme(scheme: str) -> None:
    """校验 URL scheme 仅允许 http/https。"""
    if scheme not in ("http", "https"):
        msg = f"Invalid URL scheme '{scheme}'. Only http and https are allowed."
        raise SSRFProtectionError(msg)


def _validate_hostname_exists(hostname: str | None) -> str:
    """校验 URL 中必须包含 hostname。"""
    if not hostname:
        msg = "URL must contain a valid hostname"
        raise SSRFProtectionError(msg)
    return hostname


def _validate_direct_ip_address(hostname: str) -> bool:
    """校验 URL 中的直连 IP 是否允许。"""
    try:
        ip_obj = ipaddress.ip_address(hostname)
    except ValueError:
        # 非 IP：交由 DNS 解析流程
        return False

    # 直连 IP：先检查白名单
    if is_host_allowed(hostname, str(ip_obj)):
        logger.debug("IP address %s is in allowlist, bypassing SSRF checks", hostname)
        return True

    if is_ip_blocked(ip_obj):
        msg = (
            f"Access to IP address {hostname} is blocked by SSRF protection. "
            "Requests to private/internal IP ranges are not allowed for security reasons. "
            "To allow this IP, add it to LANGFLOW_SSRF_ALLOWED_HOSTS environment variable."
        )
        raise SSRFProtectionError(msg)

    # 直连公网 IP 允许访问
    return True


def _validate_hostname_resolution(hostname: str) -> None:
    """解析主机名并校验解析 IP 是否被阻断。"""
    # 先解析 DNS
    try:
        resolved_ips = resolve_hostname(hostname)
    except SSRFProtectionError:
        raise
    except Exception as e:
        msg = f"Failed to resolve hostname {hostname}: {e}"
        raise SSRFProtectionError(msg) from e

    # 检查解析出的 IP 是否在阻断范围
    blocked_ips = []
    for ip in resolved_ips:
        # `IP` 级别白名单优先
        if is_host_allowed(hostname, ip):
            logger.debug("Resolved IP %s for hostname %s is in allowlist, bypassing SSRF checks", ip, hostname)
            return

        if is_ip_blocked(ip):
            blocked_ips.append(ip)

    if blocked_ips:
        msg = (
            f"Hostname {hostname} resolves to blocked IP address(es): {', '.join(blocked_ips)}. "
            "Requests to private/internal IP ranges are not allowed for security reasons. "
            "This protection prevents access to internal services, cloud metadata endpoints "
            "(e.g., AWS 169.254.169.254), and other sensitive internal resources. "
            "To allow this hostname, add it to LANGFLOW_SSRF_ALLOWED_HOSTS environment variable."
        )
        raise SSRFProtectionError(msg)


def validate_url_for_ssrf(url: str, *, warn_only: bool = True) -> None:
    """对 URL 执行 SSRF 防护校验。

    关键路径：
    1) 校验 scheme/hostname
    2) 白名单命中则直接放行
    3) 直连 IP 或 DNS 解析后进行阻断判断

    契约：`warn_only=True` 仅记录告警；为 False 时阻断并抛错。
    """
    # `SSRF` 关闭时直接放行
    if not is_ssrf_protection_enabled():
        return

    # 解析 URL
    try:
        parsed = urlparse(url)
    except Exception as e:
        msg = f"Invalid URL format: {e}"
        raise ValueError(msg) from e

    try:
        # 校验 scheme
        _validate_url_scheme(parsed.scheme)
        if parsed.scheme not in ("http", "https"):
            return

        # 校验 hostname 存在
        hostname = _validate_hostname_exists(parsed.hostname)

        # 白名单命中则直接放行
        if is_host_allowed(hostname):
            logger.debug("Hostname %s is in allowlist, bypassing SSRF checks", hostname)
            return

        # 校验直连 IP 或进行 DNS 解析
        is_direct_ip = _validate_direct_ip_address(hostname)
        if is_direct_ip:
            # 直连 IP 已处理完毕
            return

        # 主机名解析并校验
        _validate_hostname_resolution(hostname)
    except SSRFProtectionError as e:
        if warn_only:
            logger.warning("SSRF Protection Warning: %s [URL: %s]", str(e), url)
            logger.warning(
                "This request will be blocked when SSRF protection is enforced in the next major version. "
                "Please review your API Request components."
            )
            return
        raise
