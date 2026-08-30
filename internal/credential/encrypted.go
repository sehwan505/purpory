// Package credential encrypts provider secrets before persistence.
package credential

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

const (
	formatVersion = byte(1)
	keySize       = 32
	maximumSecret = 2_048
)

type ciphertextStore interface {
	Credential(context.Context, string) ([]byte, bool, error)
	SaveCredential(context.Context, string, []byte) error
	DeleteCredential(context.Context, string) error
}

type Encrypted struct {
	store   ciphertextStore
	keyPath string
}

func NewEncrypted(store ciphertextStore, keyPath string) (*Encrypted, error) {
	if store == nil || strings.TrimSpace(keyPath) == "" {
		return nil, errors.New("configure credential storage: store and master key path are required")
	}
	return &Encrypted{store: store, keyPath: filepath.Clean(keyPath)}, nil
}

func (e *Encrypted) Get(ctx context.Context, account string) (string, bool, error) {
	account = strings.TrimSpace(account)
	if account == "" {
		return "", false, errors.New("load credential: account is required")
	}
	payload, found, err := e.store.Credential(ctx, account)
	if err != nil || !found {
		return "", found, err
	}
	key, err := readKey(e.keyPath)
	if err != nil {
		return "", true, err
	}
	value, err := decrypt(key, account, payload)
	if err != nil {
		return "", true, err
	}
	return value, true, nil
}

func (e *Encrypted) Set(ctx context.Context, account, value string) error {
	account, value = strings.TrimSpace(account), strings.TrimSpace(value)
	if account == "" || value == "" || len(value) > maximumSecret {
		return errors.New("save credential: account and a value up to 2048 characters are required")
	}
	key, err := loadOrCreateKey(e.keyPath)
	if err != nil {
		return err
	}
	payload, err := encrypt(key, account, value)
	if err != nil {
		return err
	}
	return e.store.SaveCredential(ctx, account, payload)
}

func (e *Encrypted) Delete(ctx context.Context, account string) error {
	return e.store.DeleteCredential(ctx, strings.TrimSpace(account))
}

func encrypt(key []byte, account, value string) ([]byte, error) {
	gcm, err := newGCM(key)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, fmt.Errorf("encrypt credential: generate nonce: %w", err)
	}
	payload := append([]byte{formatVersion}, nonce...)
	return gcm.Seal(payload, nonce, []byte(value), []byte(account)), nil
}

func decrypt(key []byte, account string, payload []byte) (string, error) {
	gcm, err := newGCM(key)
	if err != nil {
		return "", err
	}
	nonceSize := gcm.NonceSize()
	if len(payload) < 1+nonceSize+gcm.Overhead() || payload[0] != formatVersion {
		return "", errors.New("decrypt credential: unsupported or invalid ciphertext")
	}
	value, err := gcm.Open(nil, payload[1:1+nonceSize], payload[1+nonceSize:], []byte(account))
	if err != nil {
		return "", fmt.Errorf("decrypt credential: %w", err)
	}
	return string(value), nil
}

func newGCM(key []byte) (cipher.AEAD, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, fmt.Errorf("configure credential encryption: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, fmt.Errorf("configure credential encryption: %w", err)
	}
	return gcm, nil
}

func loadOrCreateKey(path string) ([]byte, error) {
	key, err := readKey(path)
	if err == nil {
		return key, nil
	}
	if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, fmt.Errorf("create credential master key directory: %w", err)
	}
	key = make([]byte, keySize)
	if _, err := rand.Read(key); err != nil {
		return nil, fmt.Errorf("generate credential master key: %w", err)
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if errors.Is(err, os.ErrExist) {
		return readKey(path)
	}
	if err != nil {
		return nil, fmt.Errorf("create credential master key: %w", err)
	}
	if _, err = file.Write(key); err == nil {
		err = file.Sync()
	}
	if closeErr := file.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		_ = os.Remove(path)
		return nil, fmt.Errorf("write credential master key: %w", err)
	}
	return key, nil
}

func readKey(path string) ([]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("load credential master key: %w", err)
	}
	defer file.Close()
	key, err := io.ReadAll(io.LimitReader(file, keySize+1))
	if err != nil {
		return nil, fmt.Errorf("load credential master key: %w", err)
	}
	if len(key) != keySize {
		return nil, errors.New("load credential master key: expected 32 bytes")
	}
	return key, nil
}
