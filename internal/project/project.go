// Package project identifies the project associated with a working directory.
package project

import (
	"errors"
	"os"
	"strings"
)

var (
	ErrNotRegistered = errors.New("project is not registered")
	ErrAmbiguous     = errors.New("multiple projects match the working directory")
)

type Project struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Root string `json:"root"`
}

func Identify(current Project, explicitID string) Project {
	id := RequestedID(explicitID)
	if id == "" {
		id = current.ID
	}
	current.ID = id
	return current
}

func RequestedID(explicitID string) string {
	if id := strings.TrimSpace(explicitID); id != "" {
		return id
	}
	return strings.TrimSpace(os.Getenv("PURPORY_PROJECT_ID"))
}
