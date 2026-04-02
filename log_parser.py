import re
from collections import Counter
from datetime import datetime


# Python logging format:
# 2024-01-01 12:00:00,123 ERROR module.name: message
# 2024-01-01 12:00:00,123 WARNING message
# 2024-01-01 12:00:00,123 INFO message

LOG_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,\.\d]*)"
    r"\s+(?P<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)"
    r"(?:\s+(?P<logger>[^\s:]+))?"
    r"\s*:?\s*(?P<message>.+)$",
    re.IGNORECASE,
)

LEVEL_MAP = {
    "WARN":     "WARNING",
    "FATAL":    "CRITICAL",
    "CRITICAL": "CRITICAL",
    "DEBUG":    "DEBUG",
    "INFO":     "INFO",
    "WARNING":  "WARNING",
    "ERROR":    "ERROR",
}

# Known error pattern signatures to group frequent issues
ERROR_SIGNATURES = [
    (re.compile(r"connection\s+(?:refused|timeout|reset|timed?\s*out)", re.I), "Connection error"),
    (re.compile(r"database\s+(?:error|exception|timeout|connection)", re.I),   "Database error"),
    (re.compile(r"(?:file|directory)\s+not\s+found",                  re.I),   "File not found"),
    (re.compile(r"permission\s+denied",                               re.I),   "Permission denied"),
    (re.compile(r"out\s+of\s+memory|memory\s+error",                  re.I),   "Out of memory"),
    (re.compile(r"null\s*pointer|none\s*type|attributeerror",         re.I),   "Null/attribute error"),
    (re.compile(r"authentication\s+(?:failed|error|invalid)",         re.I),   "Auth failure"),
    (re.compile(r"timeout",                                           re.I),   "Timeout"),
    (re.compile(r"ssl\s*(?:error|certificate|handshake)",             re.I),   "SSL error"),
    (re.compile(r"disk\s+(?:full|space|quota)",                       re.I),   "Disk space issue"),
]


def classify_message(message: str) -> str:
    for pattern, label in ERROR_SIGNATURES:
        if pattern.search(message):
            return label
    # Fall back to first 60 chars as the signature
    return message.strip()[:60]


def parse_logs(content: str) -> dict:
    lines = content.splitlines()
    total_lines = len(lines)

    counts = Counter({"INFO": 0, "WARNING": 0, "ERROR": 0, "DEBUG": 0, "CRITICAL": 0})
    error_lines   = []   # individual ERROR/CRITICAL entries
    warning_lines = []   # individual WARNING entries
    unparsed      = 0
    first_ts      = None
    last_ts       = None
    issue_counter = Counter()

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        m = LOG_PATTERN.match(line)
        if not m:
            unparsed += 1
            continue

        level    = LEVEL_MAP.get(m.group("level").upper(), m.group("level").upper())
        message  = m.group("message").strip()
        ts_raw   = m.group("timestamp")
        logger   = m.group("logger") or ""

        counts[level] += 1

        # Track time range
        try:
            ts = datetime.strptime(ts_raw.split(",")[0].split(".")[0], "%Y-%m-%d %H:%M:%S")
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts
        except ValueError:
            pass

        entry = {
            "timestamp": ts_raw,
            "level":     level,
            "logger":    logger,
            "message":   message,
        }

        if level in ("ERROR", "CRITICAL"):
            error_lines.append(entry)
            issue_counter[classify_message(message)] += 1

        elif level == "WARNING":
            warning_lines.append(entry)

    # Top 5 most frequent issues
    top_issues = [
        {"issue": issue, "count": cnt}
        for issue, cnt in issue_counter.most_common(5)
    ]

    # Most frequent single issue label
    most_frequent = issue_counter.most_common(1)
    most_frequent_issue = most_frequent[0][0] if most_frequent else None

    return {
        "total_lines":        total_lines,
        "parsed_lines":       total_lines - unparsed,
        "unparsed_lines":     unparsed,
        "counts": {
            "INFO":     counts["INFO"],
            "WARNING":  counts["WARNING"],
            "ERROR":    counts["ERROR"],
            "DEBUG":    counts["DEBUG"],
            "CRITICAL": counts["CRITICAL"],
        },
        "error_lines":        error_lines[:200],    # cap at 200 for response size
        "warning_lines":      warning_lines[:100],
        "top_issues":         top_issues,
        "most_frequent_issue": most_frequent_issue,
        "time_range": {
            "from": first_ts.isoformat() if first_ts else None,
            "to":   last_ts.isoformat()  if last_ts  else None,
        },
    }