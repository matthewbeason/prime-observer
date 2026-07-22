GATEWAY_HOST = "192.168.1.1"

TARGET_METADATA = {
    GATEWAY_HOST: {
        "label": "Gateway",
        "class": "gateway_probe",
    },
    "1.1.1.1": {
        "label": "Cloudflare",
        "class": "internet_probe",
    },
    "9.9.9.9": {
        "label": "Quad9",
        "class": "internet_probe",
    },
    "45.90.28.134": {
        "label": "NextDNS primary",
        "class": "resolver_probe",
        "dependency_group_id": "resolver_pair_home",
        "dependency_type": "dns_resolver_pair",
        "member_id": "nextdns_primary",
        "role": "primary",
        "provider": "NextDNS",
        "endpoint": "45.90.28.134",
    },
    "45.90.30.134": {
        "label": "NextDNS secondary",
        "class": "resolver_probe",
        "dependency_group_id": "resolver_pair_home",
        "dependency_type": "dns_resolver_pair",
        "member_id": "nextdns_secondary",
        "role": "secondary",
        "provider": "NextDNS",
        "endpoint": "45.90.30.134",
    },
}

TARGETS = list(TARGET_METADATA)
INTERNET_PROBE_HOSTS = {
    host for host, meta in TARGET_METADATA.items() if meta["class"] == "internet_probe"
}
RESOLVER_PROBE_HOSTS = {
    host for host, meta in TARGET_METADATA.items() if meta["class"] == "resolver_probe"
}
WAN_HOSTS = INTERNET_PROBE_HOSTS | RESOLVER_PROBE_HOSTS


def target_metadata(host):
    clean_host = (host or "").strip()
    meta = TARGET_METADATA.get(clean_host)
    if meta:
        payload = {
            "target_label": meta["label"],
            "target_class": meta["class"],
        }
        for key in ("dependency_group_id", "dependency_type", "member_id", "role", "provider", "endpoint"):
            if key in meta:
                payload[key] = meta[key]
        return payload
    if clean_host in WAN_HOSTS:
        return {
            "target_label": clean_host,
            "target_class": "internet_probe",
        }
    return {
        "target_label": clean_host,
        "target_class": "unknown_probe",
    }


def target_class(host):
    return target_metadata(host)["target_class"]


def target_label(host):
    return target_metadata(host)["target_label"]


def is_gateway_probe(host):
    return target_class(host) == "gateway_probe"


def is_wan_probe(host):
    return target_class(host) in {"internet_probe", "resolver_probe"}
