"""Unified Executive Web Console (MiroFish + Munder Difflin + AGI_like).

Couples:
1. MiroFish: Visual intelligence network topology graph & real-time thought stream.
2. Munder Difflin: Swarm office floor (Worker, Critic, Broker, Signer), live
   Kanban pipeline, and token odometer / cost meter.
3. AGI_like: Cryptographic Ed25519 attestation seal, WFP firewall containment
   status, literal citation inspector modal, and policy governance portal.

Zero-dependency standard library server with embedded modern cockpit UI.
Usage:
    python orchestrator/web_ui.py [--port 8080] [--host 127.0.0.1]
"""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import execution_pause
from trust_gateway import Gateway

CONSOLE_CSS = Path(__file__).with_suffix(".css").read_text(encoding="utf-8")
LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 65536

# A 401 may show this credential form; it contains no dashboard or harness data.
# The submitted token goes only in the header, never in a URL or persistent storage.
LOGIN_HTML = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>AGI_like operator sign-in</title>
<style>body{font:18px system-ui;background:#090d16;color:#e2e8f0;max-width:32rem;margin:12vh auto;padding:2rem}input,button{font:inherit;padding:.6rem;margin-top:1rem}input{width:100%;box-sizing:border-box}</style>
<h1>AGI_like operator sign-in</h1>
<p>Enter the bearer token printed in the server terminal. Restart the server to rotate it.</p>
<form id="login"><label for="token">Operator token</label>
<input id="token" type="password" autocomplete="off" required>
<button>Open console</button></form><p id="error" role="alert"></p>
<script>
document.getElementById('login').addEventListener('submit', async event => {
  event.preventDefault();
  const input = document.getElementById('token');
  const token = input.value.trim(); input.value = '';
  try {
    const response = await fetch('/', {headers: {Authorization: 'Bearer ' + token}, cache: 'no-store'});
    if (!response.ok) throw new Error('Sign-in refused. Check the server terminal token.');
    const html = await response.text();
    document.open(); document.write(html); document.close();
  } catch (error) { document.getElementById('error').textContent = error.message; }
});
</script></html>"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AGI_like · Autonomous Swarm & Trust Gateway</title>
  <style>__LOCAL_CSS__</style>
  <style>
    @keyframes pulse-slow { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
    .animate-pulse-slow { animation: pulse-slow 3s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
    .node-glow { filter: drop-shadow(0 0 6px rgba(6, 182, 212, 0.6)); }
    .node-glow-amber { filter: drop-shadow(0 0 6px rgba(245, 158, 11, 0.6)); }
    .node-glow-red { filter: drop-shadow(0 0 6px rgba(239, 68, 68, 0.6)); }
  </style>
</head>
<body class="bg-darkbg text-slate-200 min-h-screen flex flex-col font-sans selection:bg-cyan-500 selection:text-white">

  <!-- TOP HEADER / ATTESTATION SEAL -->
  <header class="border-b border-bordercol bg-slate-950/80 backdrop-blur sticky top-0 z-50 px-6 py-3.5 flex flex-wrap items-center justify-between gap-4">
    <div class="flex items-center gap-4">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-xl bg-gradient-to-br from-cyan-500 to-emerald-500 flex items-center justify-center font-black text-black text-xl shadow-lg shadow-cyan-500/20">
          A
        </div>
        <div>
          <div class="flex items-center gap-2">
            <span class="font-bold text-lg tracking-tight text-white">AGI_like</span>
            <span class="text-xs font-mono px-2 py-0.5 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800/60 font-semibold">V1 TRUST CONSOLE</span>
          </div>
          <div class="text-[11px] text-slate-400 flex items-center gap-1.5 font-mono">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block animate-pulse"></span>
            MiroFish Graph + Munder Swarm + Hardened Attestation Kernel
          </div>
        </div>
      </div>
    </div>

    <!-- ACTIVE SECURITY INDICATORS -->
    <div class="flex items-center gap-3 text-xs font-mono">
      <div class="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-cardbg border border-bordercol">
        <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
        <span class="text-slate-400">WFP Token:</span>
        <span class="text-slate-200 font-bold" id="hdr-wfp">Unknown</span>
      </div>
      <div class="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-cardbg border border-bordercol">
        <span class="w-2 h-2 rounded-full bg-cyan-400"></span>
        <span class="text-slate-400">Broker:</span>
        <span class="text-cyan-300 font-bold" id="hdr-broker">127.0.0.1:8787</span>
      </div>
      <div class="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-cardbg border border-bordercol cursor-pointer hover:border-cyan-700 transition" onclick="showAttestationModal()">
        <span class="w-2 h-2 rounded-full bg-slate-500"></span>
        <span class="text-slate-400">Attestation:</span><span id="hdr-attestation-state" class="text-red-400">NOT VERIFIED</span>
        <span class="text-emerald-400 font-bold" id="hdr-digest">Pending</span>
      </div>
      <button id="btn-estop" onclick="engageEstop()" class="px-3 py-1.5 rounded-lg font-bold border transition flex items-center gap-1.5 bg-red-950/60 border-red-800 text-red-300 hover:bg-red-900/80">
        <span class="w-2 h-2 rounded-full bg-red-500"></span>
        <span id="estop-label">ESTOP: ENGAGED</span>
      </button>
    </div>
  </header>

  <!-- MAIN DASHBOARD CONTENT -->
  <main class="flex-1 p-6 max-w-7xl w-full mx-auto space-y-6">

    <!-- 1. MUNDER DIFFLIN SWARM FLOOR -->
    <section class="space-y-3">
      <div class="flex items-center justify-between">
        <h2 class="text-sm font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
          <svg class="w-4 h-4 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4"></path></svg>
          Munder Difflin Swarm Floor · Autonomous Agent Stations
        </h2>
        <div class="text-xs text-slate-400 font-mono">
          <span class="text-slate-200 font-bold" id="stat-tasks-total">--</span> missions recorded · <span class="text-emerald-400 font-bold" id="stat-tokens-total">--</span> tokens burned
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
        <!-- Station 1: Worker Agent -->
        <div class="p-4 rounded-xl bg-cardbg border border-bordercol relative overflow-hidden group hover:border-cyan-500/50 transition">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-mono font-bold text-cyan-400">DESK #01 · WORKER</span>
            <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
          </div>
          <div class="font-bold text-base text-white">Hermes Worker</div>
          <div class="text-xs text-slate-400 mt-0.5">Windows Restricted Token (S-1-5-12)</div>
          <div class="mt-3 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs font-mono">
            <span class="text-slate-500">Active Model:</span>
            <span class="text-slate-300 font-semibold" id="station-worker-model">openai/gpt-4o</span>
          </div>
        </div>

        <!-- Station 2: Critic Auditor -->
        <div class="p-4 rounded-xl bg-cardbg border border-bordercol relative overflow-hidden group hover:border-emerald-500/50 transition">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-mono font-bold text-emerald-400">DESK #02 · AUDITOR</span>
            <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
          </div>
          <div class="font-bold text-base text-white">Independent Critic</div>
          <div class="text-xs text-slate-400 mt-0.5">Zero-Egress Host Ground Truth</div>
          <div class="mt-3 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs font-mono">
            <span class="text-slate-500">Authority:</span>
            <span class="text-slate-300 font-semibold">glm-5.2:cloud</span>
          </div>
        </div>

        <!-- Station 3: Boundary Warden -->
        <div class="p-4 rounded-xl bg-cardbg border border-bordercol relative overflow-hidden group hover:border-amber-500/50 transition">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-mono font-bold text-amber-400">DESK #03 · WARDEN</span>
            <span class="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></span>
          </div>
          <div class="font-bold text-base text-white">Egress Broker</div>
          <div class="text-xs text-slate-400 mt-0.5">mtime Dynamic Hot-Reload</div>
          <div class="mt-3 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs font-mono">
            <span class="text-slate-500">Allowlist:</span>
            <span class="text-cyan-300 font-semibold" id="station-allowlist-count">-- hosts</span>
          </div>
        </div>

        <!-- Station 4: Attestation Signer -->
        <div class="p-4 rounded-xl bg-cardbg border border-bordercol relative overflow-hidden group hover:border-purple-500/50 transition">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-mono font-bold text-purple-400">DESK #04 · SCRIBE</span>
            <span class="w-2 h-2 rounded-full bg-purple-400"></span>
          </div>
          <div class="font-bold text-base text-white">AGI_AuditSigner</div>
          <div class="text-xs text-slate-400 mt-0.5">Windows SCM Isolated Service</div>
          <div class="mt-3 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs font-mono">
            <span class="text-slate-500">Identity:</span>
            <span class="text-purple-300 font-semibold">.\AGI_Signer</span>
          </div>
        </div>
      </div>
    </section>

    <!-- 2. MIROFISH COGNITIVE GRAPH & MISSION DISPATCHER -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">

      <!-- MIROFISH GRAPH (2 Cols) -->
      <section class="lg:col-span-2 p-5 rounded-2xl bg-cardbg border border-bordercol flex flex-col">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-sm font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
            <svg class="w-4 h-4 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z"></path></svg>
            MiroFish Cognitive Topology · Domain Reachability & Trust Nodes
          </h3>
          <div class="flex items-center gap-3 text-xs font-mono">
            <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-emerald-400"></span> Allowed</span>
            <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-amber-400"></span> Review</span>
            <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-red-400"></span> Denied</span>
          </div>
        </div>

        <!-- SVG Topology Canvas -->
        <div class="flex-1 min-h-[300px] w-full rounded-xl bg-slate-950/80 border border-slate-800/80 relative overflow-hidden flex items-center justify-center" id="graph-container">
          <svg id="mirofish-svg" class="w-full h-full absolute inset-0 cursor-crosshair"></svg>
          <div id="graph-tooltip" class="absolute hidden px-3 py-2 rounded-lg bg-slate-900 border border-cyan-700 text-xs font-mono shadow-2xl z-20 pointer-events-none"></div>
        </div>
      </section>

      <!-- MISSION DISPATCH CONTROL (1 Col) -->
      <section class="p-5 rounded-2xl bg-cardbg border border-bordercol flex flex-col justify-between">
        <div>
          <h3 class="text-sm font-bold uppercase tracking-wider text-slate-400 mb-3 flex items-center gap-2">
            <svg class="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
            Interactive Mission Dispatch
          </h3>

          <form id="dispatch-form" onsubmit="handleDispatch(event)" class="space-y-4">
            <div>
              <label class="block text-xs font-mono text-slate-400 mb-1">TASK SPECIFICATION / OBJECTIVE</label>
              <textarea id="disp-spec" rows="3" required class="w-full rounded-lg bg-slate-950 border border-slate-700 p-2.5 text-xs font-mono text-white focus:outline-none focus:border-cyan-500 transition" placeholder="e.g. Research prompt marketplace metrics for PromptBase..."></textarea>
            </div>

            <div>
              <label class="block text-xs font-mono text-slate-400 mb-1">PASS CRITERIA (CRITIC CHECKLIST)</label>
              <textarea id="disp-criteria" rows="2" class="w-full rounded-lg bg-slate-950 border border-slate-700 p-2.5 text-xs font-mono text-white focus:outline-none focus:border-cyan-500 transition" placeholder="At least 3 verified independent sources with retrieval dates..."></textarea>
            </div>

            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-xs font-mono text-slate-400 mb-1">WORKER MODEL</label>
                <select id="disp-model" class="w-full rounded-lg bg-slate-950 border border-slate-700 p-2 text-xs font-mono text-white focus:outline-none focus:border-cyan-500">
                  <option value="openai-api/gpt-4o">openai/gpt-4o (Frontier)</option>
                  <option value="byteplus/ark-code-latest">byteplus/ark-code-latest</option>
                  <option value="ollama/qwen2.5-coder">ollama/qwen2.5-coder</option>
                </select>
              </div>
              <div>
                <label class="block text-xs font-mono text-slate-400 mb-1">BUDGET PARAMETER CAP</label>
                <select id="disp-budget" class="w-full rounded-lg bg-slate-950 border border-slate-700 p-2 text-xs font-mono text-white focus:outline-none focus:border-cyan-500">
                  <option value="0.50">$0.50 USD</option>
                  <option value="1.00" selected>$1.00 USD (maximum)</option>
                </select>
              </div>
            </div>

            <p class="text-xs text-slate-400">Admission caps: $1.00 / 100,000 tokens. No runtime spend enforcement.</p>
            <button type="submit" id="btn-dispatch-submit" disabled class="w-full mt-2 py-2.5 rounded-xl bg-gradient-to-r from-cyan-600 to-emerald-600 hover:from-cyan-500 hover:to-emerald-500 text-black font-extrabold text-xs uppercase tracking-wider transition shadow-lg shadow-cyan-600/20 flex items-center justify-center gap-2">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>
              Dispatch Under Containment
            </button>
          </form>
        </div>

        <div class="mt-4 pt-3 border-t border-slate-800/80 text-[11px] text-slate-500 font-mono flex items-center justify-between">
          <span>Fail-Closed Bounds Active</span>
          <span class="text-emerald-400 font-semibold">Strict Invariant Enforced</span>
        </div>
      </section>
    </div>

    <!-- 3. MUNDER DIFFLIN KANBAN & PIPELINE BOARD -->
    <section class="space-y-3">
      <div class="flex items-center justify-between">
        <h3 class="text-sm font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
          <svg class="w-4 h-4 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"></path></svg>
          Swarm Mission Pipeline & Attested Deliverables
        </h3>
        <div class="text-xs font-mono text-slate-400 flex items-center gap-2">
          <button onclick="refreshData()" class="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 transition">↻ Refresh</button>
        </div>
      </div>

      <div class="overflow-x-auto rounded-2xl border border-bordercol bg-cardbg">
        <table class="w-full text-left text-xs font-mono border-collapse">
          <thead>
            <tr class="border-b border-slate-800 bg-slate-950/60 text-slate-400">
              <th class="py-3 px-4">TASK ID</th>
              <th class="py-3 px-4">MISSION</th>
              <th class="py-3 px-4">STATUS</th>
              <th class="py-3 px-4">CRITIC VERDICT</th>
              <th class="py-3 px-4">MODEL USED</th>
              <th class="py-3 px-4">TOKENS (IN/OUT)</th>
              <th class="py-3 px-4">DURATION</th>
              <th class="py-3 px-4 text-right">ACTION</th>
            </tr>
          </thead>
          <tbody id="task-table-body" class="divide-y divide-slate-800/60">
            <tr>
              <td colspan="8" class="text-center py-6 text-slate-500">Loading swarm tasks...</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 4. POLICY GOVERNANCE (PHASE 1 INTEGRATION) -->
    <section class="space-y-3">
      <div class="flex items-center justify-between">
        <h3 class="text-sm font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
          <svg class="w-4 h-4 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path></svg>
          Policy Governance Portal · Candidate Domain Expansion Queue
        </h3>
        <span class="text-xs font-mono text-slate-500">Propose-and-Confirm Protocol (No Auto-Admission)</span>
      </div>

      <div class="overflow-x-auto rounded-2xl border border-bordercol bg-cardbg">
        <table class="w-full text-left text-xs font-mono border-collapse">
          <thead>
            <tr class="border-b border-slate-800 bg-slate-950/60 text-slate-400">
              <th class="py-3 px-4">CANDIDATE HOST</th>
              <th class="py-3 px-4">FREQ</th>
              <th class="py-3 px-4">REFERENCING TASKS</th>
              <th class="py-3 px-4">SYNTAX & DNS CHECK</th>
              <th class="py-3 px-4">SAFETY RECOMMENDATION</th>
              <th class="py-3 px-4 text-right">DECISION</th>
            </tr>
          </thead>
          <tbody id="candidate-table-body" class="divide-y divide-slate-800/60">
            <tr>
              <td colspan="6" class="text-center py-6 text-slate-500">Loading policy proposals...</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

  </main>

  <!-- MODAL: LITERAL CITATION & DELIVERABLE INSPECTOR -->
  <div id="deliverable-modal" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm hidden items-center justify-center p-4">
    <div class="bg-cardbg border border-cyan-800/80 rounded-2xl max-w-5xl w-full max-h-[90vh] flex flex-col overflow-hidden shadow-2xl">
      <!-- Modal Header -->
      <div class="p-4 border-b border-slate-800 flex items-center justify-between bg-slate-950/60">
        <div>
          <h3 class="font-bold text-white text-base flex items-center gap-2">
            <span class="text-cyan-400">Task #<span id="modal-task-id">--</span></span> Deliverable & Cryptographic Evidence
          </h3>
          <div class="text-xs font-mono text-slate-400 mt-0.5" id="modal-subtitle">--</div>
        </div>
        <button onclick="closeDeliverableModal()" class="w-8 h-8 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 flex items-center justify-center font-bold">✕</button>
      </div>

      <!-- Modal Body (Split view) -->
      <div class="flex-1 overflow-y-auto grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-slate-800 p-4 gap-4 text-xs font-mono">
        <!-- Left: Deliverable -->
        <div class="space-y-3">
          <div class="font-bold text-slate-400 uppercase tracking-wider flex items-center justify-between">
            <span>Recorded Deliverable</span>
            <span class="text-emerald-400" id="modal-verdict-tag">[PASS]</span>
          </div>
          <div id="modal-deliverable-text" class="p-3.5 rounded-xl bg-slate-950 border border-slate-800/80 text-slate-300 whitespace-pre-wrap leading-relaxed max-h-[60vh] overflow-y-auto"></div>
        </div>

        <!-- Right: Broker Evidence & Citations -->
        <div class="space-y-3">
          <div class="font-bold text-slate-400 uppercase tracking-wider flex items-center justify-between">
            <span>Recorded Broker Receipts</span>
            <span class="text-cyan-400" id="modal-evidence-tag">Broker Receipts</span>
          </div>
          <div id="modal-evidence-box" class="space-y-2.5 max-h-[60vh] overflow-y-auto pr-1">
            <div class="text-slate-500 italic">Inspecting broker audit trails...</div>
          </div>
        </div>
      </div>

      <!-- Modal Footer -->
      <div class="p-3 border-t border-slate-800 bg-slate-950/60 flex items-center justify-between text-xs font-mono">
        <div class="text-slate-400" id="modal-footer-info">Broker receipts do not independently verify deliverable claims</div>
        <button onclick="closeDeliverableModal()" class="px-4 py-1.5 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-black font-bold">Close Inspector</button>
      </div>
    </div>
  </div>

  <p id="api-error" role="alert" class="text-red-400 px-6"></p>
  <!-- JAVASCRIPT CONTROLLER -->
  <script>
    let globalState = null;
    const bearerToken = __BEARER_TOKEN_JSON__;
    function escapeHTML(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
    }

    async function fetchAPI(endpoint, options = {}) {
      try {
        const res = await fetch(endpoint, {
          ...options, cache: 'no-store',
          headers: {...options.headers, Authorization: 'Bearer ' + bearerToken}
        });
        if (!res.ok) {
          const error = await res.json();
          throw new Error(error.error || 'Request refused');
        }
        return await res.json();
      } catch (err) {
        console.error('API Error:', err.message);
        document.getElementById('api-error').textContent = err.message;
        return null;
      }
    }

    function showAttestationModal() {
      if (!globalState) return;
      alert('Attestation: ' + (globalState.attestation_valid ? 'VERIFIED' : 'NOT VERIFIED') +
            '\\nPolicy digest: ' + (globalState.attestation_digest || 'Unavailable') +
            '\\nVerification result: ' + (globalState.attestation_error || 'Signature, trusted key and policy checks passed'));
    }

    async function refreshData() {
      // 1. Status & Swarm Overview
      const status = await fetchAPI('/api/status');
      if (status) {
        globalState = status;
        document.getElementById('api-error').textContent = '';
        const badge = document.getElementById('hdr-attestation-state');
        badge.textContent = status.attestation_valid ? 'VERIFIED' : 'NOT VERIFIED';
        badge.className = status.attestation_valid ? 'text-emerald-400' : 'text-red-400';
        document.getElementById('hdr-wfp').textContent = status.worker_identity || 'Unknown';
        document.getElementById('btn-dispatch-submit').disabled = status.estop_engaged;
        document.getElementById('btn-estop').disabled = status.estop_engaged;
        document.getElementById('hdr-digest').innerText = (status.attestation_digest || 'none').substring(0, 12) + '...';
        document.getElementById('station-allowlist-count').innerText = `${status.allowed_hosts_count || 0} hosts`;
        document.getElementById('stat-tasks-total').innerText = status.total_tasks || 0;
        document.getElementById('stat-tokens-total').innerText = ((status.total_tokens || 0) / 1000).toFixed(1) + 'k';

        const estopBtn = document.getElementById('btn-estop');
        const estopLbl = document.getElementById('estop-label');
        if (status.estop_engaged) {
          estopBtn.className = "px-3 py-1.5 rounded-lg font-bold border transition flex items-center gap-1.5 bg-red-950/60 border-red-800 text-red-300 hover:bg-red-900/80";
          estopLbl.innerText = "ESTOP: ENGAGED";
        } else {
          estopBtn.className = "px-3 py-1.5 rounded-lg font-bold border transition flex items-center gap-1.5 bg-emerald-950/60 border-emerald-800 text-emerald-300 hover:bg-emerald-900/80";
          estopLbl.innerText = "ENGAGE ESTOP";
        }
      }

      // 2. Tasks List
      const tasksData = await fetchAPI('/api/tasks');
      if (tasksData && tasksData.tasks) {
        renderTasks(tasksData.tasks);
      }

      // 3. Candidates List
      const candData = await fetchAPI('/api/candidates');
      if (candData && candData.candidates) {
        renderCandidates(candData.candidates);
      }

      // 4. MiroFish Graph
      const graphData = await fetchAPI('/api/graph');
      if (graphData) {
        renderMirofishGraph(graphData);
      }
    }

    function renderTasks(tasks) {
      const tbody = document.getElementById('task-table-body');
      if (!tasks.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center py-6 text-slate-500">No tasks found.</td></tr>';
        return;
      }

      tbody.innerHTML = tasks.slice(0, 15).map(t => {
        let verdictBadge = '<span class="text-slate-500">--</span>';
        if (t.critic_verdict === 'pass') verdictBadge = '<span class="px-2 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800/60 font-bold">PASS</span>';
        else if (t.critic_verdict === 'fail') verdictBadge = '<span class="px-2 py-0.5 rounded bg-red-950 text-red-400 border border-red-800/60 font-bold">FAIL</span>';
        else if (t.critic_verdict === 'needs_review') verdictBadge = '<span class="px-2 py-0.5 rounded bg-amber-950 text-amber-400 border border-amber-800/60 font-bold">REVIEW</span>';

        let statusColor = t.status === 'done' ? 'text-emerald-400' : (t.status === 'failed' ? 'text-red-400' : 'text-cyan-400');

        return `
          <tr class="hover:bg-slate-900/40 transition">
            <td class="py-2.5 px-4 font-bold text-white">#${Number(t.task_id)}</td>
            <td class="py-2.5 px-4 text-slate-300 truncate max-w-[200px]" title="${escapeHTML(t.spec)}">${escapeHTML(t.mission_id || 'custom')}</td>
            <td class="py-2.5 px-4 ${statusColor} font-bold">${escapeHTML(t.status)}</td>
            <td class="py-2.5 px-4">${verdictBadge}</td>
            <td class="py-2.5 px-4 text-slate-400">${escapeHTML(t.model_used || '--')}</td>
            <td class="py-2.5 px-4 text-slate-400">${Number(t.tokens_in || 0).toLocaleString()} / ${Number(t.tokens_out || 0).toLocaleString()}</td>
            <td class="py-2.5 px-4 text-slate-400">${t.duration_seconds ? Number(t.duration_seconds).toFixed(1) + 's' : '--'}</td>
            <td class="py-2.5 px-4 text-right">
              <button onclick="inspectTask(${Number(t.task_id)})" class="px-2.5 py-1 rounded bg-cyan-950 hover:bg-cyan-900 text-cyan-400 border border-cyan-800/60 transition">Inspect</button>
            </td>
          </tr>
        `;
      }).join('');
    }

    function renderCandidates(candidates) {
      const tbody = document.getElementById('candidate-table-body');
      if (!candidates.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center py-6 text-slate-500">Zero pending candidate domains. All traffic clean.</td></tr>';
        return;
      }

      tbody.innerHTML = candidates.slice(0, 8).map(c => {
        let dnsBadge = c.dns && c.dns.resolvable ? '<span class="text-emerald-400">DNS OK</span>' : '<span class="text-red-400">DNS UNRESOLVED</span>';
        if (c.dns && c.dns.ssrf_risk) dnsBadge += ' <span class="text-red-500 font-bold">[SSRF RISK]</span>';

        return `
          <tr class="hover:bg-slate-900/40 transition">
            <td class="py-2.5 px-4 font-bold text-white">${escapeHTML(c.host)}</td>
            <td class="py-2.5 px-4 text-amber-400 font-bold">${escapeHTML(c.count)}x</td>
            <td class="py-2.5 px-4 text-slate-400">[${escapeHTML(c.task_ids.join(', '))}]</td>
            <td class="py-2.5 px-4">${dnsBadge}</td>
            <td class="py-2.5 px-4 text-slate-300 truncate max-w-[260px]" title="${escapeHTML(c.recommendation)}">${escapeHTML(c.recommendation)}</td>
            <td class="py-2.5 px-4 text-right space-x-1.5">
              <button data-host="${escapeHTML(c.host)}" onclick="approveCandidate(this.dataset.host)" class="px-2.5 py-1 rounded bg-emerald-950 hover:bg-emerald-900 text-emerald-300 border border-emerald-800/60 font-bold transition">Approve & Sign</button>
              <button data-host="${escapeHTML(c.host)}" onclick="rejectCandidate(this.dataset.host)" class="px-2.5 py-1 rounded bg-red-950 hover:bg-red-900 text-red-300 border border-red-800/60 font-bold transition">Reject</button>
            </td>
          </tr>
        `;
      }).join('');
    }

    async function inspectTask(taskId) {
      const data = await fetchAPI(`/api/tasks/${taskId}`);
      if (!data) return;

      document.getElementById('modal-task-id').innerText = taskId;
      document.getElementById('modal-footer-info').innerText =
        `Lifecycle chain: ${data.attestation_chain_valid ? 'VERIFIED' : 'NOT VERIFIED'} (${Number(data.attestation_chain_steps || 0)} steps)` +
        (data.attestation_chain_error ? ` - ${data.attestation_chain_error}` : '') +
        '. Signatures attest recorded execution; source claims still require review.';
      document.getElementById('modal-subtitle').innerText = `Mission: ${data.mission_id || 'custom'} · Model: ${data.model_used || '--'} · Tokens: ${data.tokens_in || 0} in / ${data.tokens_out || 0} out`;
      document.getElementById('modal-deliverable-text').innerText = data.deliverable || '(No deliverable recorded for this attempt)';

      const vTag = document.getElementById('modal-verdict-tag');
      vTag.innerText = `[${(data.critic_verdict || data.status || 'UNGRADED').toUpperCase()}]`;
      vTag.className = data.critic_verdict === 'pass' ? 'text-emerald-400' : 'text-red-400';

      const evBox = document.getElementById('modal-evidence-box');
      if (data.broker_audit && data.broker_audit.length) {
        evBox.innerHTML = data.broker_audit.map((rec, i) => `
          <div class="p-2.5 rounded-lg bg-slate-900 border ${rec.decision === 'allow' ? 'border-emerald-800/60' : 'border-red-800/60'} text-[11px] font-mono">
            <div class="flex items-center justify-between font-bold mb-1">
              <span class="${rec.decision === 'allow' ? 'text-emerald-400' : 'text-red-400'}">HTTP CONNECT ${escapeHTML(String(rec.decision || 'unknown').toUpperCase())}</span>
              <span class="text-slate-500">${escapeHTML((rec.timestamp || '').substring(11, 19))}Z</span>
            </div>
            <div class="text-white truncate">Host: ${escapeHTML(rec.host || '--')}</div>
            ${rec.reason ? `<div class="text-slate-400 mt-0.5">Reason: ${escapeHTML(rec.reason)}</div>` : ''}
          </div>
        `).join('');
      } else {
        evBox.innerHTML = '<div class="text-slate-500 italic p-3">Zero external egress requests intercepted for this attempt. (Local/hermetic execution).</div>';
      }

      document.getElementById('deliverable-modal').classList.remove('hidden');
      document.getElementById('deliverable-modal').classList.add('flex');
    }

    function closeDeliverableModal() {
      document.getElementById('deliverable-modal').classList.add('hidden');
      document.getElementById('deliverable-modal').classList.remove('flex');
    }

    async function approveCandidate(host) {
      const rationale = prompt(`Enter safety rationale for approving ${host}:`, `Operator verified research source`);
      if (!rationale) return;
      const res = await fetchAPI('/api/candidates/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain: host, rationale })
      });
      if (res && res.success) {
        alert(res.record && res.record.re_signed
          ? `Approved ${host} and re-signed the attestation token.`
          : `Policy updated for ${host}, but attestation signing failed. Inspect via the operator CLI.`);
        refreshData();
      }
    }

    async function rejectCandidate(host) {
      const reason = prompt(`Enter rejection reason for ${host}:`, `Off-allowlist or unverified`);
      if (!reason) return;
      const res = await fetchAPI('/api/candidates/reject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain: host, reason })
      });
      if (res && res.success) {
        alert(`Rejected ${host}.`);
        refreshData();
      }
    }

    async function handleDispatch(event) {
      event.preventDefault();
      const spec = document.getElementById('disp-spec').value;
      const criteria = document.getElementById('disp-criteria').value;
      const budget = parseFloat(document.getElementById('disp-budget').value);

      const res = await fetchAPI('/api/dispatch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ spec, pass_criteria: criteria, max_budget_usd: budget })
      });

      if (res && res.task_id) {
        alert(`Mission queued as Task #${res.task_id} under fail-closed containment!`);
        document.getElementById('disp-spec').value = '';
        document.getElementById('disp-criteria').value = '';
        refreshData();
      }
    }

    async function engageEstop() {
      if (!confirm('Engage ESTOP? Resume requires the controlled-window CLI.')) return;

      const res = await fetchAPI('/api/estop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'pause' })
      });
      if (res) refreshData();
    }

    // MIROFISH TOPOLOGY VISUALIZER
    function renderMirofishGraph(data) {
      const svg = document.getElementById('mirofish-svg');
      const nodes = data.nodes || [];
      const links = data.links || [];

      if (!nodes.length) return;

      const width = svg.clientWidth || 600;
      const height = svg.clientHeight || 300;
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

      let html = '';
      // Center node: The Kernel
      const cx = width / 2;
      const cy = height / 2;

      // Draw links
      links.forEach((l, idx) => {
        const angle = (idx / links.length) * 2 * Math.PI;
        const radius = Math.min(width, height) * 0.38;
        const x = cx + radius * Math.cos(angle);
        const y = cy + radius * Math.sin(angle);
        html += `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke="#1e293b" stroke-width="1.5" stroke-dasharray="3,3" />`;
      });

      // Center Kernel Node
      html += `
        <g class="cursor-pointer node-glow">
          <circle cx="${cx}" cy="${cy}" r="24" fill="#0e7490" stroke="#06b6d4" stroke-width="2" />
          <text x="${cx}" y="${cy + 4}" text-anchor="middle" fill="#ffffff" font-size="10" font-family="monospace" font-weight="bold">KERNEL</text>
        </g>
      `;

      // Outer Nodes
      nodes.forEach((n, idx) => {
        const angle = (idx / nodes.length) * 2 * Math.PI;
        const radius = Math.min(width, height) * 0.38;
        const x = cx + radius * Math.cos(angle);
        const y = cy + radius * Math.sin(angle);

        let color = '#10b981';
        let glowClass = 'node-glow';
        if (n.type === 'candidate') { color = '#f59e0b'; glowClass = 'node-glow-amber'; }
        if (n.type === 'denied') { color = '#ef4444'; glowClass = 'node-glow-red'; }

        html += `
          <g class="cursor-pointer ${glowClass}"><title>${escapeHTML(n.id)}: ${escapeHTML(n.type)}</title>
            <circle cx="${x}" cy="${y}" r="12" fill="#0f172a" stroke="${color}" stroke-width="2" />
            <text x="${x}" y="${y + 20}" text-anchor="middle" fill="#94a3b8" font-size="9" font-family="monospace">${escapeHTML(n.label.substring(0, 14))}</text>
          </g>
        `;
      });

      svg.innerHTML = html;
    }

    // Auto-refresh every 6 seconds
    refreshData();
    setInterval(refreshData, 6000);
  </script>
