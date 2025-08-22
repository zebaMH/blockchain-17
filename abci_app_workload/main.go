package main

import (
	"context"
	// "encoding/base64" // This line was removed as it's no longer used.
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"

	abci_server "github.com/cometbft/cometbft/abci/server"
	abci_types "github.com/cometbft/cometbft/abci/types"
)

// --- Transaction Structs ---
type BaseTransaction struct {
	Type string `json:"type"`
}
type TransferTransaction struct {
	Type      string `json:"type"`
	FromNode  string `json:"from_node"`
	ToNode    string `json:"to_node"`
	Amount    string `json:"amount"`
	Timestamp string `json:"timestamp"`
}
type WorkloadTransaction struct {
	Type             string   `json:"type"`
	SourceNode       string   `json:"source_node"`
	DestinationNodes []string `json:"destination_nodes"`
	WorkloadID       string   `json:"workload_id"`
	Details          string   `json:"details"`
	WorkloadAmount   int      `json:"workload_amount"`
	Timestamp        string   `json:"timestamp"`
}

// MyApp now holds the state for node workloads.
type MyApp struct {
	abci_types.BaseApplication
	lastBlockHeight int64
	appHash         []byte
	nodeWorkloads   map[string]int // State to track workload (nodeName -> kw)
}

// NewMyApp creates a new instance of the application.
func NewMyApp() *MyApp {
	return &MyApp{
		lastBlockHeight: 0,
		appHash:         []byte("initial_app_hash"),
		nodeWorkloads:   make(map[string]int),
	}
}

// Info can remain simple.
func (app *MyApp) Info(ctx context.Context, req *abci_types.InfoRequest) (*abci_types.InfoResponse, error) {
	return &abci_types.InfoResponse{
		LastBlockHeight:  app.lastBlockHeight,
		LastBlockAppHash: app.appHash,
	}, nil
}

// InitChain initializes the workload for each node.
func (app *MyApp) InitChain(ctx context.Context, req *abci_types.InitChainRequest) (*abci_types.InitChainResponse, error) {
	log.Println("ABCI InitChain: Initializing application state for workloads.")
	initialNodes := []string{"clab-century-serf1", "clab-century-serf2", "clab-century-serf3", "clab-century-serf4", "clab-century-serf5"}
	for _, node := range initialNodes {
		app.nodeWorkloads[node] = 100 // Start each node with 100kw
	}
	log.Printf("Initialized workloads: %+v\n", app.nodeWorkloads)
	return &abci_types.InitChainResponse{}, nil
}

// Query method to expose the application's state.
func (app *MyApp) Query(ctx context.Context, req *abci_types.QueryRequest) (*abci_types.QueryResponse, error) {
	log.Printf("ABCI Query: Received query for path: %s", req.Path)

	if req.Path == "/workloads" {
		workloadBytes, err := json.Marshal(app.nodeWorkloads)
		if err != nil {
			return &abci_types.QueryResponse{Code: 1, Log: "Failed to marshal workload data"}, nil
		}
		return &abci_types.QueryResponse{
			Code:  0,
			Log:   "Successfully returned workload state.",
			Value: workloadBytes,
		}, nil
	}

	return &abci_types.QueryResponse{Code: 2, Log: "Invalid query path"}, nil
}


