package main

import (
	"context"
	"embed"
	"errors"
	"fmt"
	"os"

	product "github.com/sehwan505/purpory/internal/app"
	"github.com/sehwan505/purpory/internal/launch"
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
	var service *product.Service
	if config.RootSet {
		service, err = product.Open(ctx, config.Root, config.DBPath, config.ProjectID)
	} else {
		service, err = product.OpenDesktop(ctx, config.DBPath, config.ProjectID)
	}
	if err != nil {
		return err
	}
	defer service.Close()
	return runDesktop(NewApp(service))
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
