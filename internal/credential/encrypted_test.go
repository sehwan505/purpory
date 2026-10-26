package credential

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

type memoryCiphertexts struct {
	values map[string][]byte
}

func (m *memoryCiphertexts) Credential(_ context.Context, account string) ([]byte, bool, error) {
	value, found := m.values[account]
	return value, found, nil
}

func (m *memoryCiphertexts) SaveCredential(_ context.Context, account string, value []byte) error {
	m.values[account] = append([]byte(nil), value...)
	return nil
}

func (m *memoryCiphertexts) DeleteCredential(_ context.Context, account string) error {
	delete(m.values, account)
	return nil
}

func TestEncryptedLifecycle(t *testing.T) {
	ctx := context.Background()
	values := &memoryCiphertexts{values: map[string][]byte{}}
	keyPath := filepath.Join(t.TempDir(), "purpory.key")
	store, err := NewEncrypted(values, keyPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Set(ctx, "provider.openai.api-key", "secret"); err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(values.values["provider.openai.api-key"], []byte("secret")) {
		t.Fatal("ciphertext contains the plaintext credential")
	}
	info, err := os.Stat(keyPath)
	if err != nil || runtime.GOOS != "windows" && info.Mode().Perm()&0o077 != 0 {
		t.Fatalf("master key permissions = %v, %v", info, err)
	}
	reopened, err := NewEncrypted(values, keyPath)
	if err != nil {
		t.Fatal(err)
	}
	value, found, err := reopened.Get(ctx, "provider.openai.api-key")
	if err != nil || !found || value != "secret" {
		t.Fatalf("credential = %q, %v, %v", value, found, err)
	}
	if err := reopened.Delete(ctx, "provider.openai.api-key"); err != nil {
		t.Fatal(err)
	}
	if _, found, err := reopened.Get(ctx, "provider.openai.api-key"); err != nil || found {
		t.Fatalf("deleted credential remains: %v, %v", found, err)
	}
}

func TestEncryptedReportsStoredCredentialWhenMasterKeyIsMissing(t *testing.T) {
	ctx := context.Background()
	values := &memoryCiphertexts{values: map[string][]byte{}}
	keyPath := filepath.Join(t.TempDir(), "purpory.key")
	store, err := NewEncrypted(values, keyPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Set(ctx, "provider.openai.api-key", "secret"); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(keyPath); err != nil {
		t.Fatal(err)
	}
	if _, found, err := store.Get(ctx, "provider.openai.api-key"); err == nil || !found {
		t.Fatalf("missing master key = found %v, error %v", found, err)
	}
}
