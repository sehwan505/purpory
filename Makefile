.PHONY: install install-cli install-app uninstall

install:
	./install.sh all --local

install-cli:
	./install.sh cli --local

install-app:
	./install.sh app --local

uninstall:
	./install.sh all --uninstall
