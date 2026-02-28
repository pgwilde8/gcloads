const DEFAULT_API_BASE = "https://codriverfreight.com";
const DEFAULT_ENDPOINT = "/api/scout/ingest";

const getText = () => document.body?.innerText || "";

const detectSource = () => {
    const host = window.location.hostname.toLowerCase();
    if (host.includes("trucksmarter")) return "trucksmarter";
    if (host.includes("dat")) return "dat_one";
    if (host.includes("truckstop")) return "truckstop";
    return "other";
};

const extractViaRegex = (regex, text, fallback = "") => {
    const match = text.match(regex);
    return match ? (match[1] || match[0] || "").trim() : fallback;
};

const extractViaQuery = (selector, attr = null) => {
    const node = document.querySelector(selector);
    if (!node) return "";
    const value = attr ? node.getAttribute(attr) : node.textContent;
    return (value || "").trim();
};

// Normalise raw equipment strings to the canonical terms the backend expects.
const EQUIP_ALIASES = {
    "van": "Dry Van", "dry van": "Dry Van", "dryvan": "Dry Van", "dry": "Dry Van",
    "ftl": "Dry Van", "reefer": "Refrigerated", "refrigerated": "Refrigerated",
    "ref": "Refrigerated", "rf": "Refrigerated",
    "flatbed": "Flatbed", "flat bed": "Flatbed", "flat": "Flatbed", "fb": "Flatbed",
    "step deck": "Step Deck", "stepdeck": "Step Deck", "step": "Step Deck",
    "lowboy": "Lowboy", "power only": "Power Only", "power": "Power Only", "po": "Power Only",
    "hotshot": "Hotshot", "hot shot": "Hotshot",
    "tanker": "Tanker", "bulk": "Bulk", "conestoga": "Conestoga",
    "rgn": "RGN", "double drop": "Double Drop",
};

const normaliseEquipment = (raw) => {
    if (!raw) return "";
    const key = raw.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();
    return EQUIP_ALIASES[key] || raw.trim();
};

const extractEquipmentType = (text) => {
    // Try labelled field first ("Equipment: Flatbed", "Trailer Type: Reefer", etc.)
    const labelled = extractViaRegex(
        /(?:Equipment|Trailer\s*Type|Equip(?:ment)?\s*Type)\s*[:\-]?\s*\n?\s*([A-Za-z][A-Za-z0-9 \-\/]{1,30})/i,
        text, ""
    );
    if (labelled) return normaliseEquipment(labelled);

    // Fall back: scan for known equipment keywords anywhere in the text
    const keywords = [
        "Dry Van", "Reefer", "Refrigerated", "Flatbed", "Step Deck", "Power Only",
        "Hotshot", "Lowboy", "Tanker", "Conestoga", "RGN", "Double Drop", "Bulk",
    ];
    for (const kw of keywords) {
        if (new RegExp(`\\b${kw}\\b`, "i").test(text)) return kw;
    }
    return "";
};

