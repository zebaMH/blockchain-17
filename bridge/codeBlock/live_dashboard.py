import os
import sys
import json
import base64
import logging
import threading
import random
import hashlib
import subprocess
import requests
import time
from flask import Flask, jsonify, render_template_string, Response
from datetime import datetime

# --- Configuration ---
DEFAULT_COMETBFT_RPC_URL = "http://localhost:26657" 
SERF_EXECUTABLE_PATH = "/usr/bin/serf"
SERF_RPC_ADDR = "172.20.20.7:7373"

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Global State ---
state_lock = threading.Lock()
app_state = {
    "serf_members": [],
    "cometbft_status": "Disconnected",
    "serf_status": "Disconnected",
    "transactions_broadcast": 0,
    "node_workloads": {},
    "recent_transactions": {},
    "last_transaction_flow": None
}

# --- CometBFT Client (with improved error handling) ---
class CometBFTClient:
    def __init__(self, rpc_url):
        self.rpc_url = rpc_url
        self.headers = {'Content-Type': 'application/json'}
        logger.info(f"CometBFTClient initialized for URL: {self.rpc_url}")

    def _make_request(self, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": -1}
        try:
            response = requests.post(self.rpc_url, data=json.dumps(payload), headers=self.headers, timeout=2)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.ConnectionError as e:
            logger.error(f"RPC ConnectionError for '{method}': Is CometBFT running at {self.rpc_url}?")
            return {"error_type": "ConnectionError", "message": "Connection Refused. Check if CometBFT is running."}
        except requests.exceptions.Timeout as e:
            logger.error(f"RPC Timeout for '{method}': {e}")
            return {"error_type": "Timeout", "message": "Request timed out."}
        except requests.RequestException as e:
            logger.error(f"RPC request for '{method}' failed: {e}")
            return {"error_type": "RequestException", "message": str(e)}

    def get_status(self):
        return self._make_request("status")

    def broadcast_tx_sync(self, tx_b64):
        return self._make_request("broadcast_tx_sync", {"tx": tx_b64})
    
    def query_app(self, path):
        response = self._make_request("abci_query", {"path": path})
        if response and "result" in response and "response" in response["result"]:
            if response["result"]["response"].get("value"):
                try:
                    decoded_value = base64.b64decode(response["result"]["response"]["value"])
                    return json.loads(decoded_value)
                except (json.JSONDecodeError, TypeError): return None
        return None

# --- Background Monitor Thread ---
def monitor_thread(comet_client):
    logger.info("Starting background state monitor thread.")
    while True:
        try:
            cmd = [SERF_EXECUTABLE_PATH, "members", "-format=json", f"-rpc-addr={SERF_RPC_ADDR}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            status = comet_client.get_status()
            workloads = comet_client.query_app("/workloads")

            with state_lock:
                if result.returncode == 0:
                    app_state["serf_members"] = json.loads(result.stdout).get("members", [])
                    app_state["serf_status"] = "Connected"
                else:
                    app_state["serf_status"] = "Disconnected"

                if status and "error_type" not in status:
                    app_state["cometbft_status"] = "Connected"
                else:
                    app_state["cometbft_status"] = "Disconnected"

                if workloads:
                    app_state["node_workloads"] = dict(sorted(workloads.items()))
        except Exception as e:
            logger.error(f"Error in monitor thread: {e}")
        time.sleep(3)

# --- Flask Web Application ---
app = Flask(__name__)

@app.route('/')
def index():
    return render_template_string(open("dashboard.html").read())

@app.route('/events')
def events():
    def generate():
        while True:
            with state_lock:
                yield f"data: {json.dumps(app_state)}\n\n"
            time.sleep(1)
    return Response(generate(), mimetype='text/event-stream')

@app.route('/trigger_transaction', methods=['POST'])
def trigger_transaction():
    global cometbft_client
    with state_lock:
        members = [m for m in app_state["serf_members"] if m.get("status") == "alive"]
    
    if len(members) < 2: return jsonify({"status": "error", "message": "Not enough alive nodes."}), 400

    source = random.choice(members)
    destinations = [m for m in members if m["name"] != source["name"]]
    if not destinations: return jsonify({"status": "error", "message": "No destination node available."}), 400
    destination = random.choice(destinations)

    tx_data = {
        "type": "offload_workload", "source_node": source["name"],
        "destination_nodes": [destination["name"]], "workload_id": f"workload-{random.randint(1000, 9999)}",
        "details": "Offloading CPU-intensive task.", "workload_amount": random.randint(5, 20),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    tx_json = json.dumps(tx_data)
    tx_b64 = base64.b64encode(tx_json.encode('utf-8')).decode('utf-8')
    
    response = cometbft_client.broadcast_tx_sync(tx_b64)

    if response and response.get("error_type"):
        error_msg = response["message"]
        logger.error(f"Transaction broadcast failed: {error_msg}")
        return jsonify({"status": "error", "message": error_msg}), 500

    if response and response.get("result") and response["result"].get("code") == 0:
        tx_hash = response["result"]["hash"]
        logger.info(f"Transaction broadcast success: {tx_hash}")
        with state_lock:
            app_state["transactions_broadcast"] += 1
            app_state["last_transaction_flow"] = {
                "source": tx_data["source_node"],
                "destination": tx_data["destination_nodes"][0],
                "timestamp": time.time()
            }
        return jsonify({"status": "success", "hash": tx_hash})
    else:
        # MODIFIED: Extract the detailed log message from the rejection response.
        error_log = "Transaction rejected by node."
        if response and response.get("result") and response["result"].get("log"):
             error_log = response["result"]["log"]
        elif response and response.get("error"):
            error_log = response.get("error", {}).get("data", "Broadcast failed")
        
        logger.error(f"Transaction broadcast failed: {error_log}")
        return jsonify({"status": "error", "message": error_log}), 500

# --- HTML File ---
def create_dashboard_html():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Live Smart Contract Dashboard</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background-color: #111827; color: #d1d5db; font-family: 'Inter', sans-serif; }
            .card { background-color: #1f2937; border-radius: 0.75rem; border: 1px solid #374151; }
            .status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
            .connected { background-color: #22c55e; } .disconnected { background-color: #ef4444; }
            .node-card { transition: all 0.3s ease-in-out; border: 2px solid transparent; }
            .node-alive { border-color: #16a34a; }
            .node-failed { border-color: #dc2626; opacity: 0.6; }
            .highlight-source { box-shadow: 0 0 20px 5px #ef4444; transform: scale(1.05); }
            .highlight-destination { box-shadow: 0 0 20px 5px #22c55e; transform: scale(1.05); }
        </style>
    </head>
    <body class="p-4 md:p-8">
        <div class="max-w-7xl mx-auto">
            <header class="flex flex-col md:flex-row justify-between items-center mb-8 gap-4">
                <h1 class="text-3xl font-bold text-white text-center">✨ Live Smart Contract Dashboard</h1>
                <button id="dispatchBtn" class="bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-2 px-6 rounded-lg transition duration-300 w-full md:w-auto">Dispatch Workload Transaction</button>
            </header>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <!-- Left Column -->
                <div class="space-y-6">
                    <div class="card p-6">
                        <h2 class="text-xl font-semibold text-gray-300 mb-4">System Status</h2>
                        <div id="system-status-container" class="space-y-3"></div>
                    </div>
                    <div class="card p-6">
                        <h2 class="text-xl font-semibold text-gray-300 mb-4">Serf Cluster</h2>
                        <div id="serf-cluster-container" class="grid grid-cols-2 sm:grid-cols-3 gap-4"></div>
                    </div>
                </div>

                <!-- Right Column -->
                <div class="card p-6">
                    <h2 class="text-xl font-semibold text-gray-300 mb-4">Live Node Workloads (kW)</h2>
                    <div id="workloads-container" class="space-y-4"></div>
                </div>
            </div>
        </div>

        <script>
            const systemStatusContainer = document.getElementById('system-status-container');
            const serfClusterContainer = document.getElementById('serf-cluster-container');
            const workloadsContainer = document.getElementById('workloads-container');
            const dispatchBtn = document.getElementById('dispatchBtn');

            function updateSystemStatus(state) {
                systemStatusContainer.innerHTML = `
                    <p class="flex items-center justify-between"><span class="text-gray-400">CometBFT RPC:</span><span class="flex items-center font-semibold"><span class="status-dot mr-2 ${state.cometbft_status === 'Connected' ? 'connected' : 'disconnected'}"></span>${state.cometbft_status}</span></p>
                    <p class="flex items-center justify-between"><span class="text-gray-400">Serf RPC:</span><span class="flex items-center font-semibold"><span class="status-dot mr-2 ${state.serf_status === 'Connected' ? 'connected' : 'disconnected'}"></span>${state.serf_status}</span></p>
                    <p class="flex items-center justify-between"><span class="text-gray-400">Transactions Broadcast:</span><span class="font-semibold text-white">${state.transactions_broadcast}</span></p>
                `;
            }

            function updateSerfCluster(members, lastFlow) {
                serfClusterContainer.innerHTML = '';
                members.forEach(member => {
                    const card = document.createElement('div');
                    card.id = `node-${member.name}`;
                    card.className = `node-card p-3 rounded-lg text-center ${member.status === 'alive' ? 'node-alive' : 'node-failed'}`;
                    card.innerHTML = `<div class="font-bold text-white">${member.name}</div><div class="text-xs text-gray-400">${member.addr}</div>`;
                    
                    if (lastFlow && (Date.now() / 1000 - lastFlow.timestamp < 4)) {
                        if (member.name === lastFlow.source) card.classList.add('highlight-source');
                        if (member.name === lastFlow.destination) card.classList.add('highlight-destination');
                    }
                    serfClusterContainer.appendChild(card);
                });
            }

            function updateWorkloads(workloads) {
                workloadsContainer.innerHTML = '';
                for (const [node, workload] of Object.entries(workloads)) {
                    const p = Math.min((workload / 150) * 100, 100);
                    workloadsContainer.innerHTML += `
                        <div>
                            <div class="flex justify-between items-center mb-1"><span class="text-sm font-medium text-gray-300">${node}</span><span class="text-sm font-bold text-white">${workload} kW</span></div>
                            <div class="bg-gray-700 w-full h-2.5 rounded-full"><div class="bg-blue-600 h-2.5 rounded-full" style="width: ${p}%"></div></div>
                        </div>
                    `;
                }
            }

            const eventSource = new EventSource('/events');
            eventSource.onmessage = (event) => {
                const state = JSON.parse(event.data);
                updateSystemStatus(state);
                updateSerfCluster(state.serf_members, state.last_transaction_flow);
                updateWorkloads(state.node_workloads);
            };

            dispatchBtn.addEventListener('click', async () => {
                dispatchBtn.disabled = true;
                dispatchBtn.textContent = 'Dispatching...';
                try {
                    const response = await fetch('/trigger_transaction', { method: 'POST' });
                    if (!response.ok) {
                        const data = await response.json();
                        alert('Failed: ' + data.message);
                    }
                } catch (error) {
                    alert('Error connecting to server.');
                } finally {
                    setTimeout(() => {
                        dispatchBtn.disabled = false;
                        dispatchBtn.textContent = 'Dispatch Workload Transaction';
                    }, 1000);
                }
            });
        </script>
    </body>
    </html>
    """;
    with open("dashboard.html", "w") as f:
        f.write(html_content)

# --- Main Execution ---
if __name__ == '__main__':
    if len(sys.argv) > 1:
        cometbft_rpc_url = sys.argv[1]
    else:
        cometbft_rpc_url = DEFAULT_COMETBFT_RPC_URL
    
    cometbft_client = CometBFTClient(cometbft_rpc_url)
    create_dashboard_html()

    monitor = threading.Thread(target=monitor_thread, args=(cometbft_client,), daemon=True, name="StateMonitorThread")
    monitor.start()

    logger.info(f"Starting Flask UI on http://0.0.0.0:5000, connecting to CometBFT at {cometbft_rpc_url}")
    app.run(host='0.0.0.0', port=5000, debug=False)
