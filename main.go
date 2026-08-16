package main

import (
	"context"
	"embed"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	product "github.com/sehwan505/purpory/internal/app"
	"github.com/sehwan505/purpory/internal/launch"
	"github.com/sehwan505/purpory/internal/store"
	"github.com/wailsapp/wails/v2"
	"github.com/wailsapp/wails/v2/pkg/options"
	"github.com/wailsapp/wails/v2/pkg/options/assetserver"
)

//go:embed all:frontend/dist
var assets embed.FS

func main() {
	if err := runDesktopApplication(os.Args[1:]); err != nil {
		fmt.Fprintf(os.Stderr, "purpory desktop: %v\n", err)
		os.Exit(1)
	}
}

func runDesktopApplication(arguments []string) error {
	if generatingBindings {
		return runDesktop(NewApp(nil))
	}
	config, err := launch.Parse(arguments)
	if err != nil {
		return err
	}
	if len(config.Args) > 0 {
		return errors.New("desktop accepts only --root, --db, and --project; use the standalone purpory CLI for commands")
	}
	ctx := context.Background()
	config = desktopConfig(ctx, config)
	service, err := product.Open(ctx, config.Root, config.DBPath, config.ProjectID)
	if err != nil {
		return err
	}
	defer service.Close()
	return runDesktop(NewApp(service))
}

func desktopConfig(ctx context.Context, config launch.Config) launch.Config {
	if config.RootSet {
		return config
	}
	database, err := store.Open(ctx, config.DBPath)
	if err == nil {
		defer database.Close()
		projects, loadErr := database.Projects(ctx)
		if loadErr == nil {
			if config.ProjectID != "" {
				for _, value := range projects {
					if value.ID == config.ProjectID && usableProjectRoot(value.Root) {
						config.Root = value.Root
						return config
					}
				}
			}
			for _, value := range projects {
				if samePath(value.Root, config.Root) && usableProjectRoot(value.Root) {
					config.Root = value.Root
					config.ProjectID = value.ID
					return config
				}
			}
			for _, value := range projects {
				if usableProjectRoot(value.Root) {
					config.Root = value.Root
					config.ProjectID = value.ID
					return config
				}
			}
		}
	}
	if usableProjectRoot(config.Root) {
		return config
	}
	home, err := os.UserHomeDir()
	if err == nil {
		config.Root = home
	}
	return config
}

func usableProjectRoot(path string) bool {
	clean := filepath.Clean(path)
	if clean == filepath.VolumeName(clean)+string(os.PathSeparator) {
		return false
	}
	info, err := os.Stat(clean)
	return err == nil && info.IsDir()
}

func samePath(left, right string) bool {
	leftPath, leftErr := filepath.Abs(left)
	rightPath, rightErr := filepath.Abs(right)
	return leftErr == nil && rightErr == nil && leftPath == rightPath
}

func runDesktop(app *App) error {
	return wails.Run(&options.App{
		Title:            "Purpory",
		Width:            1180,
		Height:           760,
		MinWidth:         900,
		MinHeight:        620,
		BackgroundColour: &options.RGBA{R: 245, G: 246, B: 242, A: 1},
		AssetServer:      &assetserver.Options{Assets: assets},
		OnStartup:        app.startup,
		Bind:             []any{app},
	})
}
