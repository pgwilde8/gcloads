def _root_prefix() -> str:
    return "greencandle"


def packet_doc_key(driver_id: int, doc_type: str) -> str:
    normalized = (doc_type or "").strip().lower()
    filename_map = {
        "w9": "w9.pdf",
        "coi": "coi.pdf",
        "insurance": "coi.pdf",
        "mc_auth": "mc_auth.pdf",
        "authority": "mc_auth.pdf",
        "w9.pdf": "w9.pdf",
        "coi.pdf": "coi.pdf",
        "mc_auth.pdf": "mc_auth.pdf",
    }
    filename = filename_map.get(normalized)
    if not filename:
        filename = normalized if normalized.endswith(".pdf") else f"{normalized}.pdf"
    return f"{_root_prefix()}/drivers/{driver_id}/packet/{filename}"


def negotiation_doc_key(driver_id: int, negotiation_id: int, doc_type: str) -> str:
    normalized = (doc_type or "").strip().lower()
    key_map = {
        "bol_raw": "bol/raw.pdf",
        "bol_pdf": "bol/packet.pdf",
        "bol_packet": "bol/packet.pdf",
        "ratecon": "ratecon.pdf",
        "negotiation_packet": "packet/packet.pdf",
        "factor_packet": "packet/packet.pdf",
    }
    relative_key = key_map.get(normalized)
    if not relative_key:
        raise ValueError(f"unsupported_negotiation_doc_type:{doc_type}")
    return f"{_root_prefix()}/drivers/{driver_id}/negotiations/{negotiation_id}/{relative_key}"


def driver_packet_key(driver_id: int, filename: str) -> str:
    return packet_doc_key(driver_id, filename)


def driver_packet_prefix(driver_id: int) -> str:
    return f"{_root_prefix()}/drivers/{driver_id}/packet/"


def driver_space_marker_key(driver_id: int) -> str:
    return f"{_root_prefix()}/drivers/{driver_id}/.init"


def negotiation_doc_root(driver_id: int, negotiation_id: int) -> str:
    return f"{_root_prefix()}/drivers/{driver_id}/negotiations/{negotiation_id}"


def bol_raw_key(driver_id: int, negotiation_id: int, filename: str | None = None) -> str:
    return negotiation_doc_key(driver_id, negotiation_id, "bol_raw")


def bol_processed_key(driver_id: int, negotiation_id: int, filename: str = "bol.pdf") -> str:
    return negotiation_doc_key(driver_id, negotiation_id, "bol_pdf")


def ratecon_key(driver_id: int, negotiation_id: int, filename: str | None = None) -> str:
    return negotiation_doc_key(driver_id, negotiation_id, "ratecon")


def negotiation_packet_key(driver_id: int, negotiation_id: int, filename: str | None = None) -> str:
    return negotiation_doc_key(driver_id, negotiation_id, "negotiation_packet")


def factoring_packet_key(driver_id: int, negotiation_id: int, filename: str = "factoring_packet.pdf") -> str:
    return f"{negotiation_doc_root(driver_id, negotiation_id)}/packets/factoring/{filename}"
