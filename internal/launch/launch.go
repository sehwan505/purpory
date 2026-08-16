// Package launch resolves process-wide project and database arguments.
package launch

import (
	"fmt"
	"os"

	"github.com/sehwan505/purpory/internal/store"
)

type Config struct {
	Root      string
	RootSet   bool
	DBPath    string
	ProjectID string
	Args      []string
}

func Parse(arguments []string) (Config, error) {
	root, err := os.Getwd()
	if err != nil {
		return Config{}, fmt.Errorf("resolve working directory: %w", err)
	}
	database, err := store.DefaultPath()
	if err != nil {
		return Config{}, err
	}
	config := Config{Root: root, DBPath: database}
	for index := 0; index < len(arguments); index++ {
		argument := arguments[index]
		if argument != "--root" && argument != "--db" && argument != "--project" {
			config.Args = append(config.Args, argument)
			continue
		}
		if index+1 >= len(arguments) {
			return Config{}, fmt.Errorf("%s requires a value", argument)
		}
		index++
		switch argument {
		case "--root":
			config.Root = arguments[index]
			config.RootSet = true
		case "--db":
			config.DBPath = arguments[index]
		case "--project":
			config.ProjectID = arguments[index]
		}
	}
	return config, nil
}
