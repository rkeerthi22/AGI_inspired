"""Egress Policy Governance Module & CLI (Phase 1).

Implements the operator-gated 'propose-and-confirm' policy expansion workflow:
- Reads candidate domains from runs/policy_expansion_candidates.jsonl.
- Performs RFC 1035 / RFC 1123 syntax validation and IP rejection.
- Performs DNS liveness and SSRF / private IP detection.
- Generates safety proposals with risk heuristics.
- Atomically appends approved domains to config/egress_policy.yaml.
- Re-signs the Ed25519 attestation token via scripts/enforce_worker_firewall.ps1.
- Records all approvals and rejections in runs/policy_approvals.audit.jsonl.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import threading
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = ROOT / "config" / "egress_policy.yaml"
DEFAULT_CANDIDATES_PATH = ROOT / "runs" / "policy_expansion_candidates.jsonl"
DEFAULT_APPROVALS_PATH = ROOT / "runs" / "policy_approvals.audit.jsonl"
DEFAULT_ATTEST_SCRIPT = ROOT / "scripts" / "enforce_worker_firewall.ps1"

# Known dynamic DNS and tunneling domains to flag
DYNAMIC_DNS_DOMAINS = frozenset([
    "duckdns.org",
    "ngrok.io",
    "ngrok-free.app",
    "nip.io",
    "sslip.io",
    "localho.st",
    "no-ip.com",
    "zapto.org",
    "sytes.net",
])

# High-risk top level domains frequently associated with phishing / malware
HIGH_RISK_TLDS = frozenset([
    "zip",
    "mov",
    "country",
    "kim",
    "science",
    "gq",
    "work",
    "date",
    "racing",
])


class PolicyManagerError(Exception):
    """Base exception for policy manager operations."""


def normalize_hostname(host: str) -> str:
    """Normalize and lowercase hostname without trailing dots or schemes."""
    clean = host.strip()
    if "://" in clean:
        clean = clean.split("://", 1)[1]
    if "/" in clean:
        clean = clean.split("/", 1)[0]
    if ":" in clean:
        clean = clean.split(":", 1)[0]
    clean = clean.rstrip(".").lower()
    try:
        clean = clean.encode("idna").decode("ascii")
    except (UnicodeError, AttributeError):
        pass
    return clean


def validate_hostname_syntax(host: str) -> dict[str, Any]:
    """Validate RFC 1035 / RFC 1123 hostname syntax and assess static risk."""
    normalized = normalize_hostname(host)
    risk_flags: list[str] = []

    if not normalized:
        return {"valid": False, "error": "empty_hostname", "risk_flags": risk_flags}

    if len(normalized) > 253:
        return {"valid": False, "error": "hostname_too_long_exceeds_253_chars", "risk_flags": risk_flags}

    # Reject IP address literals (must use hostnames)
    try:
        ipaddress.ip_address(normalized)
        return {"valid": False, "error": "ip_address_literal_prohibited", "risk_flags": risk_flags}
    except ValueError:
        pass

    labels = normalized.split(".")
    if len(labels) < 2:
        return {"valid": False, "error": "insufficient_labels_must_have_tld", "risk_flags": risk_flags}

    tld = labels[-1]
    if tld.isdigit():
        return {"valid": False, "error": "numeric_tld_prohibited", "risk_flags": risk_flags}

    label_regex = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)
    for idx, label in enumerate(labels):
        if not label:
            return {"valid": False, "error": f"empty_label_at_index_{idx}", "risk_flags": risk_flags}
        if len(label) > 63:
            return {"valid": False, "error": f"label_too_long_at_index_{idx}", "risk_flags": risk_flags}
        if not label_regex.match(label):
            return {"valid": False, "error": f"invalid_characters_in_label_{label}", "risk_flags": risk_flags}

    # Risk heuristics
    if tld in HIGH_RISK_TLDS:
        risk_flags.append(f"high_risk_tld:.{tld}")

    for dyn in DYNAMIC_DNS_DOMAINS:
        if normalized == dyn or normalized.endswith("." + dyn):
            risk_flags.append(f"dynamic_dns_provider:{dyn}")
            break

    if len(labels) > 4:
        risk_flags.append("excessive_subdomain_depth")

    return {"valid": True, "error": None, "risk_flags": risk_flags}


def check_dns_liveness(
    host: str,
    timeout: float = 3.0,
    resolver: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Resolve hostname to verify DNS liveness and anti-SSRF address bounds."""
    normalized = normalize_hostname(host)
    resolve_fn = resolver or socket.getaddrinfo

    orig_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        answers = resolve_fn(normalized, 443, proto=socket.IPPROTO_TCP)
    except Exception as exc:
        return {
            "resolvable": False,
            "ips": [],
            "ssrf_risk": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        socket.setdefaulttimeout(orig_timeout)

    ips: list[str] = []
    has_ssrf = False
    for family, _, _, _, sockaddr in answers:
        ip_str = sockaddr[0]
        if ip_str not in ips:
            ips.append(ip_str)
        try:
            ip_obj = ipaddress.ip_address(ip_str)
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved or ip_obj.is_link_local:
                has_ssrf = True
        except ValueError:
            pass

    return {
        "resolvable": True,
        "ips": ips,
        "ssrf_risk": has_ssrf,
        "error": None,
    }


def _serialized(method):
    """Serialize governance transactions across manager instances in this process."""
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._mutation_lock:
            return method(self, *args, **kwargs)
    return locked


class PolicyManager:
    """Manages the egress policy lifecycle, approvals, and attestation."""

    _mutation_lock = threading.RLock()

    def __init__(
        self,
        policy_path: Path = DEFAULT_POLICY_PATH,
        candidates_path: Path = DEFAULT_CANDIDATES_PATH,
        approvals_path: Path = DEFAULT_APPROVALS_PATH,
        attest_script: Path = DEFAULT_ATTEST_SCRIPT,
        re_sign_runner: Callable[[], bool] | None = None,
    ):
        self.policy_path = policy_path
        self.candidates_path = candidates_path
        self.approvals_path = approvals_path
        self.attest_script = attest_script
        self._re_sign_runner = re_sign_runner

    def load_policy_yaml(self) -> dict[str, Any]:
        """Load and parse the policy YAML file."""
        if not self.policy_path.is_file():
            raise PolicyManagerError(f"Policy file not found: {self.policy_path}")
        try:
            data = yaml.safe_load(self.policy_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise PolicyManagerError("Policy file is not a valid YAML mapping")
            return data
        except yaml.YAMLError as exc:
            raise PolicyManagerError(f"YAML parse error: {exc}") from exc

    def get_allowed_hosts(self) -> set[str]:
        """Return the current set of allowlisted hosts from egress_policy.yaml."""
        data = self.load_policy_yaml()
        broker = data.get("broker") or {}
        raw_hosts = broker.get("allowed_hosts") or []
        return {normalize_hostname(h) for h in raw_hosts}

    def get_approval_history(self) -> list[dict[str, Any]]:
        """Read all historical approval/rejection audit events."""
        if not self.approvals_path.is_file():
            return []
        records: list[dict[str, Any]] = []
        for line in self.approvals_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def get_decisions(self) -> dict[str, dict[str, Any]]:
        """Return mapping of normalized host -> latest audit decision."""
        history = self.get_approval_history()
        decisions: dict[str, dict[str, Any]] = {}
        for rec in history:
            host = normalize_hostname(str(rec.get("host") or ""))
            if host:
                decisions[host] = rec
        return decisions

    def read_candidates(self) -> list[dict[str, Any]]:
        """Read raw candidates from policy_expansion_candidates.jsonl."""
        if not self.candidates_path.is_file():
            return []
        candidates: list[dict[str, Any]] = []
        for line in self.candidates_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                candidates.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return candidates

    def summarize_candidates(self, unreviewed_only: bool = True) -> list[dict[str, Any]]:
        """Aggregate candidates by host with usage metadata and decision status."""
        raw_candidates = self.read_candidates()
        allowed = self.get_allowed_hosts()
        decisions = self.get_decisions()

        grouped: dict[str, dict[str, Any]] = {}
        for item in raw_candidates:
            raw_host = str(item.get("host") or "")
            host = normalize_hostname(raw_host)
            if not host:
                continue

            if host not in grouped:
                grouped[host] = {
                    "host": host,
                    "count": 0,
                    "first_seen": item.get("timestamp"),
                    "last_seen": item.get("timestamp"),
                    "task_ids": set(),
                    "example_urls": set(),
                }

            entry = grouped[host]
            entry["count"] += 1
            ts = item.get("timestamp")
            if ts:
                if not entry["first_seen"] or ts < entry["first_seen"]:
                    entry["first_seen"] = ts
                if not entry["last_seen"] or ts > entry["last_seen"]:
                    entry["last_seen"] = ts

            tid = item.get("task_id")
            if tid is not None:
                entry["task_ids"].add(tid)

            url = item.get("url")
            if url and len(entry["example_urls"]) < 3:
                entry["example_urls"].add(url)

        results: list[dict[str, Any]] = []
        for host, entry in grouped.items():
            is_allowed = host in allowed
            dec = decisions.get(host)
            prev_decision = dec.get("action") if dec else None

            if unreviewed_only and (is_allowed or prev_decision == "reject"):
                continue

            results.append({
                "host": host,
                "count": entry["count"],
                "first_seen": entry["first_seen"],
                "last_seen": entry["last_seen"],
                "task_ids": sorted(entry["task_ids"]),
                "example_urls": sorted(entry["example_urls"]),
                "already_allowed": is_allowed,
                "previous_decision": prev_decision,
            })

        # Sort by frequency of occurrence descending
        results.sort(key=lambda x: x["count"], reverse=True)
        return results

    @_serialized
    def propose(
        self,
        unreviewed_only: bool = True,
        resolve_dns: bool = True,
        resolver: Callable[..., Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Generate structured policy expansion proposals with syntax and DNS checks."""
        summaries = self.summarize_candidates(unreviewed_only=unreviewed_only)
        proposals: list[dict[str, Any]] = []

        for item in summaries:
            host = item["host"]
            syntax = validate_hostname_syntax(host)
            dns_info: dict[str, Any] = {"resolvable": None, "ips": [], "ssrf_risk": False, "error": None}

            if syntax["valid"] and resolve_dns:
                dns_info = check_dns_liveness(host, resolver=resolver)

            # Formulate proposed safety rationale
            recs: list[str] = []
            if not syntax["valid"]:
                recs.append(f"REJECT: Invalid hostname syntax ({syntax['error']})")
            elif dns_info["ssrf_risk"]:
                recs.append("REJECT: DNS resolved to private/loopback IP (SSRF danger)")
            elif dns_info["resolvable"] is False:
                recs.append(f"WARNING: DNS resolution failed ({dns_info['error']})")
            elif syntax["risk_flags"]:
                recs.append(f"REVIEW: Host has risk indicators: {', '.join(syntax['risk_flags'])}")
            else:
                recs.append(f"RECOMMEND APPROVAL: Legitimate research source requested {item['count']}x across tasks {item['task_ids']}")

            proposals.append({
                **item,
                "syntax": syntax,
                "dns": dns_info,
                "recommendation": recs[0] if recs else "UNKNOWN",
            })

        return proposals

    @_serialized
    def re_sign_attestation(self) -> bool:
        """Invoke attestation re-signing script to update .harness/egress_attestation.signed."""
        if self._re_sign_runner:
            return self._re_sign_runner()

        if not self.attest_script.is_file():
            raise PolicyManagerError(f"Attestation script not found: {self.attest_script}")

        cmd = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.attest_script),
            "-Action",
            "Attest",
        ]
        try:
            result = subprocess.run(
                cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError) as exc:
            raise PolicyManagerError(f"Failed to re-sign attestation: {exc}") from exc

    @_serialized
    def approve(
        self,
        host: str,
        operator: str = "operator",
        rationale: str = "",
        re_sign: bool = True,
    ) -> dict[str, Any]:
        """Operator-gated approval: appends host to egress_policy.yaml and re-signs."""
        normalized = normalize_hostname(host)
        syntax = validate_hostname_syntax(normalized)
        if not syntax["valid"]:
            raise PolicyManagerError(f"Cannot approve invalid hostname '{normalized}': {syntax['error']}")

        data = self.load_policy_yaml()
        broker = data.setdefault("broker", {})
        allowed_list = list(broker.get("allowed_hosts") or [])

        normalized_existing = {normalize_hostname(h) for h in allowed_list}
        if normalized not in normalized_existing:
            allowed_list.append(normalized)
            allowed_list.sort(key=lambda s: s.lower())
            broker["allowed_hosts"] = allowed_list

            # Write atomically
            tmp_path = self.policy_path.with_suffix(".tmp.yaml")
            tmp_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
            tmp_path.replace(self.policy_path)

        # Compute new policy digest
        canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        policy_digest = hashlib.sha256(canonical_bytes).hexdigest()

        # Re-sign attestation token
        re_signed = False
        if re_sign:
            re_signed = self.re_sign_attestation()

        # Append to approvals audit ledger
        self.approvals_path.parent.mkdir(parents=True, exist_ok=True)
        audit_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "approve",
            "host": normalized,
            "operator": operator,
            "rationale": rationale or "Operator authorized research domain",
            "policy_digest": policy_digest,
            "re_signed": re_signed,
        }
        with self.approvals_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(audit_record, sort_keys=True) + "\n")

        return audit_record

    @_serialized
    def reject(
        self,
        host: str,
        operator: str = "operator",
        reason: str = "",
    ) -> dict[str, Any]:
        """Operator-gated rejection: records rejection in policy_approvals.audit.jsonl."""
        normalized = normalize_hostname(host)
        self.approvals_path.parent.mkdir(parents=True, exist_ok=True)
        audit_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "reject",
            "host": normalized,
            "operator": operator,
            "reason": reason or "Operator rejected domain admission",
        }
        with self.approvals_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(audit_record, sort_keys=True) + "\n")

        return audit_record


def main(argv: list[str] | None = None) -> int:
    """CLI dispatcher for policy management."""
    parser = argparse.ArgumentParser(description="AGI_like Egress Policy Manager (Phase 1)")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH, help="Path to egress_policy.yaml")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES_PATH, help="Path to candidates JSONL")
    parser.add_argument("--approvals", type=Path, default=DEFAULT_APPROVALS_PATH, help="Path to approvals JSONL")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: list
    subparsers.add_parser("list", help="List observed candidate expansion domains")

    # Subcommand: propose
    subparsers.add_parser("propose", help="Generate safety proposals with DNS and syntax checks")

    # Subcommand: approve
    approve_p = subparsers.add_parser("approve", help="Approve domain, add to policy, and re-sign attestation")
    approve_p.add_argument("domain", help="Domain to approve")
    approve_p.add_argument("--operator", default=os.getenv("USERNAME", "operator"), help="Operator identifier")
    approve_p.add_argument("--rationale", default="", help="Safety rationale for approval")
    approve_p.add_argument("--no-attest", action="store_true", help="Skip attestation re-signing")

    # Subcommand: reject
    reject_p = subparsers.add_parser("reject", help="Reject domain and record decision")
    reject_p.add_argument("domain", help="Domain to reject")
    reject_p.add_argument("--operator", default=os.getenv("USERNAME", "operator"), help="Operator identifier")
    reject_p.add_argument("--reason", default="", help="Reason for rejection")

    args = parser.parse_args(argv)
    manager = PolicyManager(
        policy_path=args.policy,
        candidates_path=args.candidates,
        approvals_path=args.approvals,
    )

    if args.command == "list":
        candidates = manager.summarize_candidates(unreviewed_only=False)
        print(f"Total candidate domains: {len(candidates)}")
        for c in candidates:
            status = "[ALLOWED]" if c["already_allowed"] else (f"[{c['previous_decision'].upper()}]" if c["previous_decision"] else "[PENDING]")
            print(f"  {status:<12} {c['host']:<30} (seen {c['count']:>2}x in tasks {c['task_ids']})")
        return 0

    if args.command == "propose":
        proposals = manager.propose()
        print(f"\nActive Expansion Proposals ({len(proposals)} unreviewed candidates):\n")
        for p in proposals:
            dns_status = "DNS OK" if p["dns"]["resolvable"] else f"DNS FAIL ({p['dns']['error']})"
            print(f"Domain: {p['host']} (Count: {p['count']}, Tasks: {p['task_ids']})")
            print(f"  Status: {dns_status} | IPs: {p['dns']['ips']}")
            print(f"  Action: {p['recommendation']}\n")
        return 0

    if args.command == "approve":
        rec = manager.approve(
            args.domain,
            operator=args.operator,
            rationale=args.rationale,
            re_sign=not args.no_attest,
        )
        print(f"[SUCCESS] Approved {rec['host']} by {rec['operator']}")
        print(f"  Policy Digest: {rec['policy_digest']}")
        print(f"  Attestation Re-Signed: {rec['re_signed']}")
        return 0

    if args.command == "reject":
        rec = manager.reject(
            args.domain,
            operator=args.operator,
            reason=args.reason,
        )
        print(f"[RECORDED] Rejected {rec['host']} by {rec['operator']}: {rec['reason']}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
