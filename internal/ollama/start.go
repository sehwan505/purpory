package ollama

import (
	"context"
	"errors"
	"fmt"
	"os/exec"
	"time"
)

func (c *Client) Start(ctx context.Context, wait time.Duration) Status {
	if status := c.Status(ctx); status.Available {
		return status
	}
	path, err := exec.LookPath("ollama")
	if err != nil {
		return Status{Error: "start ollama: executable not found"}
	}
	command := exec.Command(path, "serve")
	if err := command.Start(); err != nil {
		return Status{Error: fmt.Sprintf("start ollama: %v", err)}
	}
	_ = command.Process.Release()
	deadline := time.Now().Add(wait)
	for {
		status := c.Status(ctx)
		if status.Available || time.Now().After(deadline) || errors.Is(ctx.Err(), context.Canceled) {
			return status
		}
		timer := time.NewTimer(250 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
			return Status{Error: ctx.Err().Error()}
		case <-timer.C:
		}
	}
}
