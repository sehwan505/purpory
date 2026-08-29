// Package credential stores provider secrets in the operating system keyring.
package credential

import (
	"errors"
	"fmt"
	"strings"

	"github.com/zalando/go-keyring"
)

const service = "Purpory"

type Keyring struct{}

func (Keyring) Get(account string) (string, bool, error) {
	value, err := keyring.Get(service, strings.TrimSpace(account))
	if errors.Is(err, keyring.ErrNotFound) {
		return "", false, nil
	}
	if err != nil {
		return "", false, fmt.Errorf("load credential: %w", err)
	}
	return value, true, nil
}

func (Keyring) Set(account, value string) error {
	account, value = strings.TrimSpace(account), strings.TrimSpace(value)
	if account == "" || value == "" || len(value) > 2_048 {
		return errors.New("save credential: account and a value up to 2048 characters are required")
	}
	if err := keyring.Set(service, account, value); err != nil {
		return fmt.Errorf("save credential: %w", err)
	}
	return nil
}

func (Keyring) Delete(account string) error {
	err := keyring.Delete(service, strings.TrimSpace(account))
	if errors.Is(err, keyring.ErrNotFound) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("delete credential: %w", err)
	}
	return nil
}
