package infra

import (
	"context"
	"net"

	e2e "github.com/cometbft/cometbft/v2/test/e2e/pkg"
)

// Provider defines an API for manipulating the infrastructure of a
// specific set of testnet infrastructure.
type Provider interface {
	// Setup generates any necessary configuration for the infrastructure
	// provider during testnet setup.
	Setup() error

	// Starts the nodes passed as parameter. A nodes MUST NOT
	// be started twice before calling StopTestnet
	// If no nodes are passed, start the whole network
	StartNodes(ctx context.Context, nodes ...*e2e.Node) error

	// Stops the whole network
	StopTestnet(ctx context.Context) error

	// Disconnects the node from the network
	Disconnect(ctx context.Context, name string, ip string) error

	// Reconnects the node to the network.
	// This should only be called after Disconnect
	Reconnect(ctx context.Context, name string, ip string) error

	// Returns the provider's infrastructure data
	GetInfrastructureData() *e2e.InfrastructureData

	// Checks whether the node has been upgraded in this run
	CheckUpgraded(ctx context.Context, node *e2e.Node) (string, bool, error)

	// NodeIP returns the IP address of the node that is used to communicate
	// with other nodes in the network (the internal IP address in case of the
	// docker infra type and the external IP address otherwise).
	NodeIP(node *e2e.Node) net.IP
}

type ProviderData struct {
	Testnet            *e2e.Testnet
	InfrastructureData e2e.InfrastructureData
}

// GetInfrastructureData returns the provider's infrastructure data.
func (pd ProviderData) GetInfrastructureData() *e2e.InfrastructureData {
	return &pd.InfrastructureData
}