</body>
</html>
"""


class WebConsoleHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler dispatching dashboard and REST APIs."""

    def log_message(self, format: str, *args: object) -> None:
        return  # suppress noisy console request logs

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        super().end_headers()

    def _authenticated(self) -> bool:
        headers = self.headers.get_all("Authorization", [])
        expected = ("Bearer " + self.server.bearer_token).encode("ascii")
        if len(headers) == 1 and hmac.compare_digest(headers[0].encode("utf-8"), expected):
            return True
        # Do not log paths, request bodies, or headers: they may contain secrets.
        LOGGER.warning("Web console authentication rejected from %s (%s)", self.client_address[0], self.command)
        self.close_connection = True
        if self.command == "GET" and urlparse(self.path).path in ("", "/"):
            self._send_html(LOGIN_HTML, status=401)
        else:
            self._send_json({"error": "Bearer authentication required"}, status=401)
        return False

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        if status == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="AGI_like"')
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, content: str, status: int = 200) -> None:
        body = content.encode("utf-8")
        self.send_response(status)
        if status == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="AGI_like"')
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authenticated():
            return
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("", "/"):
            self._send_html(HTML_TEMPLATE.replace("__LOCAL_CSS__", CONSOLE_CSS).replace("__BEARER_TOKEN_JSON__", json.dumps(self.server.bearer_token)))
            return

        gw: Gateway = getattr(self.server, "gateway")

        if path == "/api/status":
            # Calculate ledger statistics
            total_tasks = 0
            total_tokens = 0
            with gw._conn() as c:
                row = c.execute("SELECT COUNT(*), SUM(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)) FROM tasks").fetchone()
                if row:
                    total_tasks = row[0] or 0
                    total_tokens = row[1] or 0
                latest = c.execute("SELECT MAX(task_id) FROM tasks").fetchone()[0]
            att_info = gw.get_attestation(latest)

            self._send_json({
                "estop_engaged": execution_pause.pause_engaged(),
                "attestation_digest": att_info.get("active_policy_digest"),
                "attestation_valid": att_info.get("attestation_token_valid"),
                "attestation_error": att_info.get("attestation_error"),
                "attestation_chain_task_id": latest,
                "attestation_chain_valid": att_info.get("attestation_chain_valid", False),
                "attestation_chain_steps": att_info.get("attestation_chain_steps", 0),
                "attestation_chain_error": att_info.get("attestation_chain_error", "no_task"),
                "worker_identity": att_info.get("worker_identity"),
                "allowed_hosts_count": len(gw.policy_mgr.get_allowed_hosts()),
                "total_tasks": total_tasks,
                "total_tokens": total_tokens,
            })
            return

        if path == "/api/tasks":
            tasks = []
            with gw._conn() as c:
                rows = c.execute(
                    "SELECT task_id, mission_id, spec, status, critic_verdict, "
                    "critic_notes, tokens_in, tokens_out, "
                    "cost_usd, model_used, started_at, finished_at "
                    "FROM tasks ORDER BY task_id DESC LIMIT 50"
                ).fetchall()
                for r in rows:
                    t = dict(r)
                    t["completed_at"] = t.get("finished_at")
                    dur = None
                    if t.get("started_at") and t.get("finished_at"):
                        try:
                            t0 = datetime.fromisoformat(str(t["started_at"]).replace("Z", "+00:00"))
                            t1 = datetime.fromisoformat(str(t["finished_at"]).replace("Z", "+00:00"))
                            dur = round((t1 - t0).total_seconds(), 2)
                        except Exception:
                            dur = None
                    t["duration_seconds"] = dur
                    tasks.append(t)
            self._send_json({"tasks": tasks})
            return

        if path.startswith("/api/tasks/"):
            tid_str = path.split("/api/tasks/", 1)[1]
            try:
                tid = int(tid_str)
                deliv_info = gw.get_deliverable(tid)
                attestation = gw.get_attestation(tid)
                deliv_info.update({k: v for k, v in attestation.items() if k.startswith("attestation_chain_")})
                # Parse broker audit rows if present
                broker_rows = []
                audit_file = gw.runs_dir / f"task{tid}_a1_broker.audit.jsonl"
                if audit_file.is_file():
                    for line in audit_file.read_text(encoding="utf-8").splitlines()[:20]:
                        if line.strip():
                            try:
                                broker_rows.append(json.loads(line))
                            except Exception:
                                pass
                deliv_info["broker_audit"] = broker_rows
                self._send_json(deliv_info)
            except ValueError:
                self._send_json({"error": "invalid task id"}, status=400)
            return

        if path == "/api/candidates":
            proposals = gw.propose_domains(unreviewed_only=True)
            self._send_json({"candidates": proposals})
            return

        if path == "/api/graph":
            # Generate MiroFish topology dataset
            allowed = sorted(list(gw.policy_mgr.get_allowed_hosts()))
            candidates = gw.policy_mgr.summarize_candidates(unreviewed_only=True)

            nodes = []
            links = []

            # 1. Top allowlisted domains (up to 8)
            for h in allowed[:8]:
                nodes.append({"id": h, "label": h, "type": "allowed"})
                links.append({"source": "kernel", "target": h})

            # 2. Candidate domains under review (up to 6)
            for c in candidates[:6]:
                nodes.append({"id": c["host"], "label": c["host"], "type": "candidate", "count": c["count"]})
                links.append({"source": "kernel", "target": c["host"]})

            self._send_json({"nodes": nodes, "links": links})
            return

        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        if not self._authenticated():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) > 1:
                raise ValueError("Unsupported request framing")
            length = int(self.headers.get("Content-Length", 0))
            if not 0 <= length <= MAX_REQUEST_BYTES:
                raise ValueError("Request body must be at most 65536 bytes")
            raw_body = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(raw_body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
        except (ValueError, UnicodeError):
            self.close_connection = True
            self._send_json({"error": "Invalid JSON object or request length"}, status=400)
            return

        gw: Gateway = getattr(self.server, "gateway")

        if path == "/api/dispatch":
            spec = str(payload.get("spec") or "")
            criteria = str(payload.get("pass_criteria") or "")
            try:
                res = gw.dispatch_task(spec=spec, pass_criteria=criteria,
                                       max_budget_usd=payload.get("max_budget_usd", 1.0),
                                       max_tokens=payload.get("max_tokens", 100000))
                self._send_json(res)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if path == "/api/estop":
            action = payload.get("action")
            if action != "pause":
                self._send_json({"error": "Web console supports pause only; use the controlled-window CLI for resume"}, status=403)
                return
            try:
                # Engage only. Never remove or overwrite an existing sentinel.
                estop_file = execution_pause.estop_path()
                estop_file.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with estop_file.open("x", encoding="utf-8") as sentinel:
                        sentinel.write("ESTOP engaged by operator via web console\n")
                except FileExistsError:
                    pass
                if not execution_pause.pause_engaged():
                    raise RuntimeError("ESTOP state could not be confirmed")
            except (OSError, ValueError, RuntimeError):
                self._send_json({"error": "Unable to confirm ESTOP engagement; use operator CLI"}, status=500)
                return
            self._send_json({"success": True, "estop_engaged": execution_pause.pause_engaged()})
            return

        if path == "/api/candidates/approve":
            domain = str(payload.get("domain") or "")
            rationale = str(payload.get("rationale") or "")
            try:
                rec = gw.approve_domain(domain, rationale=rationale)
                self._send_json({"success": True, "record": rec})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if path == "/api/candidates/reject":
            domain = str(payload.get("domain") or "")
            reason = str(payload.get("reason") or "")
            try:
                rec = gw.reject_domain(domain, reason=reason)
                self._send_json({"success": True, "record": rec})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        self._send_json({"error": "not found"}, status=404)


class WebConsoleServer(ThreadingHTTPServer):
    """Threading HTTP server with attached gateway instance."""

    def __init__(self, host: str, port: int, gateway: Gateway, *, allow_network: bool = False):
        if host == "localhost":
            host = "127.0.0.1"
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False  # hostnames are not trusted to keep resolving to loopback
        if not loopback and not allow_network:
            raise ValueError("Non-loopback binding requires explicit --allow-network")
        if not loopback:
            print("WARNING: --allow-network exposes the bearer token and operator controls over plaintext HTTP. Network use is not recommended.", file=sys.stderr, flush=True)
        self.gateway = gateway
        self.bearer_token = secrets.token_urlsafe(32)
        if ":" in host:
            self.address_family = socket.AF_INET6
        super().__init__((host, port), WebConsoleHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AGI_like Unified Executive Web Console")
    parser.add_argument("--host", default="127.0.0.1", help="Binding host")
    parser.add_argument("--port", type=int, default=8080, help="Binding port")
    parser.add_argument("--allow-network", action="store_true", help="Explicitly allow non-loopback exposure (not recommended)")
    args = parser.parse_args(argv)

    gw = Gateway()
    try:
        server = WebConsoleServer(args.host, args.port, gw, allow_network=args.allow_network)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Operator bearer token: {server.bearer_token}", flush=True)
    print(f"[ONLINE] AGI_like Executive Web Console listening on http://{args.host}:{args.port}")
    print("Coupling MiroFish Cognitive Graph + Munder Difflin Swarm + AGI_like Attestation Kernel.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