// CheckTx with improved logging.
func (app *MyApp) CheckTx(ctx context.Context, req *abci_types.CheckTxRequest) (*abci_types.CheckTxResponse, error) {
	// CometBFT already decodes the base64 string from the RPC request.
	// We should treat req.Tx as the raw JSON bytes directly.
	jsonBytes := req.Tx
	log.Printf("CheckTx received JSON data: %s", string(jsonBytes))

	var baseTx BaseTransaction
	if err := json.Unmarshal(jsonBytes, &baseTx); err != nil {
		return &abci_types.CheckTxResponse{Code: 2, Log: "Failed to unmarshal tx type"}, nil
	}

	switch baseTx.Type {
	case "transfer":
		log.Println("CheckTx: Validating 'transfer' transaction.")
	case "offload_workload":
		var tx WorkloadTransaction
		if err := json.Unmarshal(jsonBytes, &tx); err != nil {
			return &abci_types.CheckTxResponse{Code: 2, Log: "Failed to unmarshal workload tx"}, nil
		}
		
		currentWorkload, ok := app.nodeWorkloads[tx.SourceNode]
		if !ok { return &abci_types.CheckTxResponse{Code: 3, Log: fmt.Sprintf("Source node %s not found", tx.SourceNode)}, nil }
		if currentWorkload < tx.WorkloadAmount { return &abci_types.CheckTxResponse{Code: 4, Log: fmt.Sprintf("Insufficient workload on %s", tx.SourceNode)}, nil }
		if len(tx.DestinationNodes) == 0 { return &abci_types.CheckTxResponse{Code: 6, Log: "Missing destination node"}, nil }
	default:
		return &abci_types.CheckTxResponse{Code: 5, Log: fmt.Sprintf("Unknown transaction type: %s", baseTx.Type)}, nil
	}
	return &abci_types.CheckTxResponse{Code: 0, Log: "Transaction format OK."}, nil
}

// FinalizeBlock handles multiple transaction types and updates state.
func (app *MyApp) FinalizeBlock(ctx context.Context, req *abci_types.FinalizeBlockRequest) (*abci_types.FinalizeBlockResponse, error) {
	txResults := make([]*abci_types.ExecTxResult, len(req.Txs))
	for i, txBytes := range req.Txs {
		jsonBytes := txBytes

		var baseTx BaseTransaction
		if err := json.Unmarshal(jsonBytes, &baseTx); err != nil {
			txResults[i] = &abci_types.ExecTxResult{Code: 2, Log: "Failed to unmarshal tx type"}
			continue
		}

		switch baseTx.Type {
		case "transfer":
			log.Println("FinalizeBlock: Executing 'transfer' transaction.")
			txResults[i] = &abci_types.ExecTxResult{Code: 0, Log: "Transfer executed successfully."}
		case "offload_workload":
			var tx WorkloadTransaction
			if err := json.Unmarshal(jsonBytes, &tx); err != nil {
				txResults[i] = &abci_types.ExecTxResult{Code: 2, Log: "Failed to unmarshal workload tx"}
				continue
			}

			app.nodeWorkloads[tx.SourceNode] -= tx.WorkloadAmount
			destNode := tx.DestinationNodes[0]
			app.nodeWorkloads[destNode] += tx.WorkloadAmount

			logMsg := fmt.Sprintf("Workload %dkw offloaded from %s to %s", tx.WorkloadAmount, tx.SourceNode, destNode)
			log.Println("FinalizeBlock:", logMsg)
			log.Printf("New workloads state: %+v\n", app.nodeWorkloads)
			txResults[i] = &abci_types.ExecTxResult{Code: 0, Log: logMsg}
		default:
			txResults[i] = &abci_types.ExecTxResult{Code: 5, Log: fmt.Sprintf("Unknown transaction type: %s", baseTx.Type)}
		}
	}
	app.lastBlockHeight = req.Height
	app.appHash = []byte(fmt.Sprintf("app_hash_at_height_%d", req.Height))
	return &abci_types.FinalizeBlockResponse{TxResults: txResults, AppHash: app.appHash}, nil
}

// Commit persists the state.
func (app *MyApp) Commit(ctx context.Context, req *abci_types.CommitRequest) (*abci_types.CommitResponse, error) {
	log.Printf("ABCI : Committing state at height %d", app.lastBlockHeight)
	return &abci_types.CommitResponse{}, nil
}

// main function starts the ABCI server.
func main() {
	app := NewMyApp()
	addr := "tcp://0.0.0.0:26659"
	server := abci_server.NewSocketServer(addr, app)
	log.Printf("ABCI server listening on %s", addr)
	if err := server.Start(); err != nil { log.Fatalf("Error starting ABCI server: %v", err) }
	c := make(chan os.Signal, 1)
	signal.Notify(c, os.Interrupt, syscall.SIGTERM)
	<-c
	log.Println("Shutting down ABCI server gracefully...")
	if err := server.Stop(); err != nil { log.Fatalf("Error stopping ABCI server: %v", err) }
	log.Println("ABCI server stopped.")
}
