def _root_prefix() -> str:
    return "greencandle"


def driver_packet_key(driver_id: int, filename: str) -> str:
    return f"{_root_prefix()}/drivers/{driver_id}/packet/{filename}"


def driver_packet_prefix(driver_id: int) -> str:
    return f"{_root_prefix()}/drivers/{driver_id}/packet/"


def driver_space_marker_key(driver_id: int) -> str:
    return f"{_root_prefix()}/drivers/{driver_id}/.init"


def negotiation_doc_root(driver_id: int, negotiation_id: int) -> str:
    return f"{_root_prefix()}/drivers/{driver_id}/negotiations/{negotiation_id}"


def bol_raw_key(driver_id: int, negotiation_id: int, filename: str) -> str:
    return f"{negotiation_doc_root(driver_id, negotiation_id)}/paperwork/bol/raw/{filename}"


def bol_processed_key(driver_id: int, negotiation_id: int, filename: str = "bol.pdf") -> str:
    return f"{negotiation_doc_root(driver_id, negotiation_id)}/paperwork/bol/processed/{filename}"


def ratecon_key(driver_id: int, negotiation_id: int, filename: str) -> str:
    return f"{negotiation_doc_root(driver_id, negotiation_id)}/paperwork/ratecon/{filename}"


def factoring_packet_key(driver_id: int, negotiation_id: int, filename: str = "factoring_packet.pdf") -> str:
    return f"{negotiation_doc_root(driver_id, negotiation_id)}/packets/factoring/{filename}"