const harvestLoadData = () => {
    const text = getText();

    const emailFromLink = extractViaQuery("a[href^='mailto:']", "href").replace(/^mailto:/i, "").split("?")[0];
    const phoneFromLink = extractViaQuery("a[href^='tel:']", "href").replace(/^tel:/i, "");

    // MC: accept 4–8 digits; handle "MC#", "MC #", "MC", "Motor Carrier #" prefixes
    const mc_number = extractViaRegex(
        /(?:Motor\s*Carrier\s*#?|MC\s*#?)\s*([0-9]{4,8})/i, text, ""
    );

    // Distance: extract miles if present for RPM computation on the backend
    const distance_miles = extractViaRegex(
        /(\d{3,4})\s*(?:mi(?:les?)?|mi\.)/i, text, ""
    );

    // Rate per mile: explicit $/mi label takes priority
    const rate_per_mile = extractViaRegex(
        /\$?\s*(\d+\.\d{1,2})\s*\/?\s*(?:mi(?:le)?|rpm)/i, text, ""
    );

    const equipment_type = extractEquipmentType(text);

    const payload = {
        load_id:        extractViaRegex(/Load\s*ID\s*\n?\s*([0-9-]+)/i, text, ""),
        mc_number,
        dot_number:     extractViaRegex(/DOT\s*#?\s*([0-9]{5,10})/i, text, ""),
        email:          emailFromLink || extractViaRegex(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/, text, ""),
        phone:          phoneFromLink || extractViaRegex(/(\+?1?\s*\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4})/, text, ""),
        price:          extractViaRegex(/(\$\s?\d{1,3}(?:,\d{3})*)/, text, "Check Notes"),
        origin:         extractViaRegex(/Origin\s*\n?\s*([^\n]+)/i, text, "Unknown"),
        destination:    extractViaRegex(/Destination\s*\n?\s*([^\n]+)/i, text, "Unknown"),
        equipment_type,
        source:         detectSource(),
        metadata: {
            ...(distance_miles  && { distance_miles:  parseFloat(distance_miles)  }),
            ...(rate_per_mile   && { rate_per_mile:   parseFloat(rate_per_mile)   }),
            ...(equipment_type  && { equipment_type                               }),
        },
    };

    payload.raw_notes = extractViaRegex(/((?:Email bids only|Phone calls only|Call to book|Must call|No emails)[^\n]*)/i, text, "");
    payload.contact_info = {
        email: payload.email,
        phone: payload.phone,
    };

    return payload;
};

const setButtonState = (btn, label, color) => {
    btn.innerText = label;
    btn.style.background = color;
};

const setTransientState = (btn, label, color, timeoutMs = 2500) => {
    setButtonState(btn, label, color);
    setTimeout(() => setButtonState(btn, "SHIP TO DISPATCH", "#10b981"), timeoutMs);
};

const setBlockedState = (btn, note) => {
    const message = note ? `🛑 BLOCKED: ${note}` : "🛑 BLOCKED: DO NOT BOOK";
    btn.innerText = message;
    btn.style.background = "#dc2626";
    btn.style.maxWidth = "340px";
    btn.style.whiteSpace = "normal";
    btn.style.lineHeight = "1.2";
};

const setStandingState = (btn, status, note) => {
    btn.style.maxWidth = "340px";
    btn.style.whiteSpace = "normal";
    btn.style.lineHeight = "1.2";

    if (status === "PREFERRED") {
        btn.innerText = note ? `🔵 PREFERRED: ${note}` : "🔵 PREFERRED BROKER";
        btn.style.background = "#2563eb";
        return true;
    }

    if (status === "NOTE") {
        btn.innerText = note ? `🟡 NOTE: ${note}` : "🟡 BROKER NOTE";
        btn.style.background = "#d97706";
        return true;
    }

    return false;
};

const getConfig = async () => {
    const config = await chrome.storage.local.get(["gcdApiBase", "gcdApiKey"]);
    return {
        gcdApiBase: config.gcdApiBase || DEFAULT_API_BASE,
        gcdApiKey: config.gcdApiKey || ""
    };
};

const injectButton = () => {
    if (!document.body || document.getElementById("gcd-scout-btn")) return;

    const btn = document.createElement("button");
    btn.id = "gcd-scout-btn";
    btn.innerText = "SHIP TO DISPATCH";
    btn.style = "position:fixed; top:20px; right:20px; z-index:999999; background:#10b981; color:white; padding:12px; border-radius:8px; font-weight:bold; cursor:pointer; border:none; box-shadow: 0 4px 6px rgba(0,0,0,0.1);";

    btn.onclick = async () => {
        const data = harvestLoadData();
        if (!data.load_id) {
            setButtonState(btn, "MISSING LOAD ID", "#ef4444");
            setTimeout(() => setButtonState(btn, "SHIP TO DISPATCH", "#10b981"), 2000);
            return;
        }

        setButtonState(btn, "SHIPPING...", "#3b82f6");

        try {
            const config = await getConfig();
            if (!config.gcdApiKey) {
                setButtonState(btn, "NO API KEY", "#ef4444");
                setTimeout(() => setButtonState(btn, "SHIP TO DISPATCH", "#10b981"), 2500);
                return;
            }

            const response = await fetch(`${config.gcdApiBase}${DEFAULT_ENDPOINT}`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "x-api-key": config.gcdApiKey
                },
                body: JSON.stringify(data)
            });

            if (response.ok) {
                const result = await response.json();
                const standing = result.standing || {};
                if (standing.status === "BLACKLISTED") {
                    setBlockedState(btn, standing.note || null);
                    return;
                }
                if (setStandingState(btn, standing.status, standing.note || null)) {
                    setTimeout(() => setButtonState(btn, "SHIP TO DISPATCH", "#10b981"), 3000);
                    return;
                }

                const nextStep = result.next_step || "";
                const missed = (result.missed || []).join(", ");
                const hasPhone = !!(result.broker_phone || "").trim();

                const buttonLabel = (() => {
                    switch (nextStep) {
                        case "AUTO_SENT":
                            return "✅ Auto-sent (perfect match)";
                        case "NEEDS_APPROVAL":
                            return missed ? `⚠️ Queued (missed ${missed})` : "⚠️ Queued (needs approval)";
                        case "SAVED_ONLY":
                            return missed ? `📋 Saved (missed ${missed})` : "📋 Saved";
                        case "CALL_REQUIRED":
                            return hasPhone ? "📞 Call broker" : "📞 Call required (no phone)";
                        case "MISSING_BROKER_EMAIL":
                            return hasPhone ? "📞 Call broker" : "🔧 Needs enrichment";
                        case "BROKER_BLOCKED":
                            setBlockedState(btn, standing.note || "Broker blocked");
                            return null;
                        case "SETUP_REQUIRED":
                            return "⚙️ Set up Scout first";
                        case "SCOUT_PAUSED":
                            return "⏸️ Scout paused";
                        default:
                            return "✅ Load secured";
                    }
                })();

                if (buttonLabel === null) return;

                const color = ["AUTO_SENT", "NEEDS_APPROVAL"].includes(nextStep)
                    ? (nextStep === "AUTO_SENT" ? "#059669" : "#b45309")
                    : ["CALL_REQUIRED", "MISSING_BROKER_EMAIL"].includes(nextStep)
                        ? (hasPhone ? "#2563eb" : "#d97706")
                        : "#059669";
                setTransientState(btn, buttonLabel, color, 3500);
            } else {
                setTransientState(btn, `ERROR ${response.status}`, "#ef4444", 3200);
            }
        } catch (error) {
            setTransientState(btn, "CONNECTION ERROR", "#ef4444", 3200);
        }
    };

    document.body.appendChild(btn);
};

const observer = new MutationObserver(() => injectButton());
if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true });
    injectButton();
} else {
    window.addEventListener("DOMContentLoaded", () => {
        observer.observe(document.body, { childList: true, subtree: true });
        injectButton();
    });
}
