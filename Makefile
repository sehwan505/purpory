.PHONY: install install-cli install-app product-eval uninstall

install:
	./install.sh all --local

install-cli:
	./install.sh cli --local

install-app:
	./install.sh app --local

product-eval:
	go test ./internal/cli -run 'Test(SetupMakesProjectAndAgentReady|ExplorationCLIIsBoundedAndProgressive|ProgressiveRenderersDoNotLoadConnectedContent)' -v

uninstall:
	./install.sh all --uninstall
